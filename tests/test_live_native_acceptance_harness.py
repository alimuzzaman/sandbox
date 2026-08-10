from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "live_native_acceptance.py"
SPEC = importlib.util.spec_from_file_location("live_native_acceptance", HARNESS)
live = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = live
SPEC.loader.exec_module(live)


class LiveNativeAcceptanceHarnessTest(unittest.TestCase):
    def test_authoritative_entry_and_resource_inventories_are_complete(self):
        self.assertEqual(set(live.ENTRY_PATHS), {
            "web", "cron", "wordpress_cli", "exec", "composer",
            "plugin_activation", "durable_job", "phpunit",
        })
        self.assertEqual(set(live.RESOURCE_PROBES), {
            "cpu", "memory", "pids", "runtime", "disk", "inodes", "fds",
            "connections", "io",
        })
        # Measured on a host where preflight actually probes. The former 3.0 came
        # from macOS, where the managed runtime is unsupported and the command
        # short-circuits, so it timed a check that never ran.
        self.assertEqual(live.PREFLIGHT_LIMIT_SECONDS, 6.0)
        self.assertEqual(live.STATUS_LIMIT_SECONDS, 3.0)
        self.assertEqual(live.WARM_START_LIMIT_SECONDS, 20.0)

    def test_runner_always_uses_sb_and_bounds_retained_output(self):
        observed = []

        def fake_run(argv, **kwargs):
            observed.append((argv, kwargs))
            return subprocess.CompletedProcess(
                argv, 0, stdout='x' * (live.MAX_CAPTURE_BYTES + 7) + '\n{"ok":true}\n', stderr='safe',
            )

        events = []
        event = live.SbRunner(events=events, run=fake_run).call(
            None, "native", "support", "--json", timeout=3,
        )
        self.assertEqual(Path(observed[0][0][0]), live.SB)
        self.assertEqual(observed[0][0][1:], ["native", "support", "--json"])
        self.assertTrue(observed[0][1]["capture_output"])
        self.assertEqual(observed[0][1]["timeout"], 3)
        self.assertTrue(event["stdout"]["truncated"])
        self.assertLessEqual(len(event["stdout"]["text"].encode()), live.MAX_CAPTURE_BYTES)
        self.assertEqual(event["json"], {"ok": True})

    def test_boundary_requires_every_hostile_observation_to_be_denied(self):
        denied = {
            key: False for key in (
                "source_write", "symlink_escape", "sibling_source_read", "host_home_read",
                "host_control_read", "host_process_visible", "host_process_signal",
                "sibling_process_visible", "sibling_process_signal", "host_ipc_visible",
                "sibling_ipc_visible", "device_open", "control_socket_open", "raw_socket",
                "new_user_namespace", "metadata_reachable", "private_reachable",
                "host_veth_reachable", "sibling_address_reachable",
                "public_reachable", "credential_read",
            )
        }
        denied["instance_db_socket"] = True
        denied["host_veth_target"] = "10.203.0.1"
        denied["host_veth_port"] = 31001
        denied["sibling_address_target"] = "10.203.0.6"
        denied["effective_uid"] = 33
        self.assertTrue(live._boundary_ok(denied))
        denied["sibling_source_read"] = True
        self.assertFalse(live._boundary_ok(denied))

    def test_network_targets_come_from_observed_backends(self):
        targets = live._observed_network_targets(
            {"address": "10.203.7.6", "port": 18080},
            {"address": "10.203.8.10", "port": 18081},
        )
        self.assertEqual(targets, ("10.203.7.5", "10.203.8.10", 18081))
        command = live._boundary_command(
            100, 200, Path("/sibling"), 300, targets[0], 31001,
            targets[1], targets[2], 400,
        )
        self.assertEqual(command[command.index("--host-veth") + 1], "10.203.7.5")
        self.assertEqual(command[command.index("--host-veth-port") + 1], "31001")
        self.assertEqual(command[command.index("--sibling-address") + 1], "10.203.8.10")
        self.assertEqual(command[command.index("--sibling-port") + 1], "18081")
        self.assertNotIn("10.0.0.1", command)

    def test_host_veth_sentinel_is_live_and_closes(self):
        sentinel = live.HostTcpSentinel("127.0.0.1")
        self.assertTrue(sentinel.active)
        with socket.create_connection((sentinel.address, sentinel.port), timeout=1):
            pass
        port = sentinel.port
        sentinel.close()
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=.2)

    def test_candidate_opt_in_rejects_conflict_and_records_exact_authority(self):
        environment = {}
        self.assertEqual(live._enable_proof_candidate(environment), live.PROOF_CANDIDATE)
        self.assertEqual(environment["SANDBOX_NATIVE_PROOF_CANDIDATE"], live.PROOF_CANDIDATE)
        exact = {"SANDBOX_NATIVE_PROOF_CANDIDATE": live.PROOF_CANDIDATE}
        self.assertEqual(live._enable_proof_candidate(exact), live.PROOF_CANDIDATE)
        self.assertTrue(live._candidate_result({"json": {
            "proof_candidate": True, "adoptable": False,
        }}))
        self.assertFalse(live._candidate_result({"json": {
            "proof_candidate": True, "adoptable": True,
        }}))
        with self.assertRaises(ValueError):
            live._enable_proof_candidate({"SANDBOX_NATIVE_PROOF_CANDIDATE": "forged"})

    def test_provenance_records_exact_source_identity_and_durable_jobs(self):
        observed = []

        def fake_run(argv, **kwargs):
            observed.append((argv, kwargs))
            if argv[-2:] == ("rev-parse", "HEAD"):
                return subprocess.CompletedProcess(argv, 0, stdout="a" * 40 + "\n", stderr="")
            if argv[-2:] == ("status", "--porcelain=v1"):
                return subprocess.CompletedProcess(argv, 0, stdout=" M local-change\n", stderr="")
            self.fail(f"unexpected provenance command: {argv}")

        identity = live._source_identity(ROOT, run=fake_run)
        self.assertEqual(identity["revision"], "a" * 40)
        self.assertFalse(identity["worktree_clean"])
        self.assertEqual(len(identity["harness_sha256"]), 64)
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(call[1]["timeout"] == 5 for call in observed))
        self.assertEqual(live._durable_job_ids((
            {"json": {"job_id": "job-b"}}, {"json": {"job_id": "job-a"}},
            {"json": {"job_id": "job-b"}}, {"json": {"job_id": 7}}, {},
        )), ("job-a", "job-b"))

    def test_io_proof_rejects_post_hoc_only_liveness(self):
        evidence = dict(
            started={"phase": "started", "resource": "io", "io_weight": "default 100"},
            progress={"phase": "progress", "resource": "io", "write_bytes_delta": 4096},
            peer_live=True, terminal_lifecycle="cancelled", cancelled=True,
            cleaned=True, expected_weight=100,
        )
        self.assertFalse(live._io_concurrent_evidence(
            lifecycle_during_probe="succeeded", **evidence,
        ))
        self.assertTrue(live._io_concurrent_evidence(
            lifecycle_during_probe="running", **evidence,
        ))

    def test_phpunit_uses_readonly_host_context_and_full_boundary_argv(self):
        harness = HARNESS.read_text()
        phpunit = (ROOT / "tests" / "fixtures" / "native-wordpress" / "tests"
                   / "NativeBoundaryTest.php").read_text()
        self.assertIn("os.O_EXCL | os.O_CLOEXEC, 0o444", harness)
        self.assertIn("/.sandbox-native-proof-context.json", phpunit)
        for flag in (
            "--host-pid", "--sibling-pid", "--sibling-root", "--host-home",
            "--host-ipc", "--sibling-ipc", "--host-veth", "--host-veth-port",
            "--sibling-address", "--sibling-port",
        ):
            self.assertIn(flag, phpunit)
        for field in (
            "source_write", "symlink_escape", "sibling_source_read", "host_home_read",
            "host_process_visible", "sibling_process_visible", "host_ipc_visible",
            "sibling_ipc_visible", "host_veth_reachable", "sibling_address_reachable",
        ):
            self.assertIn(field, phpunit)

    def test_resource_evidence_rejects_generic_failures_and_missing_deltas(self):
        generic_failure = {"returncode": 1, "stdout": {"text": "", "bytes": 0}}
        observer = {"json": {"stdout": json.dumps({"memory_events": {"oom_kill": 1}})}}
        self.assertFalse(live._resource_evidence("memory", generic_failure, observer))

        cpu_without_delta = {"returncode": 0, "json": {"stdout": "\n".join((
            json.dumps({"phase": "started", "resource": "cpu", "cpu_max": "100000 100000"}),
            json.dumps({"phase": "result", "resource": "cpu",
                        "nr_throttled_delta": 0, "throttled_usec_delta": 0}),
        ))}}
        self.assertFalse(live._resource_evidence("cpu", cpu_without_delta, {"json": {}}))

        pids_without_pcntl = {"returncode": 0, "json": {"stdout": "\n".join((
            json.dumps({"phase": "started", "resource": "pids", "pcntl": False}),
            json.dumps({"phase": "result", "resource": "pids", "forked": 1,
                        "fork_failures": 1, "pids_max_events_delta": 1}),
        ))}}
        self.assertFalse(live._resource_evidence("pids", pids_without_pcntl, {"json": {}}))

        wrong_disk = {"returncode": 0, "json": {"stdout": "\n".join((
            json.dumps({"phase": "started", "resource": "disk"}),
            json.dumps({"phase": "result", "resource": "disk", "path": "/tmp",
                        "write_failed": True, "bytes_written": 1}),
        ))}}
        self.assertFalse(live._resource_evidence("disk", wrong_disk, {"json": {}}))

    def test_connection_probe_uses_observed_backend_address_and_port(self):
        calls = []

        class Runner:
            def call(self, project, *args, **kwargs):
                calls.append(args)
                if args[0] == "exec":
                    return {"returncode": 1, "json": {"stdout": ""},
                            "stdout": {"text": "", "bytes": 0}}
                return {"returncode": 0, "json": {"ok": True}}

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config = Path(temporary.name) / "config.json"
        config.write_text(json.dumps({"wordpressRuntime": {"resources": {"connections": 128}}}))
        project = live.Project(Path("/primary"), "default", "nginx", config)
        sibling = live.Project(Path("/sibling"), "default", "apache", config)
        live._resource_matrix(Runner(), project, sibling,
                              {"address": "10.203.0.2", "port": 18080})
        connection = next(argv for argv in calls if "resource_connections" not in argv
                          and "connections" in argv and "resource-observe" not in argv)
        self.assertIn("10.203.0.2", connection)
        self.assertIn("18080", connection)
        positive = {"returncode": 0, "json": {"stdout": "\n".join((
            json.dumps({"phase": "started", "resource": "connections",
                        "backend_address": "10.203.0.2", "backend_port": 18080,
                        "connection_limit": 128, "backend_connected": True}),
            json.dumps({"phase": "result", "resource": "connections",
                        "connection_failed": True, "held_connections": 129}),
        ))}}
        self.assertTrue(live._resource_evidence("connections", positive, {"json": {}}))

    def test_cleanup_failure_requires_attributed_fault_and_verified_retry(self):
        arbitrary = {"json": {"ok": False, "state": "cleanup_incomplete",
                              "reason": {"code": "isolation_drift"}}}
        self.assertFalse(live._cleanup_check(arbitrary, "drift"))
        attributed = {"json": {**arbitrary["json"], "acceptance_fault": {
            "kind": "owned_drift", "owner_match": True,
            "retained_state_verified": True, "restored": True, "retry_ok": True,
        }}}
        self.assertTrue(live._cleanup_check(attributed, "drift"))

    def test_runtime_grant_edit_is_atomic_and_restorable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "sandbox.config.default.json"
            original = {
                "wordpressRuntime": {
                    "mode": "managed-native", "adapter": "ubuntu-nspawn",
                    "webServer": "nginx", "egress": [],
                }
            }
            config.write_text(json.dumps(original))
            project = live.Project(root, "default", "nginx", config)
            editor = live.RuntimeConfigGrant(project)
            editor.set([{"grant_id": "exact"}])
            self.assertEqual(json.loads(config.read_text())["wordpressRuntime"]["egress"],
                             [{"grant_id": "exact"}])
            editor.restore()
            self.assertEqual(config.read_bytes(), json.dumps(original).encode())
            self.assertFalse((root / "sandbox.config.default.json.acceptance.tmp").exists())

    def test_harness_has_no_runtime_adapter_or_host_control_import(self):
        source = HARNESS.read_text()
        self.assertNotIn("sandbox.application.context", source)
        self.assertNotIn("sandbox.runtimes", source)
        self.assertNotIn("machinectl", source.replace("machinectl,", ""))
        self.assertNotIn("docker compose", source.lower())
        self.assertNotIn("systemctl", source)
        self.assertNotIn('("cron", "event", "run"', source)


if __name__ == "__main__":
    unittest.main()
