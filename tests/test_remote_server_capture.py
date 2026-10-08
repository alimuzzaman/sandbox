"""Spec 058 registered transport for the server capture helper (T009, T017)."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest

from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.hosted import HostedRecoveryMaterializer
from sandbox.recovery.materialize import SourceBinding
from sandbox.recovery.planner import build_plan
from sandbox.recovery.server_capture import ServerCaptureService, slot_for
from sandbox.recovery.server_capture import capture_request_id
from sandbox.transports.remote_server_capture import HELPER_PATH, RegisteredServerCaptureTransport
from tests.server_capture_support import (
    SECRET, HelperHarness, LocalSsh, declarations_bytes, local_transport, request_for,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(ROOT / "config" / "recovery-profiles.json")


def _canned(stdout: bytes, code: int = 0):
    calls = []

    def ssh(_entry, command, *, input_data=None, timeout=30):
        calls.append(command)
        return subprocess.CompletedProcess([], code, stdout, b"")

    return ssh, calls


def _transport(ssh):
    return RegisteredServerCaptureTransport(
        remote_lookup=lambda name: {"provisioned": True, "name": name},
        ssh_run=lambda *_a, **_k: subprocess.CompletedProcess([], 0, "host\n", ""),
        ssh_process=ssh, resolve_home=lambda _entry: "/home/remote/sandbox",
        service_status=lambda _entry: {}, inventory=lambda _remote: {})


class TestTransportInvocation(unittest.TestCase):
    """T009: delivery, argv, bounded parsing and error mapping."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.h = HelperHarness(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def test_helper_is_sent_as_python_c_with_fixed_args_only(self):
        transport, ssh = local_transport(self.h)
        with self.assertRaises(RecoveryError) as caught:
            transport.status("fixture-remote", slot_for("fixture-remote", "set-a"))
        self.assertEqual(caught.exception.code, "capture_not_found")
        argv = shlex.split(ssh.calls[0][0])
        self.assertEqual(argv[:3], ["python3", "-c", HELPER_PATH.read_text()])
        self.assertEqual(argv[3:], ["status", str(self.h.root), slot_for("fixture-remote", "set-a")])

    def test_ok_false_codes_map_to_typed_errors_without_raw_text(self):
        ssh, _calls = _canned(b'{"ok": false, "code": "capture_in_progress", "active_backup_id": "x", "path": "/secret"}')
        with self.assertRaises(RecoveryError) as caught:
            _transport(ssh).list("fixture-remote")
        self.assertEqual(caught.exception.code, "capture_in_progress")
        self.assertEqual(caught.exception.data, {"active_backup_id": "x"})
        self.assertNotIn("/secret", str(caught.exception))
        ssh, _calls = _canned(b'{"ok": false, "code": "Bad Code; rm -rf"}')
        with self.assertRaises(RecoveryError) as caught:
            _transport(ssh).list("fixture-remote")
        self.assertEqual(caught.exception.code, "server_capture_failed")

    def test_invalid_oversized_or_failed_output_is_remote_unavailable(self):
        for stdout, code in ((b"not json", 0), (b"[1]", 0), (b"{" + b" " * (3 * 1024 * 1024) + b"}", 0),
                             (b'{"ok": true}', 255), (b"", 0)):
            with self.subTest(stdout=stdout[:20], code=code):
                ssh, _calls = _canned(stdout, code)
                with self.assertRaises(RecoveryError) as caught:
                    _transport(ssh).list("fixture-remote")
                self.assertEqual(caught.exception.code, "remote_unavailable")

    def test_timeout_is_remote_unavailable_and_never_replayed(self):
        calls = []

        def ssh(_entry, command, *, input_data=None, timeout=30):
            calls.append(command)
            raise subprocess.TimeoutExpired(command, timeout)

        with self.assertRaises(RecoveryError) as caught:
            _transport(ssh).start("fixture-remote", slot_for("fixture-remote", "set-a"),
                                  request_for("fixture-remote", "set-a"), SECRET, b"{}\n")
        self.assertEqual(caught.exception.code, "remote_unavailable")
        self.assertEqual(len(calls), 1)

    def test_unprovisioned_or_invalid_remote_is_remote_unavailable(self):
        transport, ssh = local_transport(self.h, provisioned=False)
        for remote in ("fixture-remote", "bad name;"):
            with self.subTest(remote=remote):
                with self.assertRaises(RecoveryError) as caught:
                    transport.list(remote)
                self.assertEqual(caught.exception.code, "remote_unavailable")
        self.assertEqual(ssh.calls, [])

    def test_transport_never_uses_remote_ssh_command_or_job_apis(self):
        source = (ROOT / "sandbox" / "transports" / "remote_server_capture.py").read_text()
        service = (ROOT / "sandbox" / "recovery" / "server_capture.py").read_text()
        for text in (source, service, HELPER_PATH.read_text()):
            for forbidden in ("sandbox.commands.remote", "cmd_remote", "job-start",
                              "remote_jobs", "job_start", '"ssh", ', "'ssh', "):
                self.assertFalse(forbidden in text, forbidden)

    def test_runtime_revision_must_match_for_observe(self):
        for state in ("mismatch", "unknown"):
            with self.subTest(state=state):
                transport, _ssh = local_transport(self.h, revision_state=state)
                with self.assertRaises(RecoveryError) as caught:
                    transport.observe("fixture-remote")
                self.assertEqual(caught.exception.code, "remote_runtime_stale")
        transport, _ssh = local_transport(self.h)
        self.assertEqual(transport.observe("fixture-remote").revision_state, "match")

    def test_read_chunk_rejects_a_header_that_does_not_match_the_bytes(self):
        header = json.dumps({"ok": True, "offset": 0, "length": 3, "sha256": "0" * 64}).encode()
        ssh, _calls = _canned(header + b"\nabc")
        with self.assertRaises(RecoveryError) as caught:
            _transport(ssh).read_chunk("fixture-remote", slot_for("fixture-remote", "set-a"), 0, 3)
        self.assertEqual(caught.exception.code, "transfer_mismatch")
        with self.assertRaises(RecoveryError) as caught:
            _transport(ssh).read_chunk("fixture-remote", slot_for("fixture-remote", "set-a"), 0,
                                       16 * 1024 * 1024 + 1)
        self.assertEqual(caught.exception.code, "request_invalid")

    def test_retire_plan_uses_a_closed_candidate_frame(self):
        payload = json.dumps({"ok": True, "state": "failed", "receipt_sha256": None,
                              "archive_sha256": "a" * 64, "archive_size": 17}).encode()
        ssh, _calls = _canned(payload)
        candidate = _transport(ssh).retire_plan(
            "fixture-remote", slot_for("fixture-remote", "set-a"))
        self.assertEqual(candidate, {
            "state": "failed", "receipt_sha256": None,
            "archive_sha256": "a" * 64, "archive_size": 17,
        })
        ssh, _calls = _canned(payload[:-1] + b', "unexpected": true}')
        with self.assertRaises(RecoveryError) as caught:
            _transport(ssh).retire_plan("fixture-remote", slot_for("fixture-remote", "set-a"))
        self.assertEqual(caught.exception.code, "record_invalid")

    def test_retire_requires_a_valid_mutation_ack_and_warns_to_inspect_status(self):
        candidate = {"state": "promoted", "receipt_sha256": "b" * 64,
                     "archive_sha256": "a" * 64, "archive_size": 17}
        ssh, _calls = _canned(b'{"ok":true,"retired_at":123.5,"removed_bytes":17}')
        self.assertEqual(_transport(ssh).retire(
            "fixture-remote", slot_for("fixture-remote", "set-a"), candidate),
            {"retired_at": 123.5, "removed_bytes": 17})
        for response in (b'{"ok":true}',
                         b'{"ok":true,"retired_at":123.5,"removed_bytes":true}',
                         b'{"ok":true,"retired_at":NaN,"removed_bytes":17}',
                         b'{"ok":true,"retired_at":123.5,"removed_bytes":17,"path":"/private"}'):
            with self.subTest(response=response):
                ssh, calls = _canned(response)
                with self.assertRaises(RecoveryError) as caught:
                    _transport(ssh).retire(
                        "fixture-remote", slot_for("fixture-remote", "set-a"), candidate)
                self.assertEqual(caught.exception.code, "record_invalid")
                self.assertIn("inspect status before any retry", str(caught.exception))
                self.assertEqual(len(calls), 1)


