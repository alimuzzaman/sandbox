import tempfile
import unittest
from pathlib import Path
import shutil

from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.restore import apply_restore, build_restore_plan
from sandbox.recovery.restore import FilesystemRestoreAdapter


class FileSwapAdapter:
    def __init__(self, target: Path, replacement: bytes, *, fail_verify: bool = False) -> None:
        self.target, self.replacement, self.fail_verify = target, replacement, fail_verify
        self.checkpoint_path = target.with_name(target.name + ".checkpoint")
        self.stage_path = target.with_name(target.name + ".stage")
        self.events = []

    def checkpoint(self): self.events.append("checkpoint"); self.checkpoint_path.write_bytes(self.target.read_bytes())
    def quiesce(self): self.events.append("quiesce")
    def stage(self): self.events.append("stage"); self.stage_path.write_bytes(self.replacement)
    def swap(self): self.events.append("swap"); self.stage_path.replace(self.target)
    def import_(self): self.events.append("import")
    def __getattr__(self, name):
        if name == "import":
            return self.import_
        raise AttributeError(name)
    def verify(self):
        self.events.append("verify")
        if self.fail_verify: raise RuntimeError("fixture verification failure")
    def resume(self): self.events.append("resume")
    def rollback(self): self.events.append("rollback"); self.checkpoint_path.replace(self.target)


class TestDisposableRestoreApply(unittest.TestCase):
    def test_filesystem_adapter_restores_and_cleans_checkpoint(self):
        class FileCrypto:
            def decrypt_file(self, source, target):
                shutil.copyfile(source, target)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir()
            (source / "new.txt").write_text("new")
            archive = root / "archive.tar"
            from sandbox.recovery.filesystem import archive_paths
            archive_paths(source, (source / "new.txt",), archive)
            target = root / "target"; target.mkdir(); (target / "old.txt").write_text("old")
            adapter = FilesystemRestoreAdapter(FileCrypto(), archive, target)
            plan = type("Plan", (), {"profiles": ("filesystem",)})()
            result = apply_restore(plan, {"filesystem": adapter}, confirm=True)
            self.assertEqual(result["status"], "complete")
            self.assertTrue((target / "new.txt").is_file())
            self.assertFalse((target / "old.txt").exists())
            self.assertEqual(list(root.glob(".target.recovery-*")), [])

    def test_filesystem_adapter_rolls_back_when_restored_member_is_missing(self):
        class FileCrypto:
            def decrypt_file(self, source, target):
                shutil.copyfile(source, target)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir()
            (source / "new.txt").write_text("new")
            archive = root / "archive.tar"
            from sandbox.recovery.filesystem import archive_paths
            archive_paths(source, (source / "new.txt",), archive)
            target = root / "target"; target.mkdir(); (target / "old.txt").write_text("old")
            class BrokenAdapter(FilesystemRestoreAdapter):
                def verify(self):
                    (self.target / "new.txt").unlink()
                    super().verify()
            adapter = BrokenAdapter(FileCrypto(), archive, target)
            plan = type("Plan", (), {"profiles": ("filesystem",)})()
            with self.assertRaises(RecoveryError):
                apply_restore(plan, {"filesystem": adapter}, confirm=True)
            self.assertEqual((target / "old.txt").read_text(), "old")

    def test_filesystem_adapter_rejects_restored_content_tampering(self):
        class FileCrypto:
            def decrypt_file(self, source, target):
                shutil.copyfile(source, target)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir()
            (source / "new.txt").write_text("new")
            archive = root / "archive.tar"
            from sandbox.recovery.filesystem import archive_paths
            archive_paths(source, (source / "new.txt",), archive)
            target = root / "target"; target.mkdir(); (target / "old.txt").write_text("old")
            class CorruptAdapter(FilesystemRestoreAdapter):
                def verify(self):
                    (self.target / "new.txt").write_text("tampered")
                    super().verify()
            adapter = CorruptAdapter(FileCrypto(), archive, target)
            plan = type("Plan", (), {"profiles": ("filesystem",)})()
            with self.assertRaisesRegex(RecoveryError, "digest"):
                apply_restore(plan, {"filesystem": adapter}, confirm=True)
            self.assertEqual((target / "old.txt").read_text(), "old")

    def test_filesystem_adapter_rejects_symlink_target_before_checkpoint(self):
        class FileCrypto:
            def decrypt_file(self, source, target):
                shutil.copyfile(source, target)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source"; source.mkdir()
            (source / "new.txt").write_text("new")
            archive = root / "archive.tar"
            from sandbox.recovery.filesystem import archive_paths
            archive_paths(source, (source / "new.txt",), archive)
            real_target = root / "real-target"; real_target.mkdir()
            target = root / "target"; target.symlink_to(real_target, target_is_directory=True)
            adapter = FilesystemRestoreAdapter(FileCrypto(), archive, target)
            with self.assertRaisesRegex(RecoveryError, "target"):
                adapter.checkpoint()
            self.assertTrue(target.is_symlink())
            self.assertFalse(list(root.glob(".target.recovery-*")))

    def test_failed_later_profile_restores_prior_file_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); first = root / "first"; second = root / "second"
            first.write_bytes(b"before-first"); second.write_bytes(b"before-second")
            drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("fixture-set", {"data": b"fixture"})
            plan = build_restore_plan(drive, "fixture-set", ("first", "second"))
            adapters = {"first": FileSwapAdapter(first, b"after-first"),
                        "second": FileSwapAdapter(second, b"after-second", fail_verify=True)}
            with self.assertRaisesRegex(RecoveryError, "rollback"):
                apply_restore(plan, adapters, confirm=True)
            self.assertEqual(first.read_bytes(), b"before-first")
            self.assertIn("rollback", adapters["first"].events)
            self.assertEqual(second.read_bytes(), b"before-second")
            self.assertIn("rollback", adapters["second"].events)
