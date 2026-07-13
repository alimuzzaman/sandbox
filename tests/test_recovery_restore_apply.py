import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.restore import apply_restore, build_restore_plan


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
            self.assertEqual(second.read_bytes(), b"after-second")
