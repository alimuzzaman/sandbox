import unittest

from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.restore import build_restore_plan


class TestRecoveryRestore(unittest.TestCase):
    def test_complete_manifest_has_non_mutating_checkpointed_plan(self):
        drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("set-1", {"a": b"b"})
        plan = build_restore_plan(drive, "set-1", ("fixture",))
        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.checkpoints, ("checkpoint:fixture",))

    def test_incomplete_manifest_is_rejected(self):
        drive = MemoryDrive(); drive.put("sets/set-1/manifest.json", b'{"schema_version": 1, "status": "incomplete"}')
        with self.assertRaises(RecoveryError): build_restore_plan(drive, "set-1")
