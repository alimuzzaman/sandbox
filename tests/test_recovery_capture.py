import hashlib
import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.filesystem import archive_paths


class TestRecoveryCapture(unittest.TestCase):
    def test_archive_rejects_paths_outside_declared_root(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); allowed = root / "allowed"; allowed.mkdir(); (allowed / "ok.txt").write_text("ok")
            with self.assertRaises(RecoveryError):
                archive_paths(allowed, (root / "outside",), root / "artifact.tar")

    def test_capture_publishes_ciphertext_before_complete_manifest(self):
        drive = MemoryDrive(); coordinator = CaptureCoordinator(FixtureCrypto(), drive)
        receipt = coordinator.publish("set-1", {"artifact.txt": b"payload"})
        self.assertEqual(receipt["status"], "complete")
        self.assertIn("sets/set-1/archive.bin", drive.objects)
        self.assertIn("sets/set-1/manifest.json", drive.objects)
        self.assertTrue(coordinator.verify("set-1"))

    def test_failed_ciphertext_verification_never_publishes_manifest(self):
        class BrokenDrive(MemoryDrive):
            def get(self, key): return b"different"
        drive = BrokenDrive(); coordinator = CaptureCoordinator(FixtureCrypto(), drive)
        with self.assertRaises(RecoveryError):
            coordinator.publish("set-1", {"artifact.txt": b"payload"})
        self.assertNotIn("sets/set-1/manifest.json", drive.objects)
