import unittest
import hashlib
import json
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

    def test_restore_rejects_cross_set_ciphertext_reference(self):
        drive = MemoryDrive()
        drive.put("sets/set-b/archive.bin", b"valid")
        drive.put("sets/set-a/manifest.json", json.dumps({
            "schema_version": 1, "id": "set-a", "status": "complete",
            "ciphertext_object": "sets/set-b/archive.bin",
            "ciphertext_sha256": hashlib.sha256(b"valid").hexdigest(), "ciphertext_size": 5,
        }).encode())
        with self.assertRaises(RecoveryError) as caught:
            build_restore_plan(drive, "set-a")
        self.assertEqual(caught.exception.code, "invalid_manifest")

    def test_restore_rejects_non_object_manifest(self):
        drive = MemoryDrive()
        drive.put("sets/set-1/manifest.json", b"[]")
        with self.assertRaisesRegex(RecoveryError, "manifest"):
            build_restore_plan(drive, "set-1")

    def test_restore_rejects_non_text_manifest_payload(self):
        drive = MemoryDrive()
        drive.objects["sets/set-1/manifest.json"] = None
        with self.assertRaisesRegex(RecoveryError, "manifest"):
            build_restore_plan(drive, "set-1")

    def test_restore_rejects_malformed_ciphertext_metadata(self):
        drive = MemoryDrive()
        drive.put("sets/set-1/archive.bin", b"valid")
        manifest = {
            "schema_version": 1, "id": "set-1", "status": "complete",
            "ciphertext_object": "sets/set-1/archive.bin",
            "ciphertext_sha256": ["not-a-digest"], "ciphertext_size": "5",
        }
        drive.put("sets/set-1/manifest.json", json.dumps(manifest).encode())
        with self.assertRaisesRegex(RecoveryError, "fields"):
            build_restore_plan(drive, "set-1")

    def test_restore_rejects_incomplete_staged_manifest(self):
        drive = MemoryDrive(); ciphertext = b"valid"
        drive.put("sets/set-1/archive.tar.gpg", ciphertext)
        drive.put("sets/set-1/manifest.json", json.dumps({
            "schema_version": 1, "id": "set-1", "status": "complete",
            "restore_compatibility": "sandbox-recovery-v1", "profiles": ["fixture"],
            "artifacts": [{"name": "artifact", "sha256": "bad", "size": 4}],
            "ciphertext_object": "sets/set-1/archive.tar.gpg",
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(), "ciphertext_size": len(ciphertext),
        }).encode())
        with self.assertRaisesRegex(RecoveryError, "artifact"):
            build_restore_plan(drive, "set-1")

    def test_restore_rejects_unsafe_staged_artifact_name(self):
        drive = MemoryDrive(); ciphertext = b"valid"
        drive.put("sets/set-unsafe/archive.tar.gpg", ciphertext)
        drive.put("sets/set-unsafe/manifest.json", json.dumps({
            "schema_version": 1, "id": "set-unsafe", "status": "complete",
            "restore_compatibility": "sandbox-recovery-v1", "profiles": ["fixture"],
            "artifacts": [{"name": "../artifact", "sha256": hashlib.sha256(b"x").hexdigest(), "size": 1}],
            "ciphertext_object": "sets/set-unsafe/archive.tar.gpg",
            "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(), "ciphertext_size": len(ciphertext),
        }).encode())
        with self.assertRaisesRegex(RecoveryError, "artifact"):
            build_restore_plan(drive, "set-unsafe")

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
        self.assertIn("rollback:two", events)
        self.assertIn("resume:two", events)
        self.assertIn("rollback:one", events)
