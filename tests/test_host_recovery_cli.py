import unittest
import json
import shlex
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sandbox.hosting.recovery.models import RecoveryAction, RecoveryRequest, TargetIdentity
from sandbox.hosting.recovery.models import canonical_digest
from sandbox.commands import hosting as hosting_command
from sandbox.hosting.recovery.service import RecoveryAuthorityError
from tests.subprocess_support import run_test_process


class HostRecoveryCliContractTests(unittest.TestCase):
    def test_oversized_edge_or_operation_mints_no_recovery_authority(self):
        context = {
            "job_id": "a" * 32, "request_id": "apply-1",
            "project_identity": "project-id",
            "project_root_digest": "sha256:" + "1" * 64,
            "source_identity": "source-id", "source_commit": "b" * 40,
            "source_dirty_digest": None,
        }
        base_intent = {
            "records": [],
            "routes": [{"hostname": "example.test", "mode": "proxy"}],
            "certificate_hostnames": ["example.test"],
            "proxied": False, "healthcheck_path": "/health",
            "basic_auth": {"enabled": False, "username": None},
        }
        oversized_edge = {**base_intent,
            "routes": [{"hostname": f"r{index}.example.test", "mode": "proxy"}
                       for index in range(65)],
            "certificate_hostnames": [f"r{index}.example.test" for index in range(65)],
        }
        large_validated = {
            "project": "project", "environment": "development",
            "compose": {"files": ["compose.yml"], "service": "web",
                        "background_services": [],
                        "init_services": ["i" + str(index) + "x" * 100
                                          for index in range(2000)]},
            "deploy": {}, "routes": base_intent["routes"],
            "healthcheck": {"path": "/health"}, "cloudflare": {},
            "basic_auth": None, "secrets": {},
        }
        for edge_intent, validated in (
                (oversized_edge, {**large_validated, "compose": {
                    **large_validated["compose"], "init_services": []}}),
                (base_intent, large_validated)):
            state = {"version": 1, "hosts": {}}
            saved = []
            with self.subTest(routes=len(edge_intent["routes"])), \
                 patch.object(hosting_command, "_durable_host_context",
                              return_value=context), \
                 patch.object(hosting_command.personal_secrets,
                              "prospective_hosting_binding_reference",
                              return_value={
                                  "metadata_id": "sha256:" + "f" * 64,
                                  "key_version": "v1", "revision": 1}), \
                 patch.object(hosting_command.personal_secrets,
                              "write_hosting_binding_metadata",
                              side_effect=AssertionError("authority metadata minted")):
                result = hosting_command._accept_hosting_operation(
                    state, "remote/project/development", validated=validated,
                    entry={"ssh": "alim@example.test"}, remote_name="remote",
                    home="/srv", source_state_identity="source-id",
                    source_clean=True, source_commit="b" * 40,
                    source_branch="main", config_digest="sha256:" + "2" * 64,
                    secret_values={}, save_state=lambda value: saved.append(value),
                    binding_key=b"k" * 32, key_version="v1",
                    machine_identity="machine-1", edge_intent=edge_intent,
                    broker_locked=True)
            self.assertIsNone(result)
            self.assertEqual(saved, [])
            self.assertNotIn("hosting_operation",
                             state["hosts"].get("remote/project/development", {}))

    def test_oversized_operation_creates_no_binding_key_or_authority_directory(self):
        context = {
            "job_id": "a" * 32, "request_id": "apply-1",
            "project_identity": "project-id",
            "project_root_digest": "sha256:" + "1" * 64,
            "source_identity": "source-id", "source_commit": "b" * 40,
            "source_dirty_digest": None,
        }
        validated = {
            "project": "project", "environment": "development",
            "compose": {"files": ["compose.yml"], "service": "web",
                        "background_services": [],
                        "init_services": ["init-" + str(index) + "x" * 100
                                          for index in range(2000)]},
            "deploy": {}, "routes": [{"hostname": "example.test", "mode": "proxy"}],
            "healthcheck": {"path": "/health"}, "cloudflare": {},
            "basic_auth": None, "secrets": {},
        }
        edge_intent = {
            "records": [],
            "routes": [{"hostname": "example.test", "mode": "proxy"}],
            "certificate_hostnames": ["example.test"],
            "proxied": False, "healthcheck_path": "/health",
            "basic_auth": {"enabled": False, "username": None},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            secret = root / "secrets"
            secret.write_text("# synthetic\n")
            secret.chmod(0o600)
            state = {"version": 1, "hosts": {}}
            with patch.object(hosting_command, "_durable_host_context",
                              return_value=context), \
                 patch("sandbox.core._paths.RUNTIME_DIR", runtime), \
                 patch.object(hosting_command.personal_secrets, "secret_file",
                              return_value=secret):
                result = hosting_command._accept_hosting_operation(
                    state, "remote/project/development", validated=validated,
                    entry={"ssh": "alim@example.test"}, remote_name="remote",
                    home="/srv", source_state_identity="source-id",
                    source_clean=True, source_commit="b" * 40,
                    source_branch="main", config_digest="sha256:" + "2" * 64,
                    secret_values={}, save_state=lambda _value: self.fail("state written"),
                    machine_identity="machine-1", edge_intent=edge_intent,
                    broker_locked=True)
            self.assertIsNone(result)
            self.assertFalse(runtime.exists())
            self.assertEqual(state, {"version": 1, "hosts": {}})

    def test_registered_host_identity_binds_real_endpoints_without_token(self):
        entry = {
            "ssh": "ssh://alim@example.test:2222",
            "control_transport": "https",
            "control_url": "https://control.example.test/",
            "tailscale_host": "Node.Tailnet.TS.NET",
            "mcp_port": 9174,
            "bearer_token": "first-secret",
        }
        original = hosting_command._registered_host_identity(
            entry, "remote", "/srv/sandbox/")
        self.assertEqual(original, hosting_command._registered_host_identity(
            {**entry, "bearer_token": "different-secret"},
            "remote", "/srv/sandbox"))
        self.assertNotEqual(original, hosting_command._registered_host_identity(
            {**entry, "ssh": "ssh://alim@other.example.test:2222"},
            "remote", "/srv/sandbox"))
        self.assertNotEqual(original, hosting_command._registered_host_identity(
            {**entry, "control_url": "https://other.example.test"},
            "remote", "/srv/sandbox"))

    def test_concurrent_set_origin_is_serialized_and_old_edge_intent_refuses(self):
        old = {"ssh": "alim@old.example.test", "provisioned": True,
               "control_url": "https://control.example.test",
               "origin_ipv4": "192.0.2.1"}
        shared = {"remote": dict(old)}
        started = threading.Event()
        completed = threading.Event()

        def read_block():
            return {name: dict(value) for name, value in shared.items()}

        def write_block(value):
            shared.clear()
            shared.update({name: dict(item) for name, item in value.items()})

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(hosting_command.remote, "RUNTIME_DIR", Path(temporary)), \
             patch.object(hosting_command.remote, "_remote_block",
                          side_effect=read_block), \
             patch.object(hosting_command.remote, "_write_remote_block",
                          side_effect=write_block):
            def repoint():
                started.set()
                hosting_command.remote.put_remote(
                    "remote", origin_ipv4="192.0.2.2")
                completed.set()

            with hosting_command.remote.registered_remote_lock():
                worker = threading.Thread(target=repoint)
                worker.start()
                self.assertTrue(started.wait(1))
                time.sleep(0.03)
                self.assertFalse(completed.is_set())
            worker.join(1)
            self.assertTrue(completed.is_set())

            validated = {
                "project": "project", "environment": "development",
                "routes": [{"hostname": "example.test", "mode": "proxy"}],
                "cloudflare": {"proxied": False},
                "healthcheck": {"path": "/health"}, "basic_auth": None,
            }
            with patch.object(hosting_command, "_cloudflare_drift",
                              return_value={"configured": False}) as drift:
                guarded_plan = hosting_command._guarded_host_apply_plan(
                    validated, hosting_command.remote.get_remote("remote"), "remote",
                    allow_zone_ssl_change=False)
            self.assertEqual(
                {item["address"] for item in guarded_plan["records"]},
                {"192.0.2.2"})
            self.assertEqual(
                {item["address"] for item in drift.call_args.args[0]["records"]},
                {"192.0.2.2"})
            edge_intent = hosting_command._desired_edge_intent(validated, old)
            operation = {"evidence": {
                "host_identity": hosting_command._registered_host_identity(
                    old, "remote", "/srv/sandbox"),
                "machine_identity": "machine-1",
                "edge_intent": edge_intent,
                "edge_intent_digest": canonical_digest(edge_intent),
            }}
            with patch.object(hosting_command.remote, "resolve_sandbox_home",
                              return_value="/srv/sandbox"), \
                 patch.object(hosting_command, "_authenticated_machine_identity",
                              return_value="machine-1"), \
                 self.assertRaises(RecoveryAuthorityError):
                with hosting_command._registered_recovery_authority(
                        validated, "remote", operation, {}):
                    self.fail("changed origin reached recovery")

    def test_registration_lock_rejects_unsafe_directory_and_file_shapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir(mode=0o700)
            (root / "remote-registration").symlink_to(target, target_is_directory=True)
            with patch.object(hosting_command.remote, "RUNTIME_DIR", root), \
                 self.assertRaisesRegex(ValueError, "directory is unsafe"):
                with hosting_command.remote.registered_remote_lock():
                    self.fail("directory symlink acquired")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "remote-registration"
            directory.mkdir(mode=0o755)
            with patch.object(hosting_command.remote, "RUNTIME_DIR", root), \
                 self.assertRaisesRegex(ValueError, "directory is unsafe"):
                with hosting_command.remote.registered_remote_lock():
                    self.fail("unsafe directory acquired")

        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                directory = root / "remote-registration"
                directory.mkdir(mode=0o700)
                source = root / "source"
                source.write_text("")
                source.chmod(0o600)
                lock = directory / "registry.lock"
                if kind == "symlink":
                    lock.symlink_to(source)
                else:
                    lock.hardlink_to(source)
                with patch.object(hosting_command.remote, "RUNTIME_DIR", root), \
                     self.assertRaisesRegex(ValueError, "file is unsafe"):
                    with hosting_command.remote.registered_remote_lock():
                        self.fail(f"{kind} lock acquired")

    def test_recover_predispatch_reaches_refusal_without_compatibility_writers(self):
        import sandbox.cli as cli

        argv = [
            "sb", "host", "recover", "--project-dir", "/project",
            "--environment", "development", "--remote", "remote",
            "--original-request-id", "apply-1", "--request-id", "recover-1",
            "--expected-generation", "0", "--json",
        ]
        output = StringIO()
        validated = {"project": "project", "environment": "development"}
        forbidden = AssertionError("compatibility writer reached recovery")
        with patch.object(sys, "argv", argv), redirect_stdout(output), \
             patch.object(cli, "load_config", return_value={}), \
             patch.object(cli, "resolve_instances", return_value={}), \
             patch.object(cli, "_cwd_instance", return_value=None), \
             patch.object(cli, "_core", return_value=SimpleNamespace(
                 registry_all=lambda: {})), \
             patch("sandbox.commands.migrate.maybe_auto_migrate",
                   side_effect=forbidden), \
             patch("sandbox.commands.migrate.finalize_auto_migration",
                   side_effect=forbidden), \
             patch.object(cli, "write_compose_files", side_effect=forbidden), \
             patch.object(cli, "write_env_for_compose", side_effect=forbidden), \
             patch.object(hosting_command.hosting, "validate_manifest",
                          return_value=validated), \
             patch.object(hosting_command.remote, "get_remote",
                          return_value={"ssh": "alim@example.test"}), \
             patch.object(hosting_command.hosting, "load_host_state",
                          return_value={"version": 1, "hosts": {}}), \
             self.assertRaises(SystemExit):
            cli.main()
        self.assertEqual(json.loads(output.getvalue())["result_class"],
                         "binding_mismatch")

    def test_public_edge_governance_is_unavailable_until_feature_047_projection(self):
        self.assertFalse(hosting_command._recovery_governance_authorized(
            {"project": "project", "environment": "development"}, "remote"))

    def test_recovery_source_check_rechecks_allowed_local_branch(self):
        validated = {
            "project_root": "/project",
            "deploy": {"allowed_branches": ["main"]},
        }
        operation = {"source": {"clean": True, "commit": "a" * 40}}
        with patch.object(hosting_command.subprocess, "run",
                          return_value=SimpleNamespace(stdout="other\n")) as run:
            self.assertFalse(hosting_command._recovery_source_check(validated, operation))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
        self.assertNotIn("SYNTHETIC_PARENT_SECRET", run.call_args.kwargs["env"])

    def test_recovery_source_check_requires_operation_recorded_branch(self):
        validated = {
            "project_root": "/project",
            "deploy": {"allowed_branches": ["main", "release"]},
        }
        operation = {"source": {"clean": True, "commit": "a" * 40},
                     "evidence": {"source_branch": "main"}}
        with patch.object(hosting_command.subprocess, "run",
                          return_value=SimpleNamespace(stdout="release\n")) as run:
            self.assertFalse(hosting_command._recovery_source_check(validated, operation))
        self.assertEqual(run.call_count, 1)

    def test_recovery_source_git_probes_all_disable_optional_locks(self):
        validated = {"project_root": "/project"}
        operation = {"source": {"clean": True, "commit": "a" * 40},
                     "evidence": {"source_branch": "main"}}
        outputs = [SimpleNamespace(stdout="main\n"),
                   SimpleNamespace(stdout="a" * 40 + "\n"),
                   SimpleNamespace(stdout="")]
        with patch.object(hosting_command.subprocess, "run", side_effect=outputs) as run:
            self.assertTrue(hosting_command._recovery_source_check(validated, operation))
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            environment = call.kwargs["env"]
            self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(set(environment), {
                "PATH", "LANG", "LC_ALL", "GIT_OPTIONAL_LOCKS", "GIT_CONFIG_NOSYSTEM"})
            argv = call.args[0]
            self.assertEqual(argv[1:5], [
                "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"])
        self.assertNotIn("--ignore-submodules=all", run.call_args_list[-1].args[0])

    def test_fsmonitor_is_disabled_and_dirty_submodule_refuses_locally_and_remotely(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "parent"
            sub_source = base / "sub-source"
            marker = base / "fsmonitor-entered"
            monitor = base / "hostile-fsmonitor"
            monitor.write_text(f"#!/bin/sh\ntouch \"{marker}\"\nexit 0\n")
            monitor.chmod(0o700)
            environment = {
                "GIT_AUTHOR_NAME": "Synthetic",
                "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
                "GIT_COMMITTER_NAME": "Synthetic",
                "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
            }
            run_test_process(["git", "init", "-b", "main", str(sub_source)], env=environment,
                             capture_output=True, text=True)
            tracked = sub_source / "tracked.txt"
            tracked.write_text("clean\n")
            run_test_process(["git", "-C", str(sub_source), "add", "tracked.txt"],
                             env=environment, capture_output=True, text=True)
            run_test_process(["git", "-C", str(sub_source), "commit", "-m", "sub-base"],
                             env=environment, capture_output=True, text=True)
            run_test_process(["git", "init", "-b", "main", str(root)], env=environment,
                             capture_output=True, text=True)
            run_test_process([
                "git", "-c", "protocol.file.allow=always", "-C", str(root),
                "submodule", "add", str(sub_source), "dependency"],
                env=environment, capture_output=True, text=True)
            run_test_process(["git", "-C", str(root), "commit", "-am", "parent-base"],
                             env=environment, capture_output=True, text=True)
            run_test_process([
                "git", "-C", str(root), "config", "core.fsmonitor",
                str(monitor)], env=environment, capture_output=True, text=True)
            commit = run_test_process(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                env=environment, capture_output=True, text=True).stdout.strip()
            (root / "dependency" / "tracked.txt").write_text("dirty\n")
            operation = {"source": {"clean": True, "commit": commit},
                         "evidence": {"source_branch": "main"}}
            self.assertFalse(hosting_command._recovery_source_check(
                {"project_root": str(root)}, operation))
            self.assertFalse(marker.exists())
            command = hosting_command._host_observation_command(
                "false", [], [], 5, source_dir=str(root))
            observed = run_test_process(
                shlex.split(command), capture_output=True, text=True)
            receipt = json.loads(observed.stdout)
            self.assertFalse(receipt["source_clean"])
            self.assertFalse(marker.exists())

    def test_host_recover_requires_explicit_target_before_manifest_or_writer(self):
        for project_dir, environment in ((None, "production"), ("/project", None)):
            with self.subTest(project_dir=project_dir, environment=environment), \
                 patch.object(hosting_command.hosting, "validate_manifest") as validate, \
                 patch.object(hosting_command.remote, "get_remote") as get_remote, \
                 self.assertRaises(SystemExit):
                hosting_command.cmd_host(None, SimpleNamespace(
                    action="recover", project_dir=project_dir,
                    environment=environment))
            validate.assert_not_called()
            get_remote.assert_not_called()

    def test_json_selector_refusal_is_one_schema_envelope_before_writers(self):
        for project_dir, environment in ((None, "production"), ("/project", None)):
            output = StringIO()
            args = SimpleNamespace(
                action="recover", project_dir=project_dir, environment=environment,
                json=True, continue_edge=False, expected_generation=None)
            with self.subTest(project_dir=project_dir, environment=environment), \
                 redirect_stdout(output), \
                 patch.object(hosting_command.hosting, "validate_manifest") as validate, \
                 patch.object(hosting_command.remote, "get_remote") as get_remote, \
                 patch.object(hosting_command, "RecoveryRepository") as repository, \
                 self.assertRaises(SystemExit) as exited:
                hosting_command.cmd_host(None, args)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(exited.exception.code, 1)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(payload["result_family"], "refused")
            self.assertEqual(payload["result_class"], "binding_mismatch")
            self.assertLessEqual(len(lines[0].encode()), 2048)
            validate.assert_not_called()
            get_remote.assert_not_called()
            repository.assert_not_called()

    def test_recovery_observer_reads_only_opaque_secret_metadata(self):
        validated = {
            "project": "project", "environment": "development",
            "compose": {"service": "web", "background_services": [],
                        "init_services": [], "files": ["compose.yml"]},
            "deploy": {"allowed_branches": ["main"]},
        }
        raw = {
            "complete": True, "bounded": True, "epoch_start": "same",
            "epoch_end": "same", "configured_services": ["web"],
            "images": [{"name": "web", "id": "sha256:" + "1" * 64}],
            "config_digests": [
                {"name": str(index), "digest": "sha256:" + "2" * 64}
                for index in range(4)],
            "source_head": "a" * 40, "source_branch": "main",
            "source_clean": True, "phases": [],
        }
        classified = {"services": [{"service": "web", "state": "running",
                                     "health": "healthy"}],
                      "source_revision": {"state": "ready"}}
        operation = {"source": {"commit": "a" * 40}, "evidence": {
            "machine_identity": "machine-1",
            "secret_binding_metadata_id": "sha256:" + "5" * 64,
            "secret_binding_revision": 1, "secret_binding_key_version": "v1-test"}}
        edge_intent = {"records": [],
                       "routes": [{"hostname": "example.test", "mode": "proxy"}],
                       "certificate_hostnames": ["example.test"], "proxied": False,
                       "healthcheck_path": "/health",
                       "basic_auth": {"enabled": False, "username": None}}
        operation["evidence"]["edge_intent"] = edge_intent
        operation["evidence"]["edge_intent_digest"] = canonical_digest(edge_intent)
        events = []

        def read_metadata(_target):
            events.append("metadata")
            return {"metadata_id": "sha256:" + "5" * 64,
                    "key_version": "v1-test", "revision": 1}

        def observe_remote(*_args, **_kwargs):
            self.assertEqual(events, ["metadata"])
            events.append("remote")
            return raw

        with patch.object(hosting_command.remote, "resolve_sandbox_home", return_value="/srv"), \
             patch.object(hosting_command, "_observe_host_runtime", side_effect=observe_remote), \
             patch.object(hosting_command, "_classify_host_observation", return_value=classified), \
             patch.object(hosting_command, "_registered_host_identity", return_value="sha256:" + "3" * 64), \
             patch.object(hosting_command, "_authenticated_machine_identity",
                          return_value="machine-1"), \
             patch.object(hosting_command, "_nonsecret_host_intent", return_value="sha256:" + "4" * 64), \
             patch.object(hosting_command.hosting, "compose_project_name", return_value="project"), \
             patch.object(hosting_command.hosting, "load_host_state", return_value={"hosts": {}}), \
             patch.object(hosting_command.personal_secrets,
                          "read_hosting_binding_metadata", side_effect=read_metadata), \
             patch.object(hosting_command.personal_secrets,
                          "hosting_binding_key", return_value=(b"k" * 32, "v1-test")), \
             patch.object(hosting_command, "_secret_status",
                          side_effect=AssertionError("secret material reached")):
            result = hosting_command._recovery_observer(
                validated, {}, "remote", Mock(), operation, "machine-1", edge_intent)
        self.assertEqual(result["secret_binding_metadata_id"],
                         "sha256:" + "5" * 64)
        self.assertEqual(events, ["metadata", "remote"])

    def test_json_missing_field_refusal_is_one_versioned_envelope(self):
        args = SimpleNamespace(
            job_id=None, original_request_id="apply-1", request_id="recover-1",
            expected_generation=0, continue_edge=False,
            observation_request_id=None, evidence_id=None, confirm=False, json=True)
        output = StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(output):
            hosting_command._cmd_host_recover(
                {"project": "project", "environment": "development"},
                {}, "remote", args)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["result_class"], "binding_mismatch")

    def test_edge_requires_distinct_observation_evidence_and_confirmation_fields(self):
        with self.assertRaises(ValueError):
            RecoveryRequest(
                RecoveryAction.CONTINUE_EDGE, "edge-1", "a" * 32, "apply-1",
                TargetIdentity("remote", "project", "development"), 1,
            )

    def test_observation_rejects_confirm(self):
        with self.assertRaises(ValueError):
            RecoveryRequest(
                RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
                "apply-1", TargetIdentity("remote", "project", "development"),
                0, confirmed=True,
            )

    def test_authorized_edge_adapter_reaches_only_declared_edge_steps(self):
        validated = {
            "project": "project", "environment": "development",
            "cloudflare": {"proxied": True}, "basic_auth": None,
            "healthcheck": {"path": "/health"},
            "routes": [{"hostname": "example.test", "mode": "proxy"}],
        }
        edge_intent = {
            "records": [{"hostname": "example.test", "address": "192.0.2.1",
                         "proxied": True, "mode": "proxy", "target": None}],
            "routes": validated["routes"],
            "certificate_hostnames": ["example.test"],
            "proxied": True, "healthcheck_path": "/health",
            "basic_auth": {"enabled": False, "username": None},
        }
        operation = {"digest": "sha256:" + "1" * 64, "evidence": {
            "edge_intent": edge_intent,
            "edge_intent_digest": canonical_digest(edge_intent),
        }}
        record = {"loopback_port": 18000, "hosting_operation": operation}
        client = Mock()
        client.records.return_value = []
        client.current_ssl_mode.return_value = "strict"
        client.upsert_address.return_value = {"id": "record-1"}
        certificate_runtime = []

        def issue_certificate(_entry, _validated, runtime, state_entry,
                              _client, _home):
            self.assertNotIn("certificate", state_entry)
            certificate_runtime.append(runtime)
            return "/cert.pem", "/cert.key", {
                "id": "cert-1", "hostnames": runtime["certificate_hostnames"]}

        with patch.object(hosting_command, "_secret_status", return_value=({}, [])), \
             patch.object(hosting_command.hosting, "load_host_state", return_value={
                 "version": 1, "hosts": {"remote/project/development": record}}), \
             patch.object(hosting_command.remote, "resolve_sandbox_home", return_value="/srv"), \
             patch.object(hosting_command.cloudflare, "Client", return_value=client), \
             patch.object(hosting_command, "_zone_for_hostname", return_value={
                 "id": "zone-1", "name": "example.test"}), \
             patch.object(hosting_command, "_read_remote_optional", return_value=None), \
             patch.object(hosting_command, "_origin_certificate",
                          side_effect=issue_certificate), \
             patch.object(hosting_command.hosting, "caddyfile", return_value="edge"), \
             patch.object(hosting_command, "_configure_host_caddy") as caddy, \
             patch.object(hosting_command, "_verify_edge") as verify, \
             patch.object(hosting_command, "_ensure_host_source",
                          side_effect=AssertionError("source path reached")), \
             patch.object(hosting_command, "_run_compose",
                          side_effect=AssertionError("runtime path reached")):
            result = hosting_command._continue_host_edge_only(
                validated, {}, "remote", Mock(), operation)
        self.assertEqual(result["record"]["edge"], {"state": "ready"})
        caddy.assert_called_once()
        client.upsert_address.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(certificate_runtime[0]["certificate_hostnames"],
                         ["example.test"])


if __name__ == "__main__":
    unittest.main()