class TestTransportStart(unittest.TestCase):
    """T017: brokered stdin, request identity and no archive bytes during capture."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.h = HelperHarness(self._directory.name)

    def tearDown(self):
        if self.h.slot_path().exists():
            self.h.wait()
        self._directory.cleanup()

    def test_start_sends_password_then_declarations_on_stdin_only(self):
        transport, ssh = local_transport(self.h)
        declarations = declarations_bytes()
        request = request_for("fixture-remote", "set-a", declarations=declarations)
        outcome = transport.start("fixture-remote", request["slot"], request, SECRET, declarations)
        self.assertTrue(outcome["ok"])
        command, stdin = ssh.calls[0]
        self.assertEqual(stdin, SECRET.encode() + b"\n" + declarations)
        self.assertNotIn(SECRET, command)
        self.assertIn(json.dumps(request, sort_keys=True, separators=(",", ":")), shlex.split(command))

    def test_service_start_uses_the_hosted_request_id_and_reads_no_archive(self):
        transport, ssh = local_transport(self.h)
        service = ServerCaptureService(
            CATALOG, transport, environment={"SANDBOX_RECOVERY_DB_PASSWORD": SECRET},
            config={}, state_root=Path(self._directory.name) / "operator")
        started = time.monotonic()
        outcome = service.start("fixture-remote", "set-a", ["amarsonar-bangla-prod"], confirm=True)
        self.assertLess(time.monotonic() - started, 30)
        self.assertTrue(outcome["ok"], outcome)
        self.assertIn(outcome["status"], {"queued", "running"})
        plan = build_plan(CATALOG, ("amarsonar-bangla-prod",))
        source = transport.observe("fixture-remote")
        binding = SourceBinding("fixture-remote", source.machine_identity, source.revision,
                                source.source_digest)
        artifact = next(item for item in plan.artifacts
                        if item.profile_id == "amarsonar-bangla-prod")
        control = next(item for item in plan.artifacts if item.profile_id == "control-plane")
        declarations = (json.dumps(transport.declaration(
            "fixture-remote", control, source, "set-a"),
            sort_keys=True, separators=(",", ":")) + "\n").encode()
        base_request_id = HostedRecoveryMaterializer._request_id(
            "fixture-remote", artifact, binding, "set-a")
        expected = capture_request_id(base_request_id, hashlib.sha256(declarations).hexdigest())
        self.assertEqual(outcome["data"]["request_id"], expected)
        stored = json.loads((self.h.slot_path() / "request.json").read_text())
        self.assertEqual(stored["request_id"], expected)
        self.assertEqual(ssh.ops, ["list", "start"])
        self.assertNotIn("read-chunk", ssh.ops)
        self.assertNotIn("read-receipt", ssh.ops)
        self.assertTrue(all(SECRET not in command for command, _stdin in ssh.calls))


if __name__ == "__main__":
    unittest.main()
