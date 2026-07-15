import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    def test_create_validates_catalog_and_materialization_before_capture(self):
        class Capture:
            def publish_files(self, *args, **kwargs):
                raise AssertionError("capture must not run")
        service = RecoveryService(RecoveryCatalog(1, ()), capture=Capture())
        unknown = service.create("set", {"artifact": "/tmp/artifact"}, ("missing",), confirm=True)
        self.assertEqual(unknown["error"]["code"], "unknown_profile")

        from sandbox.recovery.models import RecoveryProfile
        profile = RecoveryProfile("fixture", "test", "filesystem", ("host-manifest:fixture",),
                                  ("source",), "full", "stable", (), "encrypted", "target",
                                  "verify", "standard")
        service = RecoveryService(RecoveryCatalog(1, (profile,)), capture=Capture())
        blocked = service.create("set", {"artifact": "/tmp/artifact"}, ("fixture",), confirm=True)
        self.assertEqual(blocked["error"]["code"], "capture_not_ready")
        self.assertIn("warnings", blocked["data"])

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

    def test_context_composes_capture_only_with_destination_and_secret_channel(self):
        from sandbox.recovery import context
        with patch.dict("os.environ", {
            "RECOVERY_RCLONE_DESTINATION": "gdrive:recovery",
            "RECOVERY_PASSPHRASE": "fixture-secret",
            "RECOVERY_STAGING_ROOT": "/tmp/recovery-stage",
            "RECOVERY_PENDING_ROOT": "/tmp/recovery-pending",
        }, clear=True), patch.object(context, "RcloneDrive") as drive_cls, \
                patch.object(context, "GpgCrypto") as crypto_cls, \
                patch.object(context, "StagingCaptureCoordinator") as capture_cls:
            service = context.recovery_service(Path(__file__).parents[1])
        self.assertIsNotNone(service.drive)
        self.assertIsNotNone(service.capture)
        drive_cls.assert_called_once()
        crypto_cls.assert_called_once_with("fixture-secret")
        capture_cls.assert_called_once()

    def test_context_leaves_capture_unconfigured_without_secret_channel(self):
        from sandbox.recovery import context
        with patch.dict("os.environ", {
            "RECOVERY_RCLONE_DESTINATION": "gdrive:recovery",
        }, clear=True):
            with patch.object(context, "StagingCaptureCoordinator") as capture_cls:
                service = context.recovery_service(Path(__file__).parents[1])
        self.assertIsNone(service.capture)
        capture_cls.assert_not_called()
