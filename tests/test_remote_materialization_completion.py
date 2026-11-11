"""Source-only definitions. No real SSH, Docker, or provider data.

Linux-only cases execute the standalone coordinator locally, including a real
orphaned descendant. Other cases exercise production controller receipt parsing.
"""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from sandbox.core import _remote
from sandbox.core.remote_materialization import (
    RemoteMaterializationBusy, RemoteMaterializationLease,
    RemoteMaterializationUnknown,
)
from tests.subprocess_support import run_test_process, synthetic_environment


class ReceiptTransport:
    """Model the remote protocol, including an active disconnected writer."""
    def __init__(self):
        self.record = None
        self.calls = []
        self.disconnect = False
        self.code = 0

    def __call__(self, remote, command, **kwargs):
        args = shlex.split(command)
        action, parent, name, token = args[3:7]
        self.calls.append(action)
        if action == "acquire":
            if self.record is not None:
                return subprocess.CompletedProcess(command, 73, b"", b"")
            self.record = dict(schema_version=1, operation_id=token, state="idle", step=0)
        elif self.record is None or self.record["operation_id"] != token:
            return subprocess.CompletedProcess(command, 70, b"", b"")
        elif action == "run":
            self.record.update(state="active", step=self.record["step"] + 1,
                               intent_digest=args[8])
            if self.disconnect:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            self.record.update(state="complete", exit_code=self.code)
        elif action == "release":
            if self.record["state"] not in ("idle", "complete"):
                return subprocess.CompletedProcess(command, 70, b"", b"")
            value = dict(self.record, state="released")
            self.record = None
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        return subprocess.CompletedProcess(command, 0, json.dumps(self.record), "")


