import tempfile
import unittest
from pathlib import Path
import shutil

from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.restore import apply_restore, build_restore_plan
from sandbox.recovery.restore import (ControlPlaneRestoreAdapter, DatabaseRestoreAdapter,
                                      FilesystemRestoreAdapter, GitRestoreAdapter)


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

    def test_invalid_adapter_fails_before_any_operation(self):
        class IncompleteAdapter:
            def __init__(self): self.events = []
            def checkpoint(self): self.events.append("checkpoint")
            def quiesce(self): self.events.append("quiesce")
            def stage(self): self.events.append("stage")
            def swap(self): self.events.append("swap")
            def import_(self): self.events.append("import")
            def verify(self): self.events.append("verify")
            def resume(self): self.events.append("resume")
            def __getattr__(self, name):
                if name == "import":
                    return self.import_
                raise AttributeError(name)

        adapter = IncompleteAdapter()
        plan = type("Plan", (), {"profiles": ("filesystem",)})()
        with self.assertRaisesRegex(RecoveryError, "required operations"):
            apply_restore(plan, {"filesystem": adapter}, confirm=True)
        self.assertEqual(adapter.events, [])

    def test_database_restore_adapter_validates_and_stages_with_callbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); dump = root / "dump.sql"
            dump.write_text("CREATE TABLE example (id INT);\n")
            events = []
            adapter = DatabaseRestoreAdapter(
                "mariadb", dump,
                checkpoint=lambda: events.append("checkpoint") or "snapshot",
                apply=lambda staged: events.append(("apply", staged.read_text())),
                verify=lambda: events.append("verify"),
                rollback=lambda state: events.append(("rollback", state)),
                quiesce=lambda: events.append("quiesce"),
                resume=lambda: events.append("resume"),
            )
            plan = type("Plan", (), {"profiles": ("database",)})()
            result = apply_restore(plan, {"database": adapter}, confirm=True)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(events, ["checkpoint", "quiesce", ("apply", "CREATE TABLE example (id INT);\n"),
                                      "verify", "resume"])

    def test_source_restore_adapters_roll_back_callback_state_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); artifact = root / "artifact.bin"; artifact.write_bytes(b"declaration")
            events = []
            adapter = ControlPlaneRestoreAdapter(
                artifact,
                checkpoint=lambda: "control-checkpoint",
                apply=lambda staged: events.append(("apply", staged.read_bytes())),
                verify=lambda: (_ for _ in ()).throw(RuntimeError("verification failed")),
                rollback=lambda state: events.append(("rollback", state)),
            )
            plan = type("Plan", (), {"profiles": ("control-plane",)})()
            with self.assertRaisesRegex(RecoveryError, "rollback"):
                apply_restore(plan, {"control-plane": adapter}, confirm=True)
            self.assertEqual(events, [("apply", b"declaration"), ("rollback", "control-checkpoint")])

            GitRestoreAdapter(
                artifact,
                checkpoint=lambda: None,
                apply=lambda _staged: None,
                verify=lambda: None,
                rollback=lambda _state: None,
            )

    def test_source_restore_adapter_rejects_artifact_mutation_during_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "artifact.bin"; artifact.write_bytes(b"before")
            adapter = ControlPlaneRestoreAdapter(
                artifact,
                checkpoint=lambda: artifact.write_bytes(b"after!") or "checkpoint",
                apply=lambda _staged: self.fail("apply must not run"),
                verify=lambda: None,
                rollback=lambda _state: None,
            )
            plan = type("Plan", (), {"profiles": ("control-plane",)})()
            with self.assertRaisesRegex(RecoveryError, "changed"):
                apply_restore(plan, {"control-plane": adapter}, confirm=True)
