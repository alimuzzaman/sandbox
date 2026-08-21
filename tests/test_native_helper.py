import importlib.util
from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timezone


HELPER = Path(__file__).parent.parent / "tools/native-helper/native-helper.py"


def module():
    spec = importlib.util.spec_from_file_location("native_helper_test", HELPER)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


class TestNativeHelper(unittest.TestCase):
    def test_phpunit_artifact_is_pinned_installed_and_warm_tamper_checked(self):
        helper = module()
        wordpress = b"wordpress-archive"
        wp_cli = b"wp-cli-phar"
        phpunit = b"phpunit-phar"
        artifacts = {
            helper.WORDPRESS_URL: wordpress,
            helper.WP_CLI_URL: wp_cli,
            helper.PHPUNIT_URL: phpunit,
        }

        with tempfile.TemporaryDirectory() as directory:
            mountpoint = Path(directory)

            def run_fixed(argv, *_args, **_kwargs):
                if argv[0] == "chroot":
                    destination = mountpoint / argv[argv.index("--output") + 1].lstrip("/")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(artifacts[argv[-1]])
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch.object(
                    helper, "WORDPRESS_SHA256", hashlib.sha256(wordpress).hexdigest()), \
                    mock.patch.object(
                        helper, "WP_CLI_SHA256", hashlib.sha256(wp_cli).hexdigest()), \
                    mock.patch.object(
                        helper, "PHPUNIT_SHA256", hashlib.sha256(phpunit).hexdigest()), \
                    mock.patch.object(helper, "run_fixed", side_effect=run_fixed), \
                    mock.patch.object(helper.os, "chown") as chown:
                helper.install_wordpress_artifacts(mountpoint)

                installed = mountpoint.resolve() / helper.PHPUNIT_PATH.lstrip("/")
                self.assertEqual(installed.read_bytes(), phpunit)
                self.assertEqual(installed.stat().st_mode & 0o777, 0o555)
                self.assertIn(mock.call(installed, 0, 0), chown.call_args_list)
                marker = json.loads((mountpoint / "etc/sandbox-native/artifacts.json").read_text())
                self.assertEqual(marker["phpunit"], {
                    "url": "https://phar.phpunit.de/phpunit-9.6.34.phar",
                    "sha256": hashlib.sha256(phpunit).hexdigest(),
                })

                installed.chmod(0o755)
                installed.write_bytes(b"tampered")
                with self.assertRaises(SystemExit):
                    helper.install_wordpress_artifacts(mountpoint)

    def policy(self, root, identity="sb-0123456789ab"):
        project = root / "project"; project.mkdir(exist_ok=True)
        state = root / "state"; state.mkdir(exist_ok=True)
        helper = module(); value = {
            "policy_version": 1, "machine_id": identity,
            "uid_map": {"base": 196608, "count": 65536},
            "root_image": {"path": f"/var/lib/sandbox/native/instances/{identity}/root.img",
                           "bytes": 8 * 1024**3, "inodes": 500000},
            "read_only_mounts": [{"source": str(project.resolve()), "target": "/workspace"}],
            "writable_mounts": [],
            "network": {"egress": "deny", "veth": "ve-sb-demo",
                        "host_address": "10.203.0.1/30", "guest_address": "10.203.0.2/30",
                        "default_route": False, "ingress_port": 8080, "grants": []},
            "syscalls": {"no_new_privileges": True, "seccomp": "managed-v1"},
            "devices": [], "resources": {"cpu_percent": 200,
                "memory_bytes": 2 * 1024**3, "pids": 512, "runtime_seconds": 3600,
                "disk_bytes": 8 * 1024**3, "inodes": 500000, "fds": 4096,
                "connections": 512, "io_weight": 100},
            "credentials": [],
        }
        value["digest"] = helper.canonical_digest(value)
        path = root / f"{identity}.json"; path.write_text(json.dumps(value)); path.chmod(0o600)
        return helper, path

    def install_plan(self, root):
        helper = module()
        def rows(scope, names):
            return [{"name": name, "version": "1.0", "action": "install", "scope": scope,
                     "origin": "http://archive.ubuntu.com/ubuntu"} for name in names]
        basis = {"matrix_id": "ubuntu-24.04-systemd-255",
            "host_packages": rows("host", helper.HOST_PACKAGE_ROOTS),
            "image_packages": rows("image", (*helper.IMAGE_PACKAGE_ROOTS, "nginx")),
            "sources": [{"uri": "http://archive.ubuntu.com/ubuntu", "suite": "noble",
                         "signed": True, "kind": "archive"}],
            "service_effects": [{"scope": "image", "policy_rc_d": "deny-service-start"}],
            "owned_roots": ["/var/lib/sandbox/native", "/etc/sandbox/native"],
            "privilege_actions": ["policy-install", "image-create", "image-bootstrap"]}
        digest = helper.canonical_digest(basis); value = {**basis, "simulation_digest": digest}
        path = root / f"install-{os.getuid()}-{digest}.json"
        path.write_text(json.dumps(value)); path.chmod(0o600)
        return helper, path, digest, value

    def delegated_policy(self, root):
        helper, path = self.policy(root)
        value = json.loads(path.read_text())
        value["network"].pop("grants")
        value["network"]["grant_authority"] = helper.GRANT_AUTHORITY
        value["digest"] = helper.canonical_digest(
            {key: item for key, item in value.items() if key != "digest"})
        path.write_text(json.dumps(value)); path.chmod(0o600)
        return helper, path, value

    def staged_grants(self, helper, root, policy, grants):
        value = helper.grant_document(policy["machine_id"], policy["digest"], grants)
        path = root / f"grants-{os.getuid()}-{value['grant_digest']}.json"
        path.write_text(json.dumps(value)); path.chmod(0o600)
        return path, value

    def test_grant_reconcile_is_closed_first_and_installs_state_last(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path, policy = self.delegated_policy(root)
            grant = {"grant_id": "api", "owner": policy["machine_id"],
                     "kind": "public_cidr_tcp", "destinations": ["8.8.8.8/32"],
                     "ports": [443], "expires_at": "2999-01-01T00:00:00Z",
                     "revoked": False}
            _stage, desired = self.staged_grants(helper, root, policy, [grant])
            prior = helper.desired_network_state(
                policy["machine_id"], policy["digest"], policy["network"])
            events = []
            def replace(*_args, **kwargs):
                events.append("open" if kwargs["broker"] else "close")
                return helper.desired_network_state(
                    policy["machine_id"], policy["digest"], policy["network"],
                    broker=kwargs["broker"], grant_digest=kwargs["grant_digest"])
            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "GRANT_ROOT", root / "installed"), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, policy)), \
                    mock.patch.object(helper, "invoking_uid", return_value=os.getuid()), \
                    mock.patch.object(helper, "grant_machine_lock", return_value=nullcontext()), \
                    mock.patch.object(helper, "installed_grant_record", return_value=None), \
                    mock.patch.object(helper, "network_state_record", return_value=prior), \
                    mock.patch.object(helper, "observed_nft_state", return_value={
                        "table": {"comment": prior["marker"]}, "chains": prior["chains"],
                        "rules": helper.record_rule_tuple(prior),
                        "counters": {name: {"packets": 0, "bytes": 0}
                                     for name in helper.REQUIRED_NETWORK_RULES}}), \
                    mock.patch.object(helper, "_replace_and_observe_network",
                                      side_effect=replace), \
                    mock.patch.object(helper, "stop_owned_egress",
                                      side_effect=lambda *_: events.append("stop")), \
                    mock.patch.object(helper, "build_egress_config", return_value={
                        "machine_id": policy["machine_id"], "policy_digest": policy["digest"],
                        "grant_digest": desired["grant_digest"], "config_digest": "c" * 64,
                        "grants": []}), \
                    mock.patch.object(helper, "start_egress_config",
                                      side_effect=lambda *_: events.append("start")), \
                    mock.patch.object(helper, "egress_names", return_value=("unit", "run", Path("/x"))), \
                    mock.patch.object(helper, "unit_description", return_value="description"), \
                    mock.patch.object(helper, "egress_description", return_value="description"), \
                    mock.patch.object(helper, "query_egress_status", return_value={"ok": True}), \
                    mock.patch.object(helper, "ensure_root_directory"), \
                    mock.patch.object(helper, "atomic_install_bytes",
                                      side_effect=lambda *_: events.append("install")), \
                    mock.patch.object(helper, "write_network_state",
                                      side_effect=lambda *_: events.append("state")):
                helper.grant_reconcile(policy["machine_id"], policy["digest"],
                                       helper.ABSENT_GRANT_DIGEST, desired["grant_digest"])
            self.assertEqual(events, ["close", "stop", "start", "open", "install", "state"])

    def test_grant_reconcile_cas_mismatch_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path, policy = self.delegated_policy(root)
            _stage, desired = self.staged_grants(helper, root, policy, [])
            current = helper.grant_document(policy["machine_id"], policy["digest"], [])
            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, policy)), \
                    mock.patch.object(helper, "invoking_uid", return_value=os.getuid()), \
                    mock.patch.object(helper, "grant_machine_lock", return_value=nullcontext()), \
                    mock.patch.object(helper, "installed_grant_record", return_value=current), \
                    mock.patch.object(helper, "_replace_and_observe_network") as replace, \
                    self.assertRaises(SystemExit):
                helper.grant_reconcile(policy["machine_id"], policy["digest"],
                                       "f" * 64, desired["grant_digest"])
            replace.assert_not_called()

    def test_grant_reconcile_stops_broker_even_when_firewall_close_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path, policy = self.delegated_policy(root)
            _stage, desired = self.staged_grants(helper, root, policy, [])
            prior = helper.desired_network_state(
                policy["machine_id"], policy["digest"], policy["network"])
            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, policy)), \
                    mock.patch.object(helper, "invoking_uid", return_value=os.getuid()), \
                    mock.patch.object(helper, "grant_machine_lock", return_value=nullcontext()), \
                    mock.patch.object(helper, "installed_grant_record", return_value=None), \
                    mock.patch.object(helper, "network_state_record", return_value=prior), \
                    mock.patch.object(helper, "observed_nft_state", return_value={
                        "table": {"comment": prior["marker"]}, "chains": prior["chains"],
                        "rules": helper.record_rule_tuple(prior),
                        "counters": {name: {"packets": 0, "bytes": 0}
                                     for name in helper.REQUIRED_NETWORK_RULES}}), \
                    mock.patch.object(helper, "_replace_and_observe_network",
                                      side_effect=RuntimeError("nft failed")) as replace, \
                    mock.patch.object(helper, "stop_owned_egress") as stop, \
                    self.assertRaises(RuntimeError):
                helper.grant_reconcile(policy["machine_id"], policy["digest"],
                                       helper.ABSENT_GRANT_DIGEST, desired["grant_digest"])
            self.assertEqual(replace.call_count, 2)
            stop.assert_called_once_with(policy["machine_id"])

    def test_fixed_verbs_and_invalid_identity_are_rejected(self):
        helper = module()
        with self.assertRaises(SystemExit): helper.main(["check-policy", "../../host", "/tmp/x"])
        with self.assertRaises(SystemExit): helper.main(["shell", "anything"])

    def test_preflight_probes_use_installed_fixed_effective_surfaces(self):
        helper = module()
        success = mock.Mock(returncode=0, stdout="", stderr="")
        absent = mock.Mock(returncode=1, stdout="", stderr="")
        for probe in ("private-network", "cgroup-delegation", "nftables"):
            with self.subTest(probe=probe):
                calls = []
                def run(argv, **_kwargs):
                    calls.append(tuple(argv))
                    if argv[0] == "systemctl": return absent
                    return success
                with mock.patch.object(helper, "installed_helper_ready", return_value=True), \
                        mock.patch.object(helper.os, "urandom", return_value=b"x" * 32), \
                        mock.patch.object(helper, "run_optional", side_effect=run), \
                        mock.patch.object(helper, "unit_description", return_value=""):
                    result = helper.preflight_probe(probe)
                self.assertTrue(result["ok"])
                command = next(argv for argv in calls if argv[0] != "systemctl")
                self.assertIn(str(helper.INSTALL_PATH), command)
                self.assertIn("_preflight-child", command)
                if probe == "private-network":
                    self.assertIn("--property=PrivateNetwork=yes", command)
                    self.assertIn("--property=IPAddressDeny=any", command)
                elif probe == "cgroup-delegation":
                    self.assertIn("--scope", command)
                    self.assertIn("--property=Delegate=yes", command)
                    self.assertTrue(any(value.endswith(".scope") for value in command))
                else:
                    self.assertEqual(command[:3], ("unshare", "--net", "--"))

    def test_preflight_batch_emits_complete_canonical_ready_and_blocked_documents(self):
        helper = module()
        self.assertEqual((HELPER.parent / "VERSION").read_text().strip(), "11")
        self.assertEqual(helper.PREFLIGHT_PROBES, (
            "cgroup-delegation", "private-network", "nftables", "seccomp",
        ))
        ready = lambda probe: {"ok": True, "probe": probe, "state": "ready"}
        output = io.StringIO()
        with mock.patch.object(helper, "require_root"), \
                mock.patch.object(helper, "preflight_probe", side_effect=ready) as probe, \
                redirect_stdout(output):
            helper.main(["preflight-probes"])
        self.assertEqual(json.loads(output.getvalue()), {
            "schema": "sandbox.native-helper-preflight/v1",
            "ok": True,
            "state": "ready",
            "probes": [ready(name) for name in helper.PREFLIGHT_PROBES],
        })
        self.assertEqual(
            [call.args[0] for call in probe.call_args_list],
            list(helper.PREFLIGHT_PROBES),
        )

        def one_blocked(name):
            state = "failed" if name == "private-network" else "ready"
            return {"ok": state == "ready", "probe": name, "state": state}

        output = io.StringIO()
        with mock.patch.object(helper, "require_root"), \
                mock.patch.object(helper, "preflight_probe", side_effect=one_blocked) as probe, \
                redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            helper.main(["preflight-probes"])
        self.assertEqual(stopped.exception.code, 69)
        document = json.loads(output.getvalue())
        self.assertEqual(document["state"], "blocked")
        self.assertFalse(document["ok"])
        self.assertEqual(document["probes"], [
            one_blocked(name) for name in helper.PREFLIGHT_PROBES
        ])
        self.assertEqual(probe.call_count, 4)

    def test_preflight_batch_does_not_shape_a_hard_probe_failure(self):
        helper = module()
        output = io.StringIO()
        with mock.patch.object(helper, "require_root"), \
                mock.patch.object(helper, "preflight_probe", side_effect=SystemExit(73)), \
                redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            helper.main(["preflight-probes"])
        self.assertEqual(stopped.exception.code, 73)
        self.assertEqual(output.getvalue(), "")

    def test_preflight_refuses_unit_collision_before_probe(self):
        helper = module(); present = mock.Mock(returncode=0, stdout="loaded\n", stderr="")
        with mock.patch.object(helper, "installed_helper_ready", return_value=True), \
                mock.patch.object(helper.os, "urandom", return_value=b"x" * 32), \
                mock.patch.object(helper, "run_optional", return_value=present) as run:
            result = helper.preflight_probe("private-network")
        self.assertEqual(result, {"ok": False, "probe": "private-network",
                                  "state": "collision"})
        self.assertEqual(run.call_count, 1)

    def test_missing_systemd_unit_has_no_synthetic_owned_description(self):
        helper = module()
        missing = mock.Mock(returncode=0, stdout="not-found\n", stderr="")
        with mock.patch.object(helper, "run_optional", return_value=missing) as run:
            self.assertEqual(helper.unit_description("sandbox-native-probe-missing.service"), "")
        self.assertEqual(run.call_count, 1)

    def test_nft_probe_child_observes_owned_rule_and_cleans_in_finally(self):
        helper = module(); token = "0123456789abcdef"
        marker = f"sandbox-native:preflight:{token}"
        table = f"sb_probe_{token}"
        document = json.dumps({"nftables": [
            {"table": {"family": "inet", "name": table, "comment": marker}},
            {"chain": {"family": "inet", "table": table, "name": "probe",
                       "type": "filter", "hook": "output", "policy": "accept"}},
            {"rule": {"family": "inet", "table": table, "chain": "probe",
                      "comment": marker + ":drop",
                      "expr": [{"counter": {"packets": 1, "bytes": 24}},
                               {"drop": None}]}},
        ]})
        results = [mock.Mock(returncode=1, stdout="", stderr=""),
                   mock.Mock(returncode=0, stdout="", stderr=""),
                   mock.Mock(returncode=0, stdout="", stderr=""),
                   mock.Mock(returncode=0, stdout=document, stderr=""),
                   mock.Mock(returncode=0, stdout=document, stderr=""),
                   mock.Mock(returncode=0, stdout="", stderr="")]
        calls = []
        fake_socket = mock.Mock()
        fake_socket.sendto.side_effect = PermissionError(helper.errno.EPERM,
                                                         "nft output drop")
        with mock.patch.object(helper, "run_optional",
                               side_effect=lambda argv, **kwargs: (
                                   calls.append((tuple(argv), kwargs)) or results.pop(0))), \
                mock.patch.object(helper.socket, "socket", return_value=fake_socket):
            helper._probe_child_nftables(token)
        fake_socket.sendto.assert_called_once_with(b"sandbox-native-preflight",
                                                   ("127.0.0.1", 9))
        self.assertEqual(calls[-1][0], ("nft", "delete", "table", "inet", table))
        self.assertEqual(results, [])

    def test_privileged_subprocess_discards_the_caller_environment(self):
        helper = module()
        result = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(helper.subprocess, "run", return_value=result) as run:
            helper.run_fixed(("nft", "list", "ruleset"), "failed")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment, helper.FIXED_ENVIRONMENT)
        self.assertNotIn("HOME", environment)
        self.assertNotIn("LD_PRELOAD", environment)

    def test_execution_proxy_environment_matches_effective_grant_state(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            policy = json.loads(path.read_text())
            policy["network"].pop("grants")
            policy["network"]["grant_authority"] = helper.GRANT_AUTHORITY
            command = ("/usr/bin/php", "-r", "echo 1;")

            def request(environment):
                writable = tuple({
                    *(item["target"] for item in policy["writable_mounts"]),
                    *helper.EXECUTION_WRITABLE_TARGETS,
                })
                argv = BubblewrapCompiler("/usr/bin/bwrap").argv(
                    environment=environment, writable_targets=writable,
                    command=command,
                    payload_profile=f"sandbox-native-{policy['machine_id']}//payload",
                )
                return {"argv": list(argv), "environment": environment,
                        "credential_refs": [], "timeout": 30}

            host = policy["network"]["host_address"].split("/", 1)[0]
            proxy = f"http://{host}:{helper.BROKER_PORT}"
            active = {"grants": [{"grant_id": "api", "revoked": False}]}
            with mock.patch.object(helper, "installed_grant_record", return_value=None):
                argv, _timeout = helper.validated_execution_argv(policy, request({}))
                self.assertEqual(argv[-len(command):], command)
                policy["resources"]["runtime_seconds"] = 10
                _argv, capped = helper.validated_execution_argv(policy, request({}))
                self.assertEqual(capped, 10)
                policy["resources"]["runtime_seconds"] = 3600
                for environment in ({"HTTP_PROXY": proxy},
                                    {"HTTP_PROXY": "http://127.0.0.1:9",
                                     "HTTPS_PROXY": proxy},
                                    {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}):
                    with self.subTest(environment=environment), self.assertRaises(SystemExit):
                        helper.validated_execution_argv(policy, request(environment))
            with mock.patch.object(helper, "installed_grant_record", return_value=active):
                argv, _timeout = helper.validated_execution_argv(
                    policy, request({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy}))
                self.assertEqual(argv[-len(command):], command)
                with self.assertRaises(SystemExit):
                    helper.validated_execution_argv(policy, request({}))

    def test_execution_without_the_payload_profile_stack_is_refused(self):
        # The privileged side enforces the stack rather than trusting the caller.
        # Without this a caller could simply omit the wrapper and run the payload
        # under the weaker bwrap profile, which is what the stack exists to
        # prevent (FR-047).
        from sandbox.isolation.bubblewrap import BubblewrapCompiler

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            policy = json.loads(path.read_text())
            policy["network"].pop("grants", None)
            command = ("/usr/bin/php", "-r", "echo 1;")
            writable = tuple({
                *(item["target"] for item in policy["writable_mounts"]),
                *helper.EXECUTION_WRITABLE_TARGETS,
            })
            unstacked = BubblewrapCompiler("/usr/bin/bwrap").argv(
                environment={}, writable_targets=writable, command=command,
            )
            request = {"argv": list(unstacked), "environment": {},
                       "credential_refs": [], "timeout": 30}
            with mock.patch.object(helper, "installed_grant_record", return_value=None), \
                    self.assertRaises(SystemExit):
                helper.validated_execution_argv(policy, request)

    def test_a_leftover_staging_plan_from_a_killed_run_is_replaced(self):
        # The name is derived from the uid and plan digest, so a run that died
        # before its cleanup left a file every later run collided with -- and the
        # collision was permanent: "File exists: .../install-1000-<digest>.json".
        from sandbox.runtimes.managed.packages import PackagePlanStager
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            stager = PackagePlanStager(root=directory)
            plan = SimpleNamespace(simulation_digest="d" * 64,
                                   to_dict=lambda: {"packages": []})
            first = stager.stage(plan)
            self.assertTrue(first.exists())
            # Left behind, exactly as a killed run leaves it.
            second = stager.stage(plan)
            self.assertEqual(first, second)
            self.assertEqual(json.loads(second.read_text()), {"packages": []})
            self.assertEqual(second.stat().st_mode & 0o777, 0o600)
            # No temporary files survive.
            self.assertEqual([item.name for item in Path(directory).iterdir()],
                             [first.name])

    def test_policy_path_symlink_mode_owner_and_fixed_root_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                helper.checked_policy(path, "sb-0123456789ab")
                with mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid() + 1)}):
                    with self.assertRaises(SystemExit):
                        helper.checked_policy(path, "sb-0123456789ab")
                path.chmod(0o666)
                with self.assertRaises(SystemExit): helper.checked_policy(path, "sb-0123456789ab")
                path.chmod(0o600); link = root / "link.json"; link.symlink_to(path)
                with self.assertRaises(SystemExit): helper.checked_policy(link, "sb-0123456789ab")

    def test_digest_or_default_allow_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                value = json.loads(path.read_text()); value["network"]["egress"] = "allow"
                path.write_text(json.dumps(value))
                with self.assertRaises(SystemExit): helper.checked_policy(path, "sb-0123456789ab")

    def test_host_mount_and_missing_resource_ceiling_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                for mutate in (
                    lambda value: value["read_only_mounts"].append(
                        {"source": "/etc", "target": "/host"}),
                    lambda value: value["resources"].pop("pids"),
                ):
                    value = json.loads(path.read_text()); mutate(value)
                    value["digest"] = helper.canonical_digest(
                        {key: item for key, item in value.items() if key != "digest"})
                    path.write_text(json.dumps(value))
                    with self.assertRaises(SystemExit):
                        helper.checked_policy(path, "sb-0123456789ab")
                    _helper, path = self.policy(root)

    def test_effective_mount_observation_rejects_any_extra_host_bind_and_source_swap(self):
        from types import SimpleNamespace

        helper = module()
        policy = {
            "read_only_mounts": [{"source": "/project", "target": "/workspace"}],
            "writable_mounts": [],
        }
        rows = "\n".join((
            "1 0 7:1 / / rw,relatime - ext4 /dev/loop0 rw",
            "2 1 8:1 /project /workspace ro,relatime - ext4 /dev/sda1 rw",
            "3 1 8:1 /private /workspace-extra rw,relatime - ext4 /dev/sda1 rw",
            "4 1 0:5 / /proc rw,nosuid - proc proc rw",
            "5 1 8:2 / /boot ro,relatime - ext4 /dev/sdb1 ro",
            "6 1 0:6 / /host-run rw,nosuid - tmpfs tmpfs rw",
        ))

        def identity(path, *, follow_symlinks):
            values = {
                "/project": (8, 100),
                "/proc/77/root/workspace": (8, 100),
                "/proc/77/root/proc": (9, 200),
                "/proc": (10, 200),
            }
            dev, inode = values[path]
            return SimpleNamespace(st_dev=dev, st_ino=inode)

        with mock.patch.object(helper.Path, "read_text", return_value=rows), \
                mock.patch.object(helper.os, "stat", side_effect=identity):
            read_only, writable, unexpected = helper.observed_mounts(77, policy)
        self.assertEqual(read_only, ["/workspace"])
        self.assertEqual(writable, [])
        self.assertIn("/workspace-extra", unexpected)
        self.assertIn("/boot", unexpected)
        self.assertIn("/host-run", unexpected)

        def swapped(path, *, follow_symlinks):
            values = {
                "/project": (8, 100),
                "/proc/77/root/workspace": (8, 999),
                "/proc/77/root/proc": (9, 200),
                "/proc": (10, 200),
            }
            dev, inode = values[path]
            return SimpleNamespace(st_dev=dev, st_ino=inode)

        with mock.patch.object(helper.Path, "read_text", return_value=rows), \
                mock.patch.object(helper.os, "stat", side_effect=swapped):
            read_only, _writable, unexpected = helper.observed_mounts(77, policy)
        self.assertEqual(read_only, [])
        self.assertIn("/workspace", unexpected)

    def test_resource_observation_reads_every_effective_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path = self.policy(root)
            policy = json.loads(policy_path.read_text())
            image = root / "root.img"; image.write_bytes(b"")
            resources = policy["resources"]
            compiled_files, _units = helper.compile_service_files(
                "10.203.0.2", resources["connections"], resources["runtime_seconds"],
                "nginx", policy["network"]["ingress_port"], (),
            )
            compiled_cron = compiled_files["/usr/local/libexec/sandbox-wordpress-cron"]
            properties = {
                "MemoryMax": str(resources["memory_bytes"]),
                "MemoryHigh": str(resources["memory_bytes"] * 9 // 10),
                "MemorySwapMax": "0", "TasksMax": str(resources["pids"]),
                "LimitNOFILE": str(resources["fds"]),
                "IOWeight": str(resources["io_weight"]),
                "CPUQuotaPerSecUSec": "2s",
            }

            def observe(argv, **_kwargs):
                if argv[0] == "systemctl":
                    name = argv[3].split("=", 1)[1]
                    return mock.Mock(returncode=0, stdout=properties[name] + "\n")
                if argv[0] == "dumpe2fs":
                    return mock.Mock(returncode=0, stdout=(
                        # mke2fs rounds the request up to a whole block
                        # group, so the real filesystem never reports the
                        # number that was asked for.
                        "Inode count:              500736\n"
                        "Inodes per group:         2048\n"
                        "Block count:              2097152\n"
                        "Block size:               4096\n"))
                if argv[-1] == "SELECT @@GLOBAL.max_connections;":
                    return mock.Mock(returncode=0,
                                     stdout="512\n")
                if argv[-1].endswith("services.json"):
                    return mock.Mock(returncode=0, stdout=json.dumps({
                        "machine_id": policy["machine_id"],
                        "policy_digest": policy["digest"],
                        "web_server": "nginx",
                    }))
                if argv[-1].endswith("sandbox-wordpress-cron"):
                    return mock.Mock(returncode=0, stdout=compiled_cron)
                if argv[-1] == "-T":
                    return mock.Mock(returncode=0, stderr="", stdout=(
                        "worker_processes 1;\n"
                        "events {\n    worker_connections 512;\n}\n"))
                if argv[-1] == "-tt":
                    return mock.Mock(returncode=0, stdout="", stderr=(
                        "NOTICE: [sandbox]\n"
                        "NOTICE: pm.max_children = 32\n"
                        "NOTICE: listen = /run/php/sandbox.sock\n"
                        "NOTICE: request_terminate_timeout = 3600s\n"))
                return mock.Mock(returncode=1, stdout="", stderr="unexpected")

            details = mock.Mock(st_mode=helper.stat.S_IFREG | 0o600, st_uid=0,
                                st_size=resources["disk_bytes"])
            with mock.patch.object(helper, "image_paths",
                                   return_value=(root, image, root / "mount")), \
                    mock.patch.object(helper.Path, "lstat", return_value=details), \
                    mock.patch.object(helper, "run_optional", side_effect=observe):
                actual = helper.resource_limits_match(policy["machine_id"], policy)
            self.assertEqual(actual, {**resources,
                "memory_high_bytes": resources["memory_bytes"] * 9 // 10,
                "memory_swap_bytes": 0})

    def test_resource_observation_never_synthesizes_unobserved_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            policy = json.loads(path.read_text())
            missing = mock.Mock(returncode=1, stdout="")
            with mock.patch.object(helper, "run_optional", return_value=missing):
                self.assertEqual(
                    helper.resource_limits_match(policy["machine_id"], policy), {})

    def test_policy_install_uses_validated_bytes_not_a_reopened_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            installed = root / "installed"
            original = path.read_bytes()
            replacement = b'{"attacker":"replacement"}'
            real_install = helper.atomic_install_bytes

            def replace_then_install(payload, destination):
                path.unlink(); path.write_bytes(replacement)
                return real_install(payload, destination)

            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "POLICY_ROOT", installed), \
                    mock.patch.object(helper, "POLICY_OWNER_ROOT", root / "owners"), \
                    mock.patch.object(helper, "require_root"), \
                    mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}), \
                    mock.patch.object(helper, "atomic_install_bytes", replace_then_install):
                helper.main(["policy-install", "sb-0123456789ab", str(path)])
            self.assertEqual((installed / "sb-0123456789ab.json").read_bytes(), original)

    def test_policy_install_repairs_exact_owner_only_and_policy_only_crash_states(self):
        for surviving in ("owner", "policy"):
            with self.subTest(surviving=surviving), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); helper, path = self.policy(root)
                value = json.loads(path.read_text())
                policies = root / "installed"; owners = root / "owners"
                policies.mkdir(); owners.mkdir()
                owner = {"machine_id": value["machine_id"],
                         "policy_digest": value["digest"],
                         **helper.project_source_identity(value, os.getuid())}
                owner_payload = (json.dumps(
                    owner, sort_keys=True, separators=(",", ":"),
                ) + "\n").encode()
                policy_destination = policies / f"{value['machine_id']}.json"
                owner_destination = owners / f"{value['machine_id']}.json"
                destination, payload = (
                    (owner_destination, owner_payload) if surviving == "owner"
                    else (policy_destination, path.read_bytes())
                )
                destination.write_bytes(payload); destination.chmod(0o600)
                with mock.patch.object(helper, "STAGING_ROOT", root), \
                        mock.patch.object(helper, "POLICY_ROOT", policies), \
                        mock.patch.object(helper, "POLICY_OWNER_ROOT", owners), \
                        mock.patch.object(helper, "require_root"), \
                        mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}):
                    helper.main(["policy-install", value["machine_id"], str(path)])
                self.assertEqual(policy_destination.read_bytes(), path.read_bytes())
                self.assertEqual(owner_destination.read_bytes(), owner_payload)

    def test_policy_install_rolls_back_new_exact_owner_when_policy_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            value = json.loads(path.read_text())
            policies = root / "installed"; owners = root / "owners"
            real_install = helper.atomic_install_bytes

            def fail_policy(payload, destination):
                if destination.parent == policies:
                    raise OSError("simulated policy write failure")
                return real_install(payload, destination)

            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "POLICY_ROOT", policies), \
                    mock.patch.object(helper, "POLICY_OWNER_ROOT", owners), \
                    mock.patch.object(helper, "require_root"), \
                    mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}), \
                    mock.patch.object(helper, "atomic_install_bytes", side_effect=fail_policy), \
                    self.assertRaises(OSError):
                helper.main(["policy-install", value["machine_id"], str(path)])
            self.assertFalse((policies / f"{value['machine_id']}.json").exists())
            self.assertFalse((owners / f"{value['machine_id']}.json").exists())

    def test_policy_install_preserves_drifted_partial_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            value = json.loads(path.read_text())
            policies = root / "installed"; owners = root / "owners"
            policies.mkdir(); owners.mkdir()
            owner_destination = owners / f"{value['machine_id']}.json"
            owner_destination.write_text('{"foreign":true}\n'); owner_destination.chmod(0o600)
            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "POLICY_ROOT", policies), \
                    mock.patch.object(helper, "POLICY_OWNER_ROOT", owners), \
                    mock.patch.object(helper, "require_root"), \
                    mock.patch.dict(os.environ, {"SUDO_UID": str(os.getuid())}), \
                    self.assertRaises(SystemExit):
                helper.main(["policy-install", value["machine_id"], str(path)])
            self.assertEqual(owner_destination.read_text(), '{"foreign":true}\n')
            self.assertFalse((policies / f"{value['machine_id']}.json").exists())

    def test_network_apply_compiles_only_instance_drop_rules(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        policy_value = json.loads(path.read_text()); calls = []
        with mock.patch.object(helper, "applied_policy", return_value=(path, policy_value)), \
                mock.patch.object(helper, "observed_link",
                                  return_value={"ifalias": ""}), \
                mock.patch.object(helper, "observed_nft_table", return_value=None), \
                mock.patch.object(helper, "network_state_record", return_value=None), \
                mock.patch.object(helper, "write_network_state"), \
                mock.patch.object(helper, "machine_leader", return_value=4242), \
                mock.patch.object(helper, "run_fixed",
                                  side_effect=lambda argv, message, **kw: calls.append((argv, kw))):
            helper.network_apply("sb-0123456789ab", policy_value["digest"])
        nft = next(kwargs["input_text"] for argv, kwargs in calls if argv[:2] == ("nft", "-f"))
        self.assertIn('input iifname "ve-sb-demo"', nft)
        self.assertIn('forward iifname "ve-sb-demo"', nft)
        self.assertIn('output oifname "ve-sb-demo" ip daddr 10.203.0.2 tcp dport 8080', nft)
        self.assertIn('output oifname "ve-sb-demo" counter drop comment "host_guest_drop"', nft)
        self.assertEqual(nft.count(" comment \""), 7)  # table marker plus six countered rules
        self.assertNotIn("counter drop\nip saddr", nft)
        self.assertNotIn("masquerade", nft)
        self.assertNotIn("policy drop", nft)
        namespace_calls = [argv for argv, _kwargs in calls if argv[:5] ==
                           ("nsenter", "--target", "4242", "--net", "--")]
        self.assertTrue(any(argv[-4:] == ("route", "flush", "table", "main")
                            for argv in namespace_calls))

    def test_network_apply_rolls_back_new_exact_nft_when_state_write_fails(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        desired = helper.desired_network_state(
            value["machine_id"], value["digest"], value["network"],
        )
        observed_state = {
            "table": {"comment": desired["marker"]},
            "chains": desired["chains"],
            "rules": helper.record_rule_tuple(desired),
            "counters": {name: {"packets": 0, "bytes": 0}
                         for name, _rule in helper.record_rule_tuple(desired)},
        }
        table_observations = iter((None, {"comment": desired["marker"]}, None))
        optional = []

        def run_optional(argv, **kwargs):
            optional.append(tuple(argv))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "observed_link", return_value={"ifalias": ""}), \
                mock.patch.object(helper, "observed_nft_table",
                                  side_effect=lambda _table: next(table_observations)), \
                mock.patch.object(helper, "observed_nft_state", return_value=observed_state), \
                mock.patch.object(helper, "network_state_record", return_value=None), \
                mock.patch.object(helper, "write_network_state",
                                  side_effect=OSError("state write failed")), \
                mock.patch.object(helper, "machine_leader", return_value=4242), \
                mock.patch.object(helper, "run_fixed", return_value=mock.Mock(returncode=0)), \
                mock.patch.object(helper, "run_optional", side_effect=run_optional), \
                self.assertRaises(OSError):
            helper.network_apply(value["machine_id"], value["digest"])
        self.assertIn(("nft", "delete", "table", "inet", "sb_0123456789ab"), optional)

    def test_network_apply_repairs_exact_nft_without_record_after_lost_response(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        desired = helper.desired_network_state(
            value["machine_id"], value["digest"], value["network"],
        )
        observed_state = {
            "table": {"comment": desired["marker"]},
            "chains": desired["chains"],
            "rules": helper.record_rule_tuple(desired),
            "counters": {name: {"packets": 0, "bytes": 0}
                         for name, _rule in helper.record_rule_tuple(desired)},
        }
        fixed = []; written = []
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "observed_link",
                                  return_value={"ifalias": "sandbox-native:sb-0123456789ab"}), \
                mock.patch.object(helper, "observed_nft_table",
                                  return_value={"comment": desired["marker"]}), \
                mock.patch.object(helper, "observed_nft_state", return_value=observed_state), \
                mock.patch.object(helper, "network_state_record", return_value=None), \
                mock.patch.object(helper, "write_network_state",
                                  side_effect=lambda record: written.append(record)), \
                mock.patch.object(helper, "machine_leader", return_value=4242), \
                mock.patch.object(helper, "run_fixed",
                                  side_effect=lambda argv, message, **kw: fixed.append(tuple(argv))):
            helper.network_apply(value["machine_id"], value["digest"])
        self.assertEqual(written, [desired])
        self.assertFalse(any(argv[:2] == ("nft", "-f") for argv in fixed))

    def test_network_apply_keeps_active_grants_closed_in_the_baseline_phase(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        for kind, destinations in (("hostname_https", ["api.wordpress.org"]),
                                   ("public_cidr_tcp", ["8.8.8.8/32"])):
            with self.subTest(kind=kind):
                value = json.loads(path.read_text())
                value["network"]["grants"] = [{"grant_id": "wordpress-org",
                    "owner": "sb-0123456789ab", "kind": kind,
                    "destinations": destinations, "ports": [443],
                    "expires_at": "2999-01-01T00:00:00Z", "revoked": False}]
                calls = []
                with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                        mock.patch.object(helper, "observed_link", return_value={"ifalias": ""}), \
                        mock.patch.object(helper, "observed_nft_table", return_value=None), \
                        mock.patch.object(helper, "network_state_record", return_value=None), \
                        mock.patch.object(helper, "write_network_state"), \
                        mock.patch.object(helper, "machine_leader", return_value=4242), \
                        mock.patch.object(helper, "run_fixed", side_effect=lambda argv, message,
                                          **kwargs: calls.append((argv, kwargs))):
                    helper.network_apply("sb-0123456789ab", value["digest"])
                nft = next(kwargs["input_text"] for argv, kwargs in calls
                           if argv[:2] == ("nft", "-f"))
                self.assertNotIn("egress_broker_request", nft)
                self.assertNotIn("egress_broker_reply", nft)
                self.assertNotIn("masquerade", nft)

    def test_network_grants_open_only_proven_broker_endpoint(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        value["network"]["grants"] = [{"grant_id": "wordpress-org",
            "owner": "sb-0123456789ab", "kind": "public_cidr_tcp",
            "destinations": ["8.8.8.8/32"], "ports": [443],
            "expires_at": "2999-01-01T00:00:00Z", "revoked": False}]
        prior = helper.desired_network_state("sb-0123456789ab", value["digest"],
                                             value["network"])
        state = {"table": {"comment": prior["marker"]},
                 "chains": helper.expected_network_chains(),
                 "rules": helper.expected_network_rules(value["network"]),
                 "counters": {name: {"packets": 0, "bytes": 0}
                              for name in helper.REQUIRED_NETWORK_RULES}}
        config = {"machine_id": "sb-0123456789ab", "policy_digest": value["digest"],
                  "grant_digest": "c" * 64, "config_digest": "b" * 64}
        calls = []
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "egress_config_record", return_value=config), \
                mock.patch.object(helper, "unit_description",
                                  return_value=helper.egress_description(config)), \
                mock.patch.object(helper, "query_egress_status", return_value={"ok": True}), \
                mock.patch.object(helper, "observed_nft_table",
                                  return_value={"comment": prior["marker"]}), \
                mock.patch.object(helper, "network_state_record", return_value=prior), \
                mock.patch.object(helper, "observed_nft_state", return_value=state), \
                mock.patch.object(helper, "write_network_state") as write, \
                mock.patch.object(helper, "run_fixed", side_effect=lambda argv, message,
                                  **kwargs: calls.append((argv, kwargs))):
            helper.network_grants_apply("sb-0123456789ab", value["digest"])
        nft = calls[0][1]["input_text"]
        self.assertIn('ip daddr 10.203.0.1 tcp dport 18443', nft)
        self.assertIn('ip saddr 10.203.0.1 ip daddr 10.203.0.2 tcp sport 18443', nft)
        self.assertNotIn("8.8.8.8", nft)
        self.assertNotIn("masquerade", nft)
        written = write.call_args.args[0]
        self.assertEqual(written, helper.desired_network_state(
            "sb-0123456789ab", value["digest"], value["network"], broker=True))

    def test_egress_config_pins_hostname_and_systemd_unit_is_hardened(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        value["network"]["grants"] = [{"grant_id": "wordpress-org",
            "owner": "sb-0123456789ab", "kind": "hostname_https",
            "destinations": ["api.wordpress.org"], "ports": [443],
            "expires_at": "2999-01-01T00:00:00Z", "revoked": False}]
        with mock.patch.object(helper, "observed_forbidden_networks",
                               return_value=("10.0.0.0/8",)), \
                mock.patch.object(helper.socket, "getaddrinfo", return_value=[
                    (helper.socket.AF_INET, helper.socket.SOCK_STREAM, 6, "",
                     ("8.8.8.8", 443))]):
            config = helper.build_egress_config(
                "sb-0123456789ab", value["digest"], value["network"],
                value["resources"]["connections"])
        self.assertEqual(config["grants"][0]["pins"], {
            "api.wordpress.org": ["8.8.8.8"],
        })
        self.assertEqual(config["config_digest"], helper.canonical_digest(
            {key: item for key, item in config.items() if key != "config_digest"}))

        description = helper.egress_description(config); calls = []
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "build_egress_config", return_value=config), \
                mock.patch.object(helper, "egress_config_record", return_value=None), \
                mock.patch.object(helper, "write_egress_config") as write, \
                mock.patch.object(helper, "unit_description",
                                  side_effect=("", description)), \
                mock.patch.object(helper, "query_egress_status",
                                  return_value={"ok": True}), \
                mock.patch.object(helper, "run_fixed",
                                  side_effect=lambda argv, message, **kwargs:
                                  calls.append(tuple(argv))):
            helper.egress_apply("sb-0123456789ab", value["digest"])
        write.assert_called_once_with(config)
        command = calls[0]
        for property_value in (
            "--property=DynamicUser=yes", "--property=NoNewPrivileges=yes",
            "--property=ProtectSystem=strict", "--property=ProtectHome=yes",
            "--property=PrivateDevices=yes", "--property=IPAddressDeny=any",
            "--property=IPAddressAllow=8.8.8.8/32",
            "--property=RestrictAddressFamilies=AF_UNIX AF_INET",
            "--property=SocketBindDeny=any",
            "--property=SocketBindAllow=ipv4:tcp:18443",
        ):
            self.assertIn(property_value, command)
        self.assertEqual(command[-2], str(helper.BROKER_INSTALL_PATH))

    def test_fixed_grant_overlapping_current_host_address_is_rejected(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        value["network"]["grants"] = [{"grant_id": "api",
            "owner": "sb-0123456789ab", "kind": "public_cidr_tcp",
            "destinations": ["8.8.8.0/24"], "ports": [443],
            "expires_at": "2999-01-01T00:00:00Z", "revoked": False}]
        with mock.patch.object(helper, "observed_forbidden_networks",
                               return_value=("8.8.8.8/32",)), \
                self.assertRaises(SystemExit):
            helper.build_egress_config("sb-0123456789ab", value["digest"],
                                       value["network"],
                                       value["resources"]["connections"])

    def test_egress_remove_stops_exact_owned_unit_before_deleting_config(self):
        helper = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "sb-0123456789ab.json"; path.write_text("{}")
            config = {"machine_id": "sb-0123456789ab", "policy_digest": "a" * 64,
                      "grant_digest": "c" * 64, "config_digest": "b" * 64}
            calls = []
            with mock.patch.object(helper, "EGRESS_ROOT", root), \
                    mock.patch.object(helper, "egress_config_record", return_value=config), \
                    mock.patch.object(helper, "unit_description",
                                      return_value=helper.egress_description(config)), \
                    mock.patch.object(helper, "run_fixed",
                                      side_effect=lambda argv, message, **kwargs:
                                      calls.append(tuple(argv))):
                helper.egress_remove("sb-0123456789ab", "a" * 64)
            self.assertEqual(calls, [("systemctl", "stop",
                                     "sandbox-native-egress-0123456789ab.service")])
            self.assertFalse(path.exists())

    def test_policy_rejects_broad_private_expired_or_foreign_fixed_grants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    mock.patch.object(helper, "utc_now",
                                      return_value=datetime(2026, 1, 1, tzinfo=timezone.utc)):
                cases = (
                    {"grant_id": "broad", "owner": "sb-0123456789ab",
                     "kind": "public_cidr_tcp", "destinations": ["0.0.0.0/0"],
                     "ports": [443], "expires_at": "2999-01-01T00:00:00Z", "revoked": False},
                    {"grant_id": "private", "owner": "sb-0123456789ab",
                     "kind": "public_cidr_tcp", "destinations": ["10.0.0.0/8"],
                     "ports": [443], "expires_at": "2999-01-01T00:00:00Z", "revoked": False},
                    {"grant_id": "expired", "owner": "sb-0123456789ab",
                     "kind": "public_cidr_tcp", "destinations": ["8.8.8.8/32"],
                     "ports": [443], "expires_at": "2025-01-01T00:00:00Z", "revoked": False},
                    {"grant_id": "foreign", "owner": "sb-fedcba987654",
                     "kind": "public_cidr_tcp", "destinations": ["8.8.8.8/32"],
                     "ports": [443], "expires_at": "2999-01-01T00:00:00Z", "revoked": False},
                )
                for grant in cases:
                    with self.subTest(grant=grant["grant_id"]):
                        value = json.loads(path.read_text()); value["network"]["grants"] = [grant]
                        value["digest"] = helper.canonical_digest(
                            {key: item for key, item in value.items() if key != "digest"})
                        path.write_text(json.dumps(value))
                        with self.assertRaises(SystemExit):
                            helper.checked_policy(path, "sb-0123456789ab")

    def test_policy_rejects_integer_revocation_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path = self.policy(root)
            value = json.loads(path.read_text())
            value["network"]["grants"] = [{
                "grant_id": "api", "owner": "sb-0123456789ab",
                "kind": "public_cidr_tcp", "destinations": ["8.8.8.8/32"],
                "ports": [443], "expires_at": "2999-01-01T00:00:00Z",
                "revoked": 1,
            }]
            value["digest"] = helper.canonical_digest(
                {key: item for key, item in value.items() if key != "digest"})
            path.write_text(json.dumps(value))
            with mock.patch.object(helper, "STAGING_ROOT", root), \
                    self.assertRaises(SystemExit):
                helper.checked_policy(path, "sb-0123456789ab")

    def test_rule_comment_cannot_hide_a_different_nft_verdict(self):
        helper = module()
        network = {"veth": "ve-sb-demo", "guest_address": "10.203.0.2/30",
                   "ingress_port": 8080}
        expected = dict(helper.expected_network_rules(network))["guest_host_drop"]
        malicious = helper.canonical_digest({
            "chain": "input", "expr": [
                {"match": {"op": "==", "left": {"meta": {"key": "iifname"}},
                           "right": "ve-sb-demo"}},
                {"counter": {}}, {"accept": None},
            ],
        })
        self.assertNotEqual(expected, malicious)

    def test_network_status_requires_exact_owned_rules_and_reports_counters(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text())
        state = {"table": {"comment": helper.network_marker("sb-0123456789ab", value["digest"])},
                 "chains": {name: {"type": "filter", "hook": name, "prio": 0,
                                    "policy": "accept"}
                            for name in ("input", "output", "forward")},
                 "rules": helper.expected_network_rules(value["network"]),
                 "counters": {name: {"packets": 1, "bytes": 64}
                              for name in helper.REQUIRED_NETWORK_RULES}}
        record = helper.desired_network_state("sb-0123456789ab", value["digest"],
                                              value["network"])
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "observed_link",
                                  return_value={"ifalias": "sandbox-native:sb-0123456789ab"}), \
                mock.patch.object(helper, "observed_nft_state", return_value=state), \
                mock.patch.object(helper, "network_state_record", return_value=record), \
                mock.patch.object(helper, "unit_description", return_value=""), \
                mock.patch.object(helper, "observed_guest_network",
                                  return_value={"guest_address": "10.203.0.2/30",
                                                "default_route": False,
                                                "routes": ["10.203.0.0/30"]}), \
                mock.patch("builtins.print") as output:
            helper.network_status("sb-0123456789ab", value["digest"])
        document = json.loads(output.call_args.args[0])
        self.assertTrue(document["ok"])
        self.assertEqual(document["counters"]["guest_host_drop"]["packets"], 1)

    def test_network_apply_preserves_exact_owned_table_and_rejects_old_digest(self):
        helper = module(); _unused, path = self.policy(Path(tempfile.mkdtemp()))
        value = json.loads(path.read_text()); calls = []
        owned = {"comment": helper.network_marker("sb-0123456789ab", value["digest"])}
        state = {"table": owned,
                 "chains": {name: {"type": "filter", "hook": name, "prio": 0,
                                    "policy": "accept"}
                            for name in ("input", "output", "forward")},
                 "rules": helper.expected_network_rules(value["network"]),
                 "counters": {name: {"packets": 7, "bytes": 448}
                              for name in helper.REQUIRED_NETWORK_RULES}}
        record = helper.desired_network_state("sb-0123456789ab", value["digest"],
                                              value["network"])
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "observed_link",
                                  return_value={"ifalias": "sandbox-native:sb-0123456789ab"}), \
                mock.patch.object(helper, "observed_nft_table", return_value=owned), \
                mock.patch.object(helper, "observed_nft_state", return_value=state), \
                mock.patch.object(helper, "network_state_record", return_value=record), \
                mock.patch.object(helper, "write_network_state"), \
                mock.patch.object(helper, "machine_leader", return_value=4242), \
                mock.patch.object(helper, "run_fixed",
                                  side_effect=lambda argv, message, **kw: calls.append((argv, kw))):
            helper.network_apply("sb-0123456789ab", value["digest"])
        self.assertFalse(any(argv[:2] == ("nft", "-f") for argv, _kwargs in calls))

        old = {"comment": "sandbox-native:sb-0123456789ab:" + "b" * 64}
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "observed_link", return_value={
                    "ifalias": "sandbox-native:sb-0123456789ab"}), \
                mock.patch.object(helper, "observed_nft_table", return_value=old), \
                self.assertRaises(SystemExit):
            helper.network_apply("sb-0123456789ab", value["digest"])

        prior = helper.desired_network_state("sb-0123456789ab", "b" * 64,
                                             value["network"])
        owned_old = {"comment": prior["marker"]}
        calls = []
        with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                mock.patch.object(helper, "observed_link", return_value={
                    "ifalias": "sandbox-native:sb-0123456789ab"}), \
                mock.patch.object(helper, "observed_nft_table", return_value=owned_old), \
                mock.patch.object(helper, "observed_nft_state", return_value=state), \
                mock.patch.object(helper, "network_state_record", return_value=prior), \
                mock.patch.object(helper, "write_network_state"), \
                mock.patch.object(helper, "machine_leader", return_value=4242), \
                mock.patch.object(helper, "run_fixed",
                                  side_effect=lambda argv, message, **kw:
                                  calls.append((argv, kw))):
            helper.network_apply("sb-0123456789ab", value["digest"])
        nft = next(kwargs["input_text"] for argv, kwargs in calls
                   if argv[:2] == ("nft", "-f"))
        self.assertTrue(nft.startswith("delete table inet sb_0123456789ab\nadd table"))

    def test_machine_command_is_fixed_digest_bound_and_systemd_255_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, path = self.policy(Path(directory)); value = json.loads(path.read_text())
            command, description = helper.machine_command(value)
        self.assertEqual(command[:5], ("systemd-run", "--no-block", "--collect",
                                       "--unit=sandbox-native-sb-0123456789ab.service",
                                       "--service-type=notify"))
        self.assertIn("--settings=no", command)
        self.assertIn("--private-network", command)
        self.assertIn("--network-veth-extra=ve-sb-demo:host0", command)
        # NoNewPrivileges is applied by the guest's own service units instead:
        # on the machine it makes the kernel refuse the AppArmor transition into
        # the tighter //guest profile, so the guest init could never exec.
        self.assertNotIn("--no-new-privileges=yes", command)
        system_filter = next(value for value in command
                             if value.startswith("--system-call-filter="))
        self.assertNotIn("~@mount", system_filter)
        # The machine's init mounts its API filesystems inside its own namespace;
        # @system-service alone does not contain those syscalls.
        self.assertIn("@mount", system_filter)
        self.assertIn("~@raw-io", system_filter)
        # CAP_SYS_ADMIN stays inside the machine's private user namespace so its
        # init can mount API filesystems; untrusted payloads never hold it
        # (AppArmor payload profile plus the transient exec restrictions).
        dropped = next(value for value in command
                       if value.startswith("--drop-capability="))
        self.assertNotIn("CAP_SYS_ADMIN", dropped)
        for capability in ("CAP_SYS_PTRACE", "CAP_NET_ADMIN", "CAP_MKNOD", "CAP_SYS_BOOT"):
            self.assertIn(capability, dropped)
        self.assertIn("--private-users=", " ".join(command))
        self.assertFalse(any("private-users-delegate" in value for value in command))
        self.assertFalse(any(value.startswith("--restrict-address-families=")
                             for value in command))
        self.assertIn(value["digest"], description)
        self.assertFalse(any("RuntimeMaxSec" in item for item in command))

    def test_machine_start_refuses_when_apparmor_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            helper, path = self.policy(Path(directory)); value = json.loads(path.read_text())
            with mock.patch.object(helper, "applied_policy", return_value=(path, value)), \
                    mock.patch.object(helper, "apparmor_loaded", return_value=False), \
                    mock.patch.object(helper, "run_fixed") as run, self.assertRaises(SystemExit):
                helper.machine_start_minimal("sb-0123456789ab", value["digest"])
            run.assert_not_called()

    def test_helper_and_control_plane_compile_identical_apparmor_profiles(self):
        from sandbox.isolation.apparmor import compile_apparmor_profile
        helper = module(); identity = "sb-0123456789ab"; digest = "a" * 64
        self.assertEqual(helper.compile_apparmor_profile(identity, digest),
                         compile_apparmor_profile(identity, digest))

    def test_apparmor_has_root_only_setup_and_irreversible_payload_profiles(self):
        profile = module().compile_apparmor_profile("sb-0123456789ab", "a" * 64)
        # Addressed by full name: `cx` would look for `guest//bwrap`, which
        # does not exist, and the kernel refuses the exec outright.
        self.assertIn("/usr/bin/bwrap px -> sandbox-native-sb-0123456789ab//bwrap",
                      profile)
        self.assertIn("profile bwrap", profile)
        self.assertIn("userns,", profile)
        # The payload is entered by stacking at the final exec, not by a domain
        # transition: bubblewrap sets NoNewPrivileges before exec, and with any
        # `px` rule present the kernel then refuses every exec inside it.
        self.assertNotIn("px -> sandbox-native-sb-0123456789ab//payload", profile)
        self.assertIn("change_profile,", profile)
        self.assertIn("profile payload", profile)
        self.assertIn("deny /run/systemd/**", profile)
        self.assertIn("deny /run/sandbox-native-credentials/**", profile)

    def test_apparmor_install_writes_only_the_fixed_owned_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path = self.policy(root)
            value = json.loads(policy_path.read_text()); calls = []
            with mock.patch.object(helper, "APPARMOR_ROOT", root / "profiles"), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, value)), \
                    mock.patch.object(helper, "apparmor_loaded", return_value=True), \
                    mock.patch.object(helper, "run_fixed",
                                      side_effect=lambda argv, message, **kw: calls.append(argv)):
                helper.apparmor_install("sb-0123456789ab", value["digest"])
            installed = root / "profiles" / "sandbox-native-sb-0123456789ab"
            self.assertEqual(installed.read_text(),
                             helper.compile_apparmor_profile("sb-0123456789ab", value["digest"]))
            self.assertEqual(calls[0][:3],
                             ("apparmor_parser", "--replace", "--skip-cache"))

    def test_apparmor_install_removes_new_not_loaded_profile_without_parser_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path = self.policy(root)
            value = json.loads(policy_path.read_text()); removals = []
            failed = mock.Mock(returncode=1, stdout="", stderr="failed")

            def parser(argv, message, **kwargs):
                raise SystemExit(65)

            def cleanup(argv, **kwargs):
                removals.append(tuple(argv)); return failed

            with mock.patch.object(helper, "APPARMOR_ROOT", root / "profiles"), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, value)), \
                    mock.patch.object(helper, "apparmor_loaded", return_value=False), \
                    mock.patch.object(helper, "_apparmor_loaded_state", return_value=False), \
                    mock.patch.object(helper, "run_fixed", side_effect=parser), \
                    mock.patch.object(helper, "run_optional", side_effect=cleanup), \
                    self.assertRaises(SystemExit):
                helper.apparmor_install("sb-0123456789ab", value["digest"])
            installed = root / "profiles" / "sandbox-native-sb-0123456789ab"
            self.assertFalse(installed.exists())
            self.assertEqual(removals, [])

    def test_policy_remove_recovers_policy_only_after_owner_first_unlink_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, staged = self.policy(root)
            value = json.loads(staged.read_text())
            policies = root / "policies"; owners = root / "owners"
            policies.mkdir(); owners.mkdir()
            policy_path = policies / f"{value['machine_id']}.json"
            owner_path = owners / f"{value['machine_id']}.json"
            policy_path.write_bytes(staged.read_bytes()); policy_path.chmod(0o600)
            owner = {"machine_id": value["machine_id"], "policy_digest": value["digest"],
                     **helper.project_source_identity(value, os.getuid())}
            owner_path.write_text(json.dumps(
                owner, sort_keys=True, separators=(",", ":"),
            ) + "\n"); owner_path.chmod(0o600)
            instance = root / "instance"; image = instance / "root.img"
            real_unlink = helper.Path.unlink

            def fail_policy_unlink(path, *args, **kwargs):
                if path == policy_path:
                    raise OSError("simulated unlink crash")
                return real_unlink(path, *args, **kwargs)

            common = (
                mock.patch.object(helper, "POLICY_ROOT", policies),
                mock.patch.object(helper, "POLICY_OWNER_ROOT", owners),
                mock.patch.object(helper, "APPARMOR_ROOT", root / "apparmor"),
                mock.patch.object(helper, "checked_policy", return_value=(policy_path, value)),
                mock.patch.object(helper, "image_paths",
                                  return_value=(instance, image, instance / "mount")),
                mock.patch.object(helper, "egress_config_record", return_value=None),
                mock.patch.object(helper, "installed_grant_record", return_value=None),
                mock.patch.object(helper, "network_state_record", return_value=None),
                mock.patch.object(helper, "unit_description", return_value=""),
                mock.patch.object(helper, "_apparmor_loaded_state", return_value=False),
                mock.patch.object(helper, "invoking_uid", return_value=os.getuid()),
            )
            with common[0], common[1], common[2], common[3], common[4], common[5], \
                    common[6], common[7], common[8], common[9], common[10], \
                    mock.patch.object(helper.Path, "unlink", autospec=True,
                                      side_effect=fail_policy_unlink), \
                    self.assertRaises(SystemExit):
                helper.policy_remove(value["machine_id"], value["digest"])
            self.assertFalse(owner_path.exists()); self.assertTrue(policy_path.exists())
            with mock.patch.object(helper, "POLICY_ROOT", policies), \
                    mock.patch.object(helper, "POLICY_OWNER_ROOT", owners), \
                    mock.patch.object(helper, "APPARMOR_ROOT", root / "apparmor"), \
                    mock.patch.object(helper, "checked_policy", return_value=(policy_path, value)), \
                    mock.patch.object(helper, "project_source_identity"), \
                    mock.patch.object(helper, "image_paths",
                                      return_value=(instance, image, instance / "mount")), \
                    mock.patch.object(helper, "egress_config_record", return_value=None), \
                    mock.patch.object(helper, "installed_grant_record", return_value=None), \
                    mock.patch.object(helper, "network_state_record", return_value=None), \
                    mock.patch.object(helper, "unit_description", return_value=""), \
                    mock.patch.object(helper, "_apparmor_loaded_state", return_value=False), \
                    mock.patch.object(helper, "invoking_uid", return_value=os.getuid()):
                helper.policy_remove(value["machine_id"], value["digest"])
            self.assertFalse(policy_path.exists())

    def test_install_plan_accepts_only_fixed_digest_path_and_package_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, path, digest, _value = self.install_plan(root)
            with mock.patch.object(helper, "STAGING_ROOT", root):
                _plan, versions = helper.read_install_plan(path, digest)
                self.assertEqual(set(versions), set(helper.HOST_PACKAGE_ROOTS))
                with self.assertRaises(SystemExit):
                    helper.read_install_plan(root / "other.json", digest)

    def test_host_package_apply_uses_only_exact_roots_and_official_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, _path, digest, value = self.install_plan(root)
            source = root / "ubuntu.sources"
            source.write_text("Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\n")
            calls = []
            versions = {name: "1.0" for name in helper.HOST_PACKAGE_ROOTS}
            with mock.patch.object(helper, "OFFICIAL_APT_SOURCE", source), \
                    mock.patch.object(helper, "read_install_plan", return_value=(value, versions)), \
                    mock.patch.object(helper, "run_fixed",
                                      side_effect=lambda argv, message, **kw: calls.append((argv, kw))):
                helper.host_packages_apply("ignored", digest)
            installs = calls[-1][0]
            package_specs = {item.split("=", 1)[0] for item in installs
                             if item.split("=", 1)[0] in helper.HOST_PACKAGE_ROOTS}
            self.assertEqual(package_specs, set(helper.HOST_PACKAGE_ROOTS))
            self.assertIn(f"Dir::Etc::sourcelist={source}", installs)
            self.assertEqual(calls[-1][1]["environment"]["DEBIAN_FRONTEND"], "noninteractive")

    def test_rootfs_writer_rejects_symlink_parent_escape_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / "image"; outside = root / "outside"
            image.mkdir(); outside.mkdir(); (image / "etc").symlink_to(outside)
            helper = module()
            with self.assertRaises(SystemExit):
                helper.write_rootfs(image, "/etc/escaped", "payload")
            self.assertFalse((outside / "escaped").exists())

    def test_helper_and_control_plane_compile_identical_service_files(self):
        from sandbox.runtimes.managed.services import compile_service_files
        helper = module()
        for server in ("nginx", "apache"):
            with self.subTest(server=server):
                self.assertEqual(
                    helper.compile_service_files("10.203.0.2", 512, 3600, server, 8080),
                    compile_service_files("10.203.0.2", 512, 3600,
                                          web_server=server, backend_port=8080),
                )

    def test_credential_install_copies_validated_bytes_without_guest_host_path_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); helper, policy_path = self.policy(root)
            value = json.loads(policy_path.read_text())
            value["credentials"] = ["native/sb-0123456789ab/db-credential"]
            injected = root / "injected" / "sb-0123456789ab"
            injected.mkdir(parents=True, mode=0o700)
            source = injected / "db-credential"; source.write_bytes(b"opaque-secret")
            source.chmod(0o600); calls = []
            result = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(helper, "INJECTED_ROOT", root / "injected"), \
                    mock.patch.object(helper, "RUNTIME_ROOT", root / "runtime"), \
                    mock.patch.object(helper, "applied_policy", return_value=(policy_path, value)), \
                    mock.patch.object(helper, "ensure_root_directory",
                                      side_effect=lambda path, mode: path.mkdir(parents=True,
                                                                               exist_ok=True)), \
                    mock.patch.object(helper, "run_fixed",
                                      side_effect=lambda argv, message, **kw:
                                      calls.append(argv) or result):
                helper.credential_install("sb-0123456789ab", value["digest"],
                                          "db-credential")
            copy = next(argv for argv in calls if argv[:2] == ("machinectl", "copy-to"))
            self.assertEqual(copy[2], "sb-0123456789ab")
            self.assertEqual(copy[-1], "/run/sandbox-native-credentials/db-credential")
            self.assertNotEqual(copy[-2], str(source))
            self.assertFalse(Path(copy[-2]).exists())
            self.assertNotIn("opaque-secret", repr(calls))
            guest = ("systemd-run", "--machine=sb-0123456789ab", "--pipe",
                     "--wait", "--quiet", "--collect")
            self.assertFalse(any(argv[:len(guest)] == guest and str(source) in argv
                                 for argv in calls))
            install_dir = next(argv for argv in calls
                               if argv[:len(guest)] == guest
                               and "/usr/bin/install" in argv)
            self.assertEqual(install_dir[-8:], (
                "-d", "-o", "root", "-g", "root", "-m", "0700",
            ) + ("/run/sandbox-native-credentials",))
            self.assertTrue(any(argv[-3:] == (
                "/usr/bin/chown", "root:www-data",
                "/run/sandbox-native-credentials/db-credential") for argv in calls))
            self.assertTrue(any(argv[-3:] == (
                "/usr/bin/chmod", "0440",
                "/run/sandbox-native-credentials/db-credential") for argv in calls))

    def test_helper_and_control_plane_bind_only_requested_credentials_then_mask_source(self):
        from sandbox.isolation.bubblewrap import BubblewrapCompiler
        helper = module()
        reference = "native/sb-0123456789ab/db-credential"
        policy = {"writable_mounts": (), "credentials": [reference],
                  "machine_id": "sb-0123456789ab"}
        command = ("/usr/local/bin/wp", "core", "is-installed")
        observed = helper.fixed_probe_bwrap(policy, command, (reference,))
        expected = BubblewrapCompiler("/usr/bin/bwrap").argv(
            writable_targets=helper.EXECUTION_WRITABLE_TARGETS,
            credential_names=("db-credential",), command=command,
            payload_profile="sandbox-native-sb-0123456789ab//payload",
        )
        self.assertEqual(observed, expected)
        source = observed.index("/run/sandbox-native-credentials/db-credential")
        source_mask = observed.index("/run/sandbox-native-credentials", source)
        self.assertLess(source, source_mask)
        self.assertEqual(observed[source - 1:source + 2], (
            "--ro-bind", "/run/sandbox-native-credentials/db-credential",
            "/run/credentials/sandbox/db-credential",
        ))

    def test_database_bootstrap_starts_only_socket_database_and_rolls_back_failure(self):
        helper = module(); identity = "sb-0123456789ab"; digest = "a" * 64
        policy = {"credentials": [f"native/{identity}/db-credential"]}
        calls = []
        with mock.patch.object(helper, "applied_policy", return_value=(Path("/policy"), policy)), \
                mock.patch.object(helper, "guest_run",
                                  side_effect=lambda machine, argv, message, **kw:
                                  calls.append(argv) or mock.Mock(returncode=0)):
            helper.database_bootstrap(identity, digest)
        self.assertEqual(calls[0], ("/usr/bin/systemctl", "unmask", "mariadb.service"))
        self.assertIn(("/usr/bin/systemctl", "start", "mariadb.service"), calls)
        self.assertEqual(calls[-1][0], "/usr/local/libexec/sandbox-db-bootstrap")
        self.assertFalse(any("nginx.service" in argv or "apache2.service" in argv
                             for argv in calls))

        optional = []
        with mock.patch.object(helper, "applied_policy", return_value=(Path("/policy"), policy)), \
                mock.patch.object(helper, "guest_run", side_effect=SystemExit(69)), \
                mock.patch.object(helper, "run_optional",
                                  side_effect=lambda argv, **kw: optional.append(argv)):
            with self.assertRaises(SystemExit):
                helper.database_bootstrap(identity, digest)
        self.assertTrue(any(argv[-2:] == ("stop", "mariadb.service") for argv in optional))
        self.assertTrue(any(argv[-2:] == ("mask", "mariadb.service") for argv in optional))
        cleanup_index = next(index for index, argv in enumerate(optional)
                             if "/usr/bin/mariadb" in argv)
        stop_index = next(index for index, argv in enumerate(optional)
                          if argv[-2:] == ("stop", "mariadb.service"))
        self.assertLess(cleanup_index, stop_index)

    def test_database_remove_restarts_owned_stopped_database_then_cleans_and_masks(self):
        helper = module(); identity = "sb-0123456789ab"; digest = "a" * 64
        calls = []; optional = []
        with mock.patch.object(helper, "applied_policy"), \
                mock.patch.object(helper, "guest_run",
                                  side_effect=lambda machine, argv, message, **kw:
                                  calls.append(argv) or mock.Mock(returncode=0)), \
                mock.patch.object(helper, "run_optional",
                                  side_effect=lambda argv, **kw:
                                  optional.append(argv) or mock.Mock(returncode=0)):
            helper.database_remove(identity, digest)
        self.assertEqual(calls[0], ("/usr/bin/systemctl", "unmask", "mariadb.service"))
        self.assertEqual(calls[1], ("/usr/bin/systemctl", "daemon-reload"))
        self.assertEqual(calls[2], ("/usr/bin/systemctl", "start", "mariadb.service"))
        self.assertEqual(calls[3][0], "/usr/bin/mariadb")
        self.assertEqual(optional[-2][-2:], ("stop", "mariadb.service"))
        self.assertEqual(optional[-1][-2:], ("mask", "mariadb.service"))

    def test_service_health_probes_units_socket_database_and_exact_private_backend(self):
        helper = module(); calls = []
        policy = {"network": {"guest_address": "10.203.0.2/30", "ingress_port": 8080}}
        units = ("mariadb.service", "php8.3-fpm.service", "nginx.service", "cron.service")
        with mock.patch.object(helper, "service_plan", return_value=(policy, units)), \
                mock.patch.object(helper, "guest_run",
                                  side_effect=lambda machine, argv, message, **kw:
                                  calls.append(argv) or mock.Mock(returncode=0)):
            helper.services_health("sb-0123456789ab", "a" * 64, "b" * 64)
        self.assertEqual(calls[0], ("/usr/bin/systemctl", "is-active", *units))
        self.assertIn(("/usr/bin/test", "-S", "/run/mysqld/mysqld.sock"), calls)
        self.assertTrue(any(argv[0] == "/usr/bin/mariadb-admin" for argv in calls))
        curl = next(argv for argv in calls if argv[0] == "/usr/bin/curl")
        self.assertEqual(curl[-1], "http://10.203.0.2:8080/")
        self.assertNotIn("--insecure", curl)

    def test_managed_php_extension_allowlist_rejects_foreign_package_metadata(self):
        from sandbox.php_extensions.catalog import DEFAULT_CATALOG
        from sandbox.runtimes.managed.helper import validate_extension_package_allowlist

        row = {
            "name": "php8.3-gd", "version": "8.3.6", "action": "install", "scope": "image",
            "php_extensions": [{
                "name": "gd", "state": "enabled", "version": None,
                "package": "php8.3-gd", "package_version": "8.3.6",
                "catalog_digest": DEFAULT_CATALOG.digest,
                "source": "official-distribution",
            }],
            "extension_catalog": DEFAULT_CATALOG.digest,
            "extension_provenance": "official-distribution",
        }
        plan = type("PackagePlan", (), {"image_packages": (row,)})()
        self.assertTrue(validate_extension_package_allowlist(plan))
        row["php_extensions"][0]["package"] = "apt-foreign"
        with self.assertRaises(ValueError):
            validate_extension_package_allowlist(plan)

    def test_partial_service_activation_is_stopped_and_masked(self):
        helper = module(); optional = []
        units = ("mariadb.service", "php8.3-fpm.service", "nginx.service", "cron.service")
        with mock.patch.object(helper, "service_plan", return_value=({}, units)), \
                mock.patch.object(helper, "guest_run", side_effect=SystemExit(69)), \
                mock.patch.object(helper, "run_optional",
                                  side_effect=lambda argv, **kw: optional.append(argv)):
            with self.assertRaises(SystemExit):
                helper.services_activate("sb-0123456789ab", "a" * 64, "b" * 64)
        self.assertTrue(any("stop" in argv and argv[-4:] == tuple(reversed(units))
                            for argv in optional))
        masked = [argv[-1] for argv in optional if "mask" in argv]
        self.assertEqual(masked, list(units))


if __name__ == "__main__": unittest.main()