class RemoteMaterializationCompletionTest(unittest.TestCase):
    def test_disconnect_retains_original_lock_and_excludes_second_writer(self):
        transport = ReceiptTransport()
        transport.disconnect = True
        with patch.object(_remote, "ssh_run", side_effect=transport):
            with self.assertRaises(RemoteMaterializationUnknown) as failure:
                _remote.reset_target_to({}, "/srv/source", "a" * 40)
            original = failure.exception.operation_id
            self.assertEqual(transport.record["operation_id"], original)
            self.assertEqual(transport.record["state"], "active")
            self.assertNotIn("release", transport.calls)
            with self.assertRaises(RemoteMaterializationBusy):
                _remote.reset_target_to({}, "/srv/source", "b" * 40)
            with self.assertRaises(RemoteMaterializationUnknown):
                _remote.reconcile_remote_materialization({}, "/srv/source", original)
            self.assertEqual(transport.record["operation_id"], original)
            # Only the original coordinator's cessation proof changes state.
            transport.record.update(state="complete", exit_code=0)
            evidence = _remote.inspect_remote_materialization({}, "/srv/source", original)
            self.assertEqual(evidence["state"], "complete")
            self.assertIsNotNone(transport.record)  # inspection never unlocks
            _remote.reconcile_remote_materialization({}, "/srv/source", original)
            self.assertIsNone(transport.record)
            self.assertEqual(transport.calls.count("run"), 1)  # no replay

    def test_known_success_and_known_failure_release(self):
        for code in (0, 1, 124, -9):
            with self.subTest(code=code):
                transport = ReceiptTransport()
                transport.code = code
                with patch.object(_remote, "ssh_run", side_effect=transport):
                    if code:
                        with self.assertRaises(RuntimeError):
                            _remote.reset_target_to({}, "/srv/source", "a" * 40)
                    else:
                        _remote.reset_target_to({}, "/srv/source", "a" * 40)
                self.assertIsNone(transport.record)
                self.assertEqual(transport.calls, ["acquire", "run", "release"])

    def test_blank_ssh_success_is_not_cessation_proof(self):
        transport = ReceiptTransport()
        lease = RemoteMaterializationLease({}, "/srv/source", transport).acquire()
        lease.transport = lambda *a, **k: subprocess.CompletedProcess([], 0, "", "")
        with self.assertRaises(RemoteMaterializationUnknown):
            lease.run("git reset --hard fixture", timeout=1)
        self.assertTrue(lease.unknown)
        self.assertIsNotNone(transport.record)

    def test_wrong_original_operation_cannot_reconcile(self):
        transport = ReceiptTransport()
        lease = RemoteMaterializationLease({}, "/srv/source", transport).acquire()
        wrong = RemoteMaterializationLease({}, "/srv/source", transport,
                                           operation_id="0" * 32)
        with self.assertRaises(RemoteMaterializationUnknown):
            wrong.reconcile()
        self.assertEqual(transport.record["operation_id"], lease.operation_id)
        self.assertNotIn("release", transport.calls)

    def test_interrupted_dispatch_does_not_send_release(self):
        transport = ReceiptTransport()
        def interrupted(*args, **kwargs):
            if shlex.split(args[1])[3] == "run":
                transport.disconnect = True
                try:
                    transport(*args, **kwargs)
                except subprocess.TimeoutExpired:
                    raise KeyboardInterrupt from None
            return transport(*args, **kwargs)
        with patch.object(_remote, "ssh_run", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                _remote.reset_target_to({}, "/srv/source", "a" * 40)
        self.assertEqual(transport.record["state"], "active")
        self.assertNotIn("release", transport.calls)

    def test_wrong_step_or_intent_receipt_cannot_release(self):
        for field, wrong in (("step", 20), ("intent_digest", "f" * 64)):
            with self.subTest(field=field):
                transport = ReceiptTransport()
                lease = RemoteMaterializationLease({}, "/srv/source", transport).acquire()
                def corrupt(*args, **kwargs):
                    result = transport(*args, **kwargs)
                    value = json.loads(result.stdout)
                    value[field] = wrong
                    result.stdout = json.dumps(value)
                    return result
                lease.transport = corrupt
                with self.assertRaises(RemoteMaterializationUnknown):
                    lease.run("true", timeout=1)
                with self.assertRaises(RemoteMaterializationUnknown):
                    lease.release()
                self.assertNotIn("release", transport.calls)

    def test_overlay_uses_same_coordinator_and_payload_stays_on_stdin(self):
        transport = ReceiptTransport()
        calls = []
        def record(*args, **kwargs):
            calls.append((args, kwargs))
            return transport(*args, **kwargs)
        payload = b"fixture-private-overlay-bytes"
        with patch.object(_remote, "ssh_run", side_effect=record):
            self.assertEqual(_remote.update_target_to(
                {}, "/srv/source", "a" * 40, project_root="/unused",
                overlay_snapshot={"deleted": ["old.txt"], "archive": payload, "files": 1},
            ), 2)
        self.assertEqual(transport.calls, ["acquire", "run", "run", "run", "release"])
        archive_call = next(item for item in calls if item[1].get("input_data"))
        self.assertEqual(archive_call[1]["input_data"], payload)
        self.assertNotIn(payload.decode(), archive_call[0][1])


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux subreaper contract")
class RealCoordinatorCompletionTest(unittest.TestCase):
    def test_disconnect_with_live_escaped_descendant_keeps_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            target = str(Path(directory) / "source")
            gate = Path(directory) / "finish"
            started = Path(directory) / "started"
            original = []
            def local_transport(_remote_record, command, **kwargs):
                args = shlex.split(command)
                args[0] = sys.executable
                if args[3] == "run":
                    process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        env=synthetic_environment())
                    original.append(process)
                    deadline = time.monotonic() + 3
                    while not started.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(started.exists(), "descendant must be alive before disconnect")
                    try:
                        stdout, stderr = process.communicate(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        # Deliberately leave the remote coordinator/descendant
                        # alive. Killing a local SSH client is not termination.
                        raise
                    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
                return run_test_process(args, timeout=3, capture_output=True)
            lease = RemoteMaterializationLease({}, target, local_transport).acquire()
            child = ("import os,time; os.setsid(); "
                     f"open({str(started)!r},'w').close(); "
                     f"p={str(gate)!r}; "
                     "exec('while not os.path.exists(p): time.sleep(0.01)')")
            launch = "import subprocess; subprocess.Popen(" + repr([sys.executable, "-c", child]) + ")"
            command = shlex.join([sys.executable, "-c", launch])
            try:
                with self.assertRaises(RemoteMaterializationUnknown):
                    lease.run(command, timeout=1)
                self.assertIsNone(original[0].poll())
                with self.assertRaises(RemoteMaterializationBusy):
                    RemoteMaterializationLease({}, target, local_transport).acquire()
                recovery = RemoteMaterializationLease({}, target, local_transport,
                                                       operation_id=lease.operation_id)
                with self.assertRaises(RemoteMaterializationUnknown):
                    recovery.reconcile()
            finally:
                gate.touch()
                for process in original:
                    process.communicate(timeout=5)
            recovery = RemoteMaterializationLease({}, target, local_transport,
                                                   operation_id=lease.operation_id)
            self.assertEqual(recovery.inspect()["state"], "complete")
            recovery.reconcile()
            RemoteMaterializationLease({}, target, local_transport).acquire().release()

    def test_real_success_and_failure_have_cessation_receipt(self):
        def transport(_record, command, **kwargs):
            args = shlex.split(command)
            args[0] = sys.executable
            return run_test_process(args, timeout=3, capture_output=True)
        with tempfile.TemporaryDirectory() as directory:
            for code in (0, 7):
                with self.subTest(code=code):
                    lease = RemoteMaterializationLease({}, str(Path(directory) / "source"), transport).acquire()
                    self.assertEqual(lease.run(f"exit {code}", timeout=3).returncode, code)
                    self.assertEqual(lease.release()["state"], "released")
