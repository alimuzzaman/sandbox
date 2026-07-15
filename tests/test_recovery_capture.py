import hashlib
import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.capture import CaptureCoordinator, StagingCaptureCoordinator
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

    def test_existing_complete_set_is_never_overwritten_by_retry(self):
        drive = MemoryDrive(); coordinator = CaptureCoordinator(FixtureCrypto(), drive)
        coordinator.publish("set-1", {"artifact.txt": b"payload"})
        with self.assertRaises(RecoveryError):
            coordinator.publish("set-1", {"artifact.txt": b"changed"})
        self.assertTrue(coordinator.verify("set-1"))

    def test_file_capture_publishes_manifest_last_and_cleans_owner_staging(self):
        class FileCrypto:
            def encrypt_file(self, source, target): Path(target).write_bytes(b"cipher:" + Path(source).read_bytes())
            def verify_file(self, source, target): return hashlib.sha256(Path(source).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "database.dump"; source.write_bytes(b"database")
            drive = MemoryDrive()
            receipt = StagingCaptureCoordinator(FileCrypto(), drive, staging_root=root, clock=lambda: "2026-07-14T00:00:00Z").publish_files(
                "fixture-set", {"database.dump": source}, profiles=("fixture",), provenance={"catalog": "test"},
            )
            self.assertEqual(receipt["profiles"], ["fixture"])
            self.assertIn("sets/fixture-set/manifest.json", drive.objects)
            self.assertEqual(list(root.glob("set-*")), [])

    def test_file_capture_failure_leaves_no_complete_manifest_or_staging(self):
        class BrokenCrypto:
            def encrypt_file(self, source, target): raise RecoveryError("injected", "injected_failure")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "artifact"; source.write_bytes(b"data")
            drive = MemoryDrive()
            with self.assertRaises(RecoveryError):
                StagingCaptureCoordinator(BrokenCrypto(), drive, staging_root=root).publish_files(
                    "fixture-set", {"artifact": source}, profiles=("fixture",))
            self.assertNotIn("sets/fixture-set/manifest.json", drive.objects)
            self.assertEqual(list(root.glob("set-*")), [])

    def test_verified_ciphertext_is_preserved_for_retry_after_remote_failure(self):
        class FileCrypto:
            def encrypt_file(self, source, target): Path(target).write_bytes(b"cipher:" + Path(source).read_bytes())
            def verify_file(self, source, target): return hashlib.sha256(Path(source).read_bytes()).hexdigest()
        class BrokenDrive(MemoryDrive):
            def verify_file(self, key, source): raise RecoveryError("offline", "drive_verification_failed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "artifact"; source.write_bytes(b"data")
            pending = root / "pending"
            with self.assertRaises(RecoveryError):
                StagingCaptureCoordinator(FileCrypto(), BrokenDrive(), staging_root=root,
                                          pending_root=pending).publish_files(
                    "fixture-set", {"artifact": source}, profiles=("fixture",))
            saved = pending / "fixture-set.archive.tar.gpg"
            self.assertTrue(saved.is_file())
            self.assertTrue(saved.read_bytes().startswith(b"cipher:"))
            self.assertEqual(list(root.glob("set-*")), [])

    def test_file_capture_rejects_symlink_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; target = root / "target"
            target.write_bytes(b"data"); source.symlink_to(target)
            with self.assertRaisesRegex(RecoveryError, "unavailable"):
                StagingCaptureCoordinator(FixtureCrypto(), MemoryDrive(), staging_root=root).publish_files(
                    "fixture-set", {"artifact": source}, profiles=("fixture",))

    def test_file_capture_rejects_source_changed_during_archive(self):
        class FileCrypto:
            def encrypt_file(self, source, target): Path(target).write_bytes(Path(source).read_bytes())
            def verify_file(self, source, target): return hashlib.sha256(Path(source).read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "artifact"; source.write_bytes(b"before")
            original_open = __import__("sandbox.recovery.capture", fromlist=["tarfile"]).tarfile.open
            class Archive:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def add(self, source, *, arcname, recursive): source.write_bytes(b"after")
            module = __import__("sandbox.recovery.capture", fromlist=["tarfile"])
            module.tarfile.open = lambda *args, **kwargs: Archive()
            try:
                with self.assertRaisesRegex(RecoveryError, "changed"):
                    StagingCaptureCoordinator(FileCrypto(), MemoryDrive(), staging_root=root).publish_files(
                        "fixture-set", {"artifact": source}, profiles=("fixture",))
            finally:
                module.tarfile.open = original_open
