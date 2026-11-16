"""A source lock a lost sync left: recorded, then released only on a settled receipt.

No real SSH. The remote coordinator protocol is the receipt model shared with
tests/test_remote_materialization_completion.py.
"""
import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.commands import remote as remote_cmd
from sandbox.core import _remote
from sandbox.core.remote_materialization import RemoteMaterializationBusy, RemoteMaterializationUnknown
from tests.test_remote_materialization_completion import ReceiptTransport

TARGET = "/home/owner/sandbox/deploy-src/project"
REMOTE = {"ssh": "owner@host", "_remote_name": "box"}


class _Base(unittest.TestCase):
    def setUp(self):
        runtime = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(_remote, "RUNTIME_DIR", runtime))
        self.transport = ReceiptTransport()
        self.enterContext(patch.object(_remote, "ssh_run", self.transport))

    def lose_a_sync(self):
        """A sync whose SSH session is lost mid-step: completion unknown, lock kept."""
        self.transport.disconnect = True
        with self.assertRaises(RemoteMaterializationUnknown):
            with _remote._remote_materialization_lock(REMOTE, TARGET) as lease:
                lease.run("git reset --hard", timeout=1)
        self.transport.disconnect = False
        return self.transport.record["operation_id"]


class JobStartReconcilesItsOwnSettledOperationTests(_Base):
    def test_the_lost_operation_is_recorded(self):
        operation = self.lose_a_sync()
        self.assertEqual(_remote.recorded_materialization_operations(REMOTE), {TARGET: operation})

    def test_a_completed_receipt_is_released_and_the_next_sync_proceeds(self):
        operation = self.lose_a_sync()
        self.transport.record.update(state="complete", exit_code=0)  # the remote finished
        with _remote._remote_materialization_lock(REMOTE, TARGET) as lease:
            self.assertNotEqual(lease.operation_id, operation)
        self.assertIsNone(self.transport.record)
        self.assertEqual(_remote.recorded_materialization_operations(REMOTE), {})
        # busy acquire, inspect, reconcile (inspect + release), acquire, own release
        self.assertEqual(self.transport.calls[-6:],
                         ["acquire", "inspect", "inspect", "release", "acquire", "release"])

    def test_a_still_active_operation_stays_busy_and_names_itself(self):
        operation = self.lose_a_sync()  # receipt still says active
        with self.assertRaises(RemoteMaterializationBusy) as raised:
            with _remote._remote_materialization_lock(REMOTE, TARGET):
                pass
        self.assertIn(operation, str(raised.exception))
        self.assertIn("sb remote materialization inspect box", str(raised.exception))
        self.assertEqual(self.transport.record["state"], "active")
        self.assertNotIn("release", self.transport.calls)
        self.assertEqual(_remote.recorded_materialization_operations(REMOTE), {TARGET: operation})

    def test_another_controllers_lock_is_never_released(self):
        # Busy with no operation of ours recorded: refuse without touching it.
        self.transport.record = dict(schema_version=1, operation_id="f" * 32, state="complete",
                                     step=1, exit_code=0)
        with self.assertRaises(RemoteMaterializationBusy):
            with _remote._remote_materialization_lock(REMOTE, TARGET):
                pass
        self.assertEqual(self.transport.calls, ["acquire"])
        self.assertEqual(self.transport.record["operation_id"], "f" * 32)


class MaterializationCommandTests(_Base):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(_remote, "get_remote", return_value={"ssh": "owner@host"}))
        self.enterContext(patch.object(_remote, "deploy_target_path", return_value=TARGET))

    def command(self, action, **extra):
        args = argparse.Namespace(action="materialization", name=action, ssh_url="box",
                                  project_dir="/tmp", operation_id=None, json=True, **extra)
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                remote_cmd._cmd_materialization(args, True)
            except SystemExit as exc:
                code = exc.code
        return code, json.loads(out.getvalue())

    def test_inspect_then_reconcile_a_completed_recorded_operation(self):
        operation = self.lose_a_sync()
        self.transport.record.update(state="complete", exit_code=0)
        code, payload = self.command("inspect")
        self.assertEqual((code, payload["state"], payload["operation_id"]), (0, "complete", operation))
        self.assertIsNotNone(self.transport.record)  # inspect changes nothing
        code, payload = self.command("reconcile")
        self.assertEqual((code, payload["state"]), (0, "released"))
        self.assertIsNone(self.transport.record)
        self.assertEqual(_remote.recorded_materialization_operations(REMOTE), {})

    def test_reconcile_refuses_an_active_receipt(self):
        self.lose_a_sync()
        code, payload = self.command("reconcile")
        self.assertEqual((code, payload["error"]["code"]), (1, "remote_materialization_unsettled"))
        self.assertEqual(self.transport.record["state"], "active")

    def test_an_explicit_operation_id_works_without_a_record(self):
        # The hand case (op b75a3243): another checkout's sync, id from its error.
        self.transport.record = dict(schema_version=1, operation_id="b" * 32, state="complete",
                                     step=2, exit_code=0)
        args = argparse.Namespace(action="materialization", name="reconcile", ssh_url="box",
                                  project_dir="/tmp", operation_id="b" * 32, json=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            remote_cmd._cmd_materialization(args, True)
        self.assertEqual(json.loads(out.getvalue())["state"], "released")
        self.assertIsNone(self.transport.record)

    def test_a_wrong_operation_id_releases_nothing(self):
        self.transport.record = dict(schema_version=1, operation_id="b" * 32, state="complete",
                                     step=2, exit_code=0)
        args = argparse.Namespace(action="materialization", name="reconcile", ssh_url="box",
                                  project_dir="/tmp", operation_id="c" * 32, json=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit):
            remote_cmd._cmd_materialization(args, True)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "remote_materialization_unknown")
        self.assertEqual(self.transport.record["operation_id"], "b" * 32)


if __name__ == "__main__":
    unittest.main()
