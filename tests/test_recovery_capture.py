import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_capture_rejects_malformed_artifacts_with_stable_error(self):
        coordinator = CaptureCoordinator(FixtureCrypto(), MemoryDrive())
        for artifacts in ({"artifact": b""}, {"../outside": b"payload"}, {"artifact": "payload"}, None):
            with self.subTest(artifacts=artifacts):
                with self.assertRaisesRegex(RecoveryError, "artifact|artifacts"):
                    coordinator.publish("set-1", artifacts)

    def test_capture_verify_returns_false_for_malformed_manifest(self):
        drive = MemoryDrive()
        drive.put("sets/set-1/manifest.json", b"not-json")
        self.assertFalse(CaptureCoordinator(FixtureCrypto(), drive).verify("set-1"))
        self.assertFalse(CaptureCoordinator(FixtureCrypto(), drive).verify("../outside"))

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

    def test_file_capture_rejects_non_string_set_and_artifact_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.write_bytes(b"data")
            coordinator = StagingCaptureCoordinator(FixtureCrypto(), MemoryDrive(), staging_root=root)
            with self.assertRaisesRegex(RecoveryError, "invalid"):
                coordinator.publish_files(123, {"artifact": source}, profiles=("fixture",))
            with self.assertRaisesRegex(RecoveryError, "invalid"):
                coordinator.publish_files("fixture-set", {123: source}, profiles=("fixture",))

    def test_file_capture_rejects_malformed_profiles_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.write_bytes(b"data")
            coordinator = StagingCaptureCoordinator(FixtureCrypto(), MemoryDrive(), staging_root=root)
            with self.assertRaisesRegex(RecoveryError, "requires artifacts and profiles"):
                coordinator.publish_files("fixture-set", {"artifact": source}, profiles=["fixture"])
            with self.assertRaisesRegex(RecoveryError, "provenance"):
                coordinator.publish_files("fixture-set", {"artifact": source},
                                          profiles=("fixture",), provenance=[])

    def test_file_capture_detects_content_change_even_when_metadata_is_restored(self):
        class FileCrypto:
            def encrypt_file(self, source, target): Path(target).write_bytes(Path(source).read_bytes())
            def verify_file(self, source, target): return hashlib.sha256(Path(source).read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "artifact"; source.write_bytes(b"before")
            original_open = __import__("sandbox.recovery.capture", fromlist=["tarfile"]).tarfile.open
            class Archive:
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def add(self, source, *, arcname, recursive):
                    metadata = source.stat()
                    source.write_bytes(b"after!")
                    os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            module = __import__("sandbox.recovery.capture", fromlist=["tarfile"])
            module.tarfile.open = lambda *args, **kwargs: Archive()
            try:
                with self.assertRaisesRegex(RecoveryError, "changed"):
                    StagingCaptureCoordinator(FileCrypto(), MemoryDrive(), staging_root=root).publish_files(
                        "fixture-set", {"artifact": source}, profiles=("fixture",))
            finally:
                module.tarfile.open = original_open

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

    def test_manifest_digest_uses_verified_source_snapshot(self):
        class FileCrypto:
            def encrypt_file(self, source, target): Path(target).write_bytes(Path(source).read_bytes())
            def verify_file(self, source, target): return hashlib.sha256(Path(source).read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "artifact"; source.write_bytes(b"data")
            module = __import__("sandbox.recovery.capture", fromlist=["sha256_file"])
            original = module.sha256_file
            calls = 0

            def snapshot_digest(path):
                nonlocal calls
                if Path(path) == source:
                    calls += 1
                    return "a" * 64 if calls <= 2 else "b" * 64
                return original(path)

            with mock.patch.object(module, "sha256_file", side_effect=snapshot_digest):
                receipt = StagingCaptureCoordinator(FileCrypto(), MemoryDrive(), staging_root=root).publish_files(
                    "fixture-set", {"artifact": source}, profiles=("fixture",))
            self.assertEqual(receipt["artifacts"][0]["sha256"], "a" * 64)
