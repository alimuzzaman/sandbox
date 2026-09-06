import json
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tests.subprocess_support import run_test_process

from tests.hosting_image_fixtures import local_observation, stage_request, staging_policy


class TestRemoteHostingImages(unittest.TestCase):
    def test_inode_exec_accepts_user_owned_home_chain_and_rejects_writable_ancestor(self):
        from sandbox.transports.remote_hosting_images import _INODE_EXEC
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as directory:
            simulated_home_parent = Path(directory).resolve() / "home" / "alim"
            home = simulated_home_parent / "sandbox"
            revision = "b" * 40
            helper_bytes = b"print('helper')\n"
            digest = hashlib.sha256(helper_bytes).hexdigest()
            root = (home / "runtime" / "helpers" / "image-stage"
                    / f"sha256-{digest}-revision-{revision}")
            root.mkdir(parents=True)
            for path in (home, home / "runtime", home / "runtime" / "helpers",
                         home / "runtime" / "helpers" / "image-stage", root):
                path.chmod(0o700)
            home.chmod(0o775); (home / "runtime").chmod(0o775)
            helper = root / "staging_helper.py"
            helper.write_bytes(helper_bytes); helper.chmod(0o500)
            manifest = {"schema_version": 2, "artifact_digest": f"sha256:{digest}",
                        "entry": "sandbox-image-stage-helper-v2",
                        "runtime_revision": revision,
                        "capability_revision": "systemd-cgroup-v2-batch-stage-v2"}
            manifest_path = root / "manifest-v2.json"
            manifest_path.write_text(json.dumps(manifest)); manifest_path.chmod(0o600)
            verify_only = _INODE_EXEC.rsplit("\ntry: os.execv", 1)[0] + "\nprint('verified')"
            argv = (sys.executable, "-c", verify_only, str(root), str(home),
                    json.dumps(manifest), manifest["entry"], "manifest-v2.json")
            home.chmod(0o755); (home / "runtime").chmod(0o755)
            passed = run_test_process(argv, capture_output=True, text=True, check=False)
            self.assertEqual(passed.returncode, 0, passed.stderr)
            self.assertEqual(passed.stdout, "verified\n")
            manifest_path.write_text("{")
            invalid_json = run_test_process(argv, capture_output=True, text=True, check=False)
            self.assertEqual(json.loads(invalid_json.stdout.removeprefix("BOOTSTRAP "))["code"],
                             "inode_json")
            manifest_path.write_text(json.dumps(manifest))
            manifest_path.unlink()
            missing = run_test_process(argv, capture_output=True, text=True, check=False)
            self.assertEqual(json.loads(missing.stdout.removeprefix("BOOTSTRAP "))["code"],
                             "inode_os")
            manifest_path.write_text(json.dumps(manifest)); manifest_path.chmod(0o600)
            exec_failure_source = _INODE_EXEC.replace(
                "os.execv(sys.executable,", "os.execv('/definitely-missing-sandbox-python',")
            exec_failure = run_test_process(
                (sys.executable, "-c", exec_failure_source, *argv[3:]),
                capture_output=True, text=True, check=False)
            self.assertEqual(json.loads(
                exec_failure.stdout.removeprefix("BOOTSTRAP "))["code"], "inode_exec")
            simulated_home_parent.chmod(0o770)
            refused = run_test_process(argv, capture_output=True, text=True, check=False)
            self.assertEqual(refused.returncode, 0)
            self.assertEqual(json.loads(refused.stdout.removeprefix("BOOTSTRAP ")),
                {"schema_version": 1, "ok": False, "phase": "inode", "code": "inode_key"})

            simulated_home_parent.chmod(0o755)
            wrong_root = root.with_name(f"sha256-{digest}")
            root.rename(wrong_root)
            wrong_argv = (sys.executable, "-c", verify_only, str(wrong_root), str(home),
                          json.dumps(manifest), manifest["entry"], "manifest-v2.json")
            malformed = run_test_process(wrong_argv, capture_output=True, text=True, check=False)
            self.assertEqual(malformed.returncode, 0)
            self.assertEqual(json.loads(malformed.stdout.removeprefix("BOOTSTRAP ")),
                {"schema_version": 1, "ok": False, "phase": "inode", "code": "inode_key"})

    def test_inode_exec_anchors_at_mapped_top_level_when_root_open_is_denied(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from sandbox.transports.remote_hosting_images import _INODE_EXEC
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent) as directory:
            home = Path(directory).resolve() / "home" / "alim" / "sandbox"
            revision = "b" * 40
            helper_bytes = b"print('helper')\n"
            digest = hashlib.sha256(helper_bytes).hexdigest()
            root = (home / "runtime" / "helpers" / "image-stage"
                    / f"sha256-{digest}-revision-{revision}")
            root.mkdir(parents=True)
            for path in (home, home / "runtime", home / "runtime" / "helpers",
                         home / "runtime" / "helpers" / "image-stage", root):
                path.chmod(0o700)
            helper = root / "staging_helper.py"
            helper.write_bytes(helper_bytes); helper.chmod(0o500)
            manifest = {"schema_version": 2, "artifact_digest": f"sha256:{digest}",
                        "entry": "sandbox-image-stage-helper-v2",
                        "runtime_revision": revision,
                        "capability_revision": "systemd-cgroup-v2-batch-stage-v2"}
            manifest_path = root / "manifest-v2.json"
            manifest_path.write_text(json.dumps(manifest)); manifest_path.chmod(0o600)
            real_open, real_fstat = os.open, os.fstat
            top_fd = None; opened = []
            def guarded_open(path, flags, *args, **kwargs):
                nonlocal top_fd
                opened.append(path)
                if path == "/":
                    raise PermissionError("protected root")
                descriptor = real_open(path, flags, *args, **kwargs)
                if isinstance(path, str) and path == "/" + root.parts[1]:
                    top_fd = descriptor
                return descriptor
            def mapped_fstat(descriptor):
                nonlocal top_fd
                value = real_fstat(descriptor)
                if descriptor != top_fd:
                    return value
                top_fd = None
                return SimpleNamespace(st_mode=value.st_mode, st_uid=65534)
            class Executed(Exception):
                pass
            old_argv = sys.argv
            sys.argv = ["inode-exec", str(root), str(home), json.dumps(manifest),
                        manifest["entry"], "manifest-v2.json"]
            try:
                with patch.object(os, "open", side_effect=guarded_open), \
                     patch.object(os, "fstat", side_effect=mapped_fstat), \
                     patch.object(os, "execv", side_effect=Executed), \
                     self.assertRaises(Executed):
                    exec(compile(_INODE_EXEC, "<inode-exec>", "exec"), {})
            finally:
                sys.argv = old_argv
            self.assertNotIn("/", opened)
            self.assertEqual(opened[0], "/" + root.parts[1])

    def test_both_stage_schemas_require_lowercase_exact_runtime_revision(self):
        from sandbox.transports.remote_hosting_images import (
            RegisteredRemoteImageTransport, RemoteImageStageError,
        )
        transport = RegisteredRemoteImageTransport(
            remote_lookup=lambda _name: {"provisioned": True},
            ssh_private_frame=lambda *_args, **_kwargs: None,
            unit_observer=lambda *_args, **_kwargs: None,
            resolve_home=lambda _remote: "/home/alim/sandbox")
        for schema, entry, capability in (
                (1, "sandbox-image-stage-helper-v1", "systemd-cgroup-v2-stage-v1"),
                (2, "sandbox-image-stage-helper-v2", "systemd-cgroup-v2-batch-stage-v2")):
            frame = {"schema_version": schema,
                     "unit_name": "sandbox-image-stage-" + "a" * 32 + ".service",
                     "helper": {"artifact_digest": "sha256:" + "b" * 64,
                                "entry": entry, "runtime_revision": "A" * 40,
                                "capability_revision": capability}}
            with self.subTest(schema=schema), self.assertRaises(RemoteImageStageError) as caught:
                transport.prepare("remote-a", frame, timeout_seconds=30)
            self.assertEqual(caught.exception.code, "protocol_invalid")

    def test_v2_authority_observer_is_closed_and_exact(self):
        from types import SimpleNamespace
        from sandbox.hosting.images.staging_models import HelperIdentity
        from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport
        helper = HelperIdentity("sha256:" + "a" * 64,
            "sandbox-image-stage-helper-v2", "b" * 40,
            "systemd-cgroup-v2-batch-stage-v2")
        manifest = json.dumps({"schema_version": 2, **helper.as_mapping()})
        def observe(_remote, command, timeout):
            if command.startswith("sha256sum"):
                return SimpleNamespace(returncode=0, stdout="a" * 64 + "  helper\n")
            if command.startswith("test -f"):
                return SimpleNamespace(returncode=0, stdout=manifest)
            return SimpleNamespace(returncode=0, stdout="daemon-a\n")
        transport = RegisteredRemoteImageTransport(
            remote_lookup=lambda _name: {"provisioned": True},
            ssh_private_frame=lambda *a, **k: None, unit_observer=observe,
            resolve_home=lambda _remote: "/srv/sandbox")
        self.assertEqual(transport.observe_authority("remote-a", helper),
            {"daemon_identity": "daemon-a", "helper": helper.as_mapping()})

    def test_helper_check_uses_measured_user_unit_only_and_leaves_no_unit(self):
        from types import SimpleNamespace
        from sandbox.hosting.images.staging_models import HelperIdentity
        from sandbox.transports.remote_hosting_images import RegisteredRemoteImageTransport
        helper = HelperIdentity("sha256:" + "a" * 64,
            "sandbox-image-stage-helper-v2", "b" * 40,
            "systemd-cgroup-v2-batch-stage-v2")
        manifest = json.dumps({"schema_version": 2, **helper.as_mapping()})
        commands = []
        class RecordingStdin(io.BytesIO):
            def close(self):
                self.closed_by_transport = True
        recording_stdin = RecordingStdin()
        class Process:
            stdin = recording_stdin; returncode = 74
            def read_ready(self, _timeout): return b"READY\n"
            def communicate(self, timeout):
                return b"CHECKED\n", b""
            def kill(self): self.killed = True
        process = Process()
        class Sender:
            def prepare(self, _remote, argv, **_kwargs):
                process.argv = argv; return process
        cleanup_shows = []
        def observe(_remote, command, timeout):
            commands.append(command)
            if command.startswith("sha256sum"):
                return SimpleNamespace(returncode=0, stdout="a" * 64 + "  helper\n")
            if command.startswith("test -f"):
                return SimpleNamespace(returncode=0, stdout=manifest)
            if command == "id -u":
                return SimpleNamespace(returncode=0, stdout="1000\n")
            if "--property=KillMode" in command:
                unit = next(item.split("=", 1)[1] for item in process.argv
                            if item.startswith("--unit="))
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                cgroup = (f"/user.slice/user-1000.slice/user@1000.service/app.slice/{unit}")
                return SimpleNamespace(returncode=0, stdout=(
                    f"ActiveState=active\nDescription={description}\nControlGroup={cgroup}\n"
                    "KillMode=control-group\nDelegate=no\nNoNewPrivileges=yes\n"
                    "RestrictSUIDSGID=yes\nProtectControlGroups=yes\n"))
            if command.startswith("systemctl --user show"):
                unit = next(item.split("=", 1)[1] for item in process.argv
                            if item.startswith("--unit="))
                description = next(item.split("=", 1)[1] for item in process.argv
                                   if item.startswith("--description="))
                cleanup_shows.append(command)
                if len(cleanup_shows) == 1:
                    return SimpleNamespace(returncode=0, stdout=(
                        "LoadState=loaded\nActiveState=failed\nSubState=failed\n"
                        f"Description={description}\nMainPID=0\nControlGroup=\n"
                        "Result=exit-code\nExecMainStatus=74\n"))
                return SimpleNamespace(returncode=0, stdout=(
                    "LoadState=not-found\nActiveState=inactive\nSubState=dead\n"
                    f"Description={unit}\nMainPID=0\nControlGroup=\n"
                    "Result=success\nExecMainStatus=0\n"))
            return SimpleNamespace(returncode=0, stdout="")
        transport = RegisteredRemoteImageTransport(
            remote_lookup=lambda _name: {"provisioned": True}, ssh_private_frame=Sender(),
            unit_observer=observe, resolve_home=lambda _remote: "/home/alim/sandbox")
        result = transport.helper_check("remote-a", helper)
        self.assertTrue(result["ok"]); self.assertEqual(result["cleanup"], {"complete": True})
        self.assertEqual(recording_stdin.getvalue(), b"CHECK\n")
        self.assertTrue(recording_stdin.closed_by_transport)
        rendered = " ".join(process.argv).lower()
        for forbidden in ("credential", "docker", "registry", "repository_qualified_digest"):
            self.assertNotIn(forbidden, rendered)
        self.assertTrue(any("reset-failed" in command for command in commands))
        self.assertFalse(any(" kill " in command or " stop " in command for command in commands))

    def test_result_parser_is_closed_and_bounded(self):
        from sandbox.transports.remote_hosting_images import RemoteImageStageError, parse_stage_response
        value = {"schema_version": 1, "ok": True, "code": "staged", "payload": {}}
        self.assertTrue(parse_stage_response(value).ok)
        with self.assertRaises(RemoteImageStageError):
            parse_stage_response({**value, "helper_output": "unsafe"})

    def test_bootstrap_parser_accepts_only_ready_or_closed_bounded_failures(self):
        from sandbox.transports.remote_hosting_images import (
            MAX_BOOTSTRAP_FRAME_BYTES, RemoteImageStageError, parse_bootstrap_line,
        )
        self.assertIsNone(parse_bootstrap_line(b"READY\n"))
        for phase, code in (("inode", "inode_os"), ("plan", "plan_invalid"),
                            ("cgroup", "cgroup_invalid"),
                            ("workspace", "workspace_invalid")):
            failure = ("BOOTSTRAP " + json.dumps({"schema_version": 1, "ok": False,
                "phase": phase, "code": code}, separators=(",", ":")) + "\n").encode()
            self.assertEqual(parse_bootstrap_line(failure),
                {"schema_version": 1, "ok": False, "phase": phase, "code": code})
        for value in (b"READY", b"READY\nextra", b"TRACE /secret\n",
                      b'BOOTSTRAP {"schema_version":1,"ok":false,'
                      b'"phase":"inode","code":"other"}\n',
                      b"x" * MAX_BOOTSTRAP_FRAME_BYTES + b"\n"):
            with self.subTest(value=value[:24]), self.assertRaises(
                    RemoteImageStageError) as caught:
                parse_bootstrap_line(value)
            self.assertEqual((caught.exception.bootstrap_phase,
                              caught.exception.bootstrap_code),
                             ("unknown", "bootstrap_unavailable"))

    def test_fixed_transport_has_no_remote_job_or_caller_command_surface(self):
        from pathlib import Path
        source = (Path(__file__).parent.parent / "sandbox/transports/remote_hosting_images.py").read_text()
        self.assertNotIn("RemoteJobTransport", source)
        self.assertIn("systemd-run", source); self.assertIn("KillMode=control-group", source)
        self.assertIn("Delegate=no", source); self.assertIn("ProtectControlGroups=yes", source)
        self.assertIn('(\"systemd-run\", \"--user\"', source)
        self.assertNotIn("--property=RemainAfterExit", source)
        self.assertNotIn("info.st_uid!=0", source)
        self.assertIn("owner_uid=os.geteuid()", source)
        for system_command in ("kill", "stop", "show", "reset-failed"):
            self.assertNotIn(f'"systemctl {system_command}', source)
        self.assertGreaterEqual(source.count('"systemctl --user '), 9)

    def test_helper_derives_user_runtime_workspace_without_environment_input(self):
        source = (Path(__file__).parent.parent
                  / "sandbox/hosting/images/staging_helper.py").read_text()
        function = source[source.index("def _verify_workspace_parent"):
                          source.index("\ndef _run", source.index("def _verify_workspace_parent"))]
        self.assertIn('Path("/run/user") / str(required_uid)', function)
        self.assertIn("os.geteuid()", function)
        self.assertIn("os.O_NOFOLLOW", function)
        self.assertNotIn("getenv", function)

    def test_helper_pull_is_exact_and_has_no_tag_build_or_compose_path(self):
        from pathlib import Path
        source = (Path(__file__).parent.parent / "sandbox/hosting/images/staging_helper.py").read_text()
        self.assertIn('(\"docker\", \"pull\", plan[\"repository_qualified_digest\"])', source)
        self.assertIn("org.sandbox.application-topology.v1", source)
        self.assertIn("org.sandbox.application-topology.v1", source)
        for forbidden in ('\"latest\"', '\"docker\", \"build\"', "compose", "prune"):
            self.assertNotIn(forbidden, source)

    def test_observation_and_proof_drift_matrix_is_fail_closed(self):
        from sandbox.hosting.images.staging_models import (
            LocalImageObservation, StagedImageProof, StagingContractError, staging_digest,
        )
        policy = staging_policy(); request = stage_request(policy=policy)
        baseline = local_observation(policy)
        def rebuilt(**changes):
            body = baseline.body_mapping(); body.update(changes)
            if "observed_topology" in changes:
                body["topology_digest"] = staging_digest(
                    "sandbox.hosting.images.topology.v1", body["observed_topology"])
            if "config_digest" in changes and "local_image_id" not in changes:
                body["local_image_id"] = body["config_digest"]
            constructor = dict(body); constructor["target"] = baseline.target
            identity = dict(body)
            return LocalImageObservation(observation_id=staging_digest(
                "sandbox.hosting.images.local-observation.v1", identity), **constructor)
        for changes in (
            {"target_epoch_end": "other-machine"}, {"daemon_epoch_end": "other-daemon"},
            {"local_image_id": "sha256:" + "4" * 64},
        ):
            with self.subTest(changes=changes), self.assertRaises(StagingContractError):
                rebuilt(**changes)
        for changes in (
            {"repository": "other/repository"},
            {"repo_digest": "ghcr.io/other@sha256:" + "1" * 64},
            {"config_digest": "sha256:" + "3" * 64},
            {"platform": {"os": "linux", "architecture": "arm64"}},
            {"observed_topology": {"persistent_services": ["web"], "one_shot_services": []}},
        ):
            with self.subTest(changes=changes), self.assertRaises(StagingContractError):
                StagedImageProof.create(request, policy, rebuilt(**changes), 1)

    def test_helper_and_transport_measure_exact_installed_artifact_before_broker_ready(self):
        from pathlib import Path
        root = Path(__file__).parent.parent
        transport = (root / "sandbox/transports/remote_hosting_images.py").read_text()
        installer = (root / "scripts/install-remote.sh").read_text()
        self.assertIn("sha256sum -- ", transport)
        self.assertIn("manifest.json", transport)
        self.assertIn("/proc/self/fd/", transport)
        self.assertIn("READY_TIMEOUT_SECONDS", transport)
        self.assertNotIn('"--collect"', transport)
        provisioner = (root / "scripts/provision_image_stage_helper.py").read_text()
        self.assertIn("provision_image_stage_helper.py", installer)
        self.assertIn("installed image staging helper digest mismatch", provisioner)
        self.assertIn('"sandbox-image-stage-helper-v2"', provisioner)
        self.assertIn('"systemd-cgroup-v2-batch-stage-v2"', provisioner)
        self.assertIn("staging helper manifest mismatch", provisioner)


if __name__ == "__main__": unittest.main()
