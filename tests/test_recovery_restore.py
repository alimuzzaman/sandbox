import unittest
from unittest.mock import patch

from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.restore import apply_restore, build_restore_plan


class TestRecoveryRestore(unittest.TestCase):
    def test_complete_manifest_has_non_mutating_checkpointed_plan(self):
        drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("set-1", {"a": b"b"})
        plan = build_restore_plan(drive, "set-1", ("fixture",))
        self.assertTrue(plan.requires_confirmation)
        self.assertEqual(plan.checkpoints, ("checkpoint:fixture",))

    def test_incomplete_manifest_is_rejected(self):
        drive = MemoryDrive(); drive.put("sets/set-1/manifest.json", b'{"schema_version": 1, "status": "incomplete"}')
        with self.assertRaises(RecoveryError): build_restore_plan(drive, "set-1")

    def test_plan_rejects_bad_hash_compatibility_space_and_dependencies(self):
        drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("set-1", {"a": b"b"})
        drive.objects["sets/set-1/archive.bin"] = b"changed"
        with self.assertRaisesRegex(RecoveryError, "does not match"):
            build_restore_plan(drive, "set-1", ("fixture",))
        drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("set-2", {"a": b"b"})
        import json
        manifest = json.loads(drive.get("sets/set-2/manifest.json")); manifest["restore_compatibility"] = "future"
        drive.objects["sets/set-2/manifest.json"] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(RecoveryError, "incompatible"):
            build_restore_plan(drive, "set-2", ("fixture",))
        manifest.pop("restore_compatibility")
        drive.objects["sets/set-2/manifest.json"] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(RecoveryError, "dependency"):
            build_restore_plan(drive, "set-2", ("fixture",), dependencies={"fixture": ("base",)})
        with patch("sandbox.recovery.restore.shutil.disk_usage", return_value=type("Usage", (), {"free": 1})()):
            with self.assertRaisesRegex(RecoveryError, "free space"):
                build_restore_plan(drive, "set-2", ("fixture",), target_root="/", required_bytes=2)

    def test_apply_orders_operations_and_rolls_back_completed_profiles(self):
        events = []
        class Adapter:
            def __init__(self, name, fail=False): self.name, self.fail = name, fail
            def __getattr__(self, operation):
                def run():
                    events.append(f"{operation}:{self.name}")
                    if self.fail and operation == "verify": raise RuntimeError("injected")
                return run
        drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("set-3", {"a": b"b"})
        plan = build_restore_plan(drive, "set-3", ("one", "two"))
        with self.assertRaisesRegex(RecoveryError, "rollback"):
            apply_restore(plan, {"one": Adapter("one"), "two": Adapter("two", True)}, confirm=True)
        self.assertEqual(events[:7], [f"{op}:one" for op in ("checkpoint", "quiesce", "stage", "swap", "import", "verify", "resume")])
        self.assertIn("rollback:one", events)
