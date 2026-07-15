import unittest
import json
import tempfile
from pathlib import Path

from sandbox.recovery.catalog import RecoveryCatalog
from sandbox.recovery.capture import CaptureCoordinator
from sandbox.recovery.crypto import FixtureCrypto
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError, result
from sandbox.recovery.service import RecoveryService


class TestRecoveryService(unittest.TestCase):
    def test_result_redacts_recursive_secret_values_and_keys(self):
        payload = result(False, "create", data={"token": "visible", "nested": ["password=visible"]},
                         error=RecoveryError("passphrase=visible", "blocked"))
        rendered = str(payload)
        self.assertNotIn("visible", rendered)
        self.assertEqual(payload["error"]["code"], "blocked")

    def test_unknown_profile_returns_stable_failure_envelope(self):
        payload = RecoveryService(RecoveryCatalog(1, ())).plan(("missing",))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["action"], "plan")
        self.assertEqual(payload["error"]["code"], "unknown_profile")

    def test_create_requires_confirmation_and_list_classifies_pending_objects(self):
        drive = MemoryDrive()
        service = RecoveryService(RecoveryCatalog(1, ()), drive=drive)
        blocked = service.create("set", {}, (), confirm=False)
        self.assertEqual(blocked["error"]["code"], "confirmation_required")
        drive.put("sets/pending/archive.bin", b"cipher")
        listed = service.list()
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["data"]["pending"][0]["Path"], "sets/pending/archive.bin")
        self.assertEqual(listed["data"]["incomplete"][0]["Path"], "sets/pending/archive.bin")

    def test_list_classifies_complete_legacy_unverifiable_and_local_pending_sets(self):
        drive = MemoryDrive()
        CaptureCoordinator(FixtureCrypto(), drive).publish("complete", {"artifact": b"payload"})
        drive.put("sets/partial/archive.bin", b"cipher")
        drive.put("sets/broken/manifest.json", b"not-json")
        drive.put("legacy-backup.tar", b"legacy")
        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / "pending"
            pending.mkdir()
            (pending / "retry.archive.tar.gpg").write_bytes(b"ciphertext")
            listed = RecoveryService(RecoveryCatalog(1, ()), drive=drive, pending_root=pending).list()
        self.assertTrue(listed["ok"])
        self.assertEqual([item["Path"] for item in listed["data"]["complete_manifests"]],
                         ["sets/complete/manifest.json"])
        self.assertEqual([item["Path"] for item in listed["data"]["incomplete"]],
                         ["sets/partial/archive.bin"])
        self.assertEqual([item["Path"] for item in listed["data"]["legacy"]],
                         ["legacy-backup.tar"])
        self.assertEqual([item["Path"] for item in listed["data"]["unverifiable"]],
                         ["sets/broken/manifest.json"])
        self.assertEqual([item["Path"] for item in listed["data"]["locally_pending"]],
                         [str(pending / "retry.archive.tar.gpg")])

    def test_verify_checks_manifest_and_ciphertext(self):
        drive = MemoryDrive()
        CaptureCoordinator(FixtureCrypto(), drive).publish("set-1", {"artifact.txt": b"payload"})
        payload = RecoveryService(RecoveryCatalog(1, ()), drive=drive).verify("set-1")
        self.assertTrue(payload["ok"])
        drive.objects["sets/set-1/archive.bin"] = b"tampered"
        rejected = RecoveryService(RecoveryCatalog(1, ()), drive=drive).verify("set-1")
        self.assertEqual(rejected["error"]["code"], "ciphertext_verification_failed")

    def test_restore_plan_is_non_mutating_and_carries_confirmation_gate(self):
        drive = MemoryDrive(); CaptureCoordinator(FixtureCrypto(), drive).publish("set-1", {"artifact": b"payload"})
        payload = RecoveryService(RecoveryCatalog(1, ()), drive=drive).restore_plan("set-1", ("fixture",))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["requires_confirmation"])
