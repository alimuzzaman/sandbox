"""Spec 058 promote: gates (T032), resumable transfer (T033), publication (T034)
and finishing a locally pending ciphertext (T041)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sandbox.recovery import server_capture as module
from sandbox.recovery.capture import StagingCaptureCoordinator
from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.drive import MemoryDrive
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.restore import verify_manifest
from sandbox.recovery.server_capture import (
    ARCHIVE_ARTIFACT, DECLARATION_ARTIFACT, ServerCaptureService, slot_for,
)
from tests.server_capture_support import FakeTransport, TaggedFileCrypto, fake_facts

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog(ROOT / "config" / "recovery-profiles.json")
REMOTE = "fixture-remote"
PASSPHRASE = "fixture-passphrase"
ARCHIVE = b"server-archive-bytes-0123456789"
SLOT = slot_for(REMOTE, "set-a")


class LoggingDrive(MemoryDrive):
    def __init__(self) -> None:
        super().__init__()
        self.log: list[tuple[str, str]] = []
        self.fail: dict[str, str] = {}

    def _check(self, op, key):
        self.log.append((op, key))
        if op in self.fail:
            raise RecoveryError("injected", self.fail.pop(op))

    def put(self, key, payload):
        self._check("put", key)
        super().put(key, payload)

    def put_file(self, key, source):
        self._check("put_file", key)
        MemoryDrive.put(self, key, Path(source).read_bytes())

    def verify_file(self, key, source):
        self._check("verify_file", key)
        super().verify_file(key, source)


class PromoteCase(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.base = Path(self._directory.name)
        self.materialized = self.base / "materialized"
        self.materialized.mkdir(mode=0o700)
        self.drive = LoggingDrive()
        self.transport = FakeTransport()
        self.chunk = mock.patch.object(module, "CHUNK_BYTES", 8)
        self.chunk.start()
        self.addCleanup(self.chunk.stop)

    def tearDown(self):
        self._directory.cleanup()

    def capture(self, passphrase=PASSPHRASE):
        return StagingCaptureCoordinator(
            TaggedFileCrypto(passphrase), self.drive, staging_root=self.base / "staging",
            pending_root=self.base / "pending", materialization_root=self.materialized,
            clock=lambda: "2026-10-08T00:00:00Z")

    def service(self, *, environment=None, drive=True, passphrase=PASSPHRASE, now=1_000.0):
        environment = {"RECOVERY_PASSPHRASE": passphrase} if environment is None else environment
        return ServerCaptureService(
            CATALOG, self.transport, environment=environment, config={}, clock=lambda: now,
            state_root=self.base / "operator", drive=self.drive if drive else None,
            capture=self.capture(passphrase) if drive else None)

    def add(self, **kwargs):
        archive = kwargs.pop("archive", ARCHIVE)
        return self.transport.add(fake_facts("set-a", archive=archive, **kwargs), archive=archive)

    def promote(self, **kwargs):
        return self.service(**kwargs).promote(REMOTE, "set-a", confirm=True)

    def part(self):
        return self.base / "operator" / "promote" / SLOT / "archive.part"


class TestPromoteGates(PromoteCase):
    """T032: contract order; no refusal transfers a byte."""

    def tearDown(self):
        self.assertNotIn("read_chunk", self.transport.calls)
        super().tearDown()

    def test_confirmation_passphrase_and_destination(self):
        self.add()
        outcome = self.service().promote(REMOTE, "set-a", confirm=False)
        self.assertEqual(outcome["error"]["code"], "confirmation_required")
        outcome = self.promote(environment={})
        self.assertEqual(outcome["error"]["code"], "missing_passphrase")
        outcome = self.promote(drive=False)
        self.assertEqual(outcome["error"]["code"], "recovery_not_configured")
        self.assertEqual(self.transport.calls, [])

    def test_set_id_conflict_and_incomplete_remote_set(self):
        self.add()
        other = self.materialized / "other"
        other.mkdir()
        (other / "file").write_bytes(b"other")
        self.capture().publish_files("set-a", {"control-plane/file": other / "file"},
                                     profiles=("control-plane",))
        uploads = len(self.drive.objects)
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "set_id_conflict")
        self.assertEqual(len(self.drive.objects), uploads)
        self.drive.objects.clear()
        self.drive.objects["sets/set-a/archive.tar.gpg"] = b"partial"
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "incomplete_remote_set")

    def test_capture_not_complete(self):
        for kwargs, state in ((dict(state="failed", receipt_valid=False), "failed"),
                              (dict(state="running", lock_free=True, receipt_valid=False),
                               "incomplete"),
                              (dict(state="running", lock_free=False, receipt_valid=False),
                               "running"),
                              (dict(state="complete", receipt_valid=False), "incomplete")):
            with self.subTest(state=state):
                self.add(**kwargs)
                outcome = self.promote()
                self.assertEqual(outcome["error"]["code"], "capture_not_complete")
                self.assertEqual(outcome["data"]["state"], state)

    def test_operator_space_needs_three_times_the_archive(self):
        self.add()
        usage = mock.Mock(free=3 * len(ARCHIVE) - 1)
        with mock.patch("sandbox.recovery.server_capture.shutil.disk_usage", return_value=usage):
            outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "insufficient_space")
        self.assertEqual(outcome["data"]["need_bytes"], 3 * len(ARCHIVE))
        self.assertEqual(outcome["data"]["shortfall_bytes"], 1)
        self.assertEqual(self.drive.objects, {})


class TestPromoteTransfer(PromoteCase):
    """T033: chunked, resumable, verified transfer."""

    def test_chunks_land_owner_only_and_resume_after_interruption(self):
        self.add()
        offsets, modes = [], []

        def hook(offset, length):
            offsets.append((offset, length))
            if self.part().exists():
                modes.append((self.part().stat().st_mode & 0o777,
                              self.part().parent.stat().st_mode & 0o777))
            if offset == 16 and len(offsets) == 3:
                raise RecoveryError("dropped", "remote_unavailable")

        self.transport.chunk_hook = hook
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "remote_unavailable")
        self.assertEqual(outcome["data"]["local_transfer_bytes"], 16)
        self.assertEqual(set(modes), {(0o600, 0o700)})
        self.assertNotIn("sets/set-a/manifest.json", self.drive.objects)
        status = self.service().status(REMOTE, "set-a")
        self.assertEqual(status["data"]["local_transfer_bytes"], 16)
        offsets.clear()
        outcome = self.promote()
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(outcome["data"]["resumed_from_bytes"], 16)
        self.assertEqual(offsets, [(16, 8), (24, len(ARCHIVE) - 24)])
        self.assertTrue(all(length <= 8 for _offset, length in offsets))

    def test_progress_for_another_request_or_hash_is_discarded(self):
        self.add()
        directory = self.part().parent
        directory.mkdir(parents=True, mode=0o700)
        self.part().write_bytes(ARCHIVE[:16])
        for progress in ({"request_id": "recovery-other"}, {"archive_sha256": "0" * 64}):
            with self.subTest(progress=progress):
                facts = self.transport.slots[SLOT]
                record = {"request_id": facts["request"]["request_id"],
                          "archive_sha256": hashlib.sha256(ARCHIVE).hexdigest(),
                          "archive_size": len(ARCHIVE), "received": 16}
                record.update(progress)
                directory.mkdir(parents=True, mode=0o700, exist_ok=True)
                self.part().write_bytes(b"X" * 16)
                (directory / "progress.json").write_text(json.dumps(record))
                self.drive.objects.clear()
                self.transport.slots[SLOT]["promoted"] = None
                outcome = self.promote()
                self.assertTrue(outcome["ok"], outcome)
                self.assertEqual(outcome["data"]["resumed_from_bytes"], 0)

    def test_final_hash_mismatch_removes_partial_and_fails_the_capture(self):
        facts = self.add()
        self.transport.archives[SLOT] = b"tampered-archive-bytes-01234567"
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "transfer_mismatch")
        self.assertFalse(self.part().exists())
        self.assertNotIn("local_transfer_bytes", outcome["data"])
        self.assertEqual(self.drive.objects, {})
        self.assertIn("check_integrity", self.transport.calls)
        self.assertEqual(facts["state"]["state"], "failed")
        self.assertEqual(facts["state"]["reason"], "integrity_mismatch")
        self.assertEqual(self.transport.marked, [])

    def test_declaration_mismatch_publishes_nothing(self):
        self.add()
        self.transport.declarations[SLOT] = "{}\n"
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "transfer_mismatch")
        self.assertEqual(self.drive.objects, {})


class TestPromotePublication(PromoteCase):
    """T034: publish_files once, manifest last, provenance, mark after verify."""

    def test_publication_order_provenance_and_cleanup(self):
        facts = self.add(completed_at=0.0)
        capture = self.capture()
        calls = []
        original = capture.publish_files

        def publish(set_id, artifacts, **kwargs):
            calls.append((set_id, dict(artifacts), kwargs))
            for path in artifacts.values():
                Path(path).resolve().relative_to(self.materialized.resolve())
            return original(set_id, artifacts, **kwargs)

        capture.publish_files = publish
        marks = []
        original_mark = self.transport.mark_promoted

        def mark(remote, slot, marker):
            verify_manifest(self.drive, "set-a")
            marks.append(marker)
            return original_mark(remote, slot, marker)

        self.transport.mark_promoted = mark
        service = ServerCaptureService(
            CATALOG, self.transport, environment={"RECOVERY_PASSPHRASE": PASSPHRASE}, config={},
            clock=lambda: 30 * 86400.0, state_root=self.base / "operator", drive=self.drive,
            capture=capture)
        self.assertTrue(service.status(REMOTE, "set-a")["data"]["retention_exceeded"])
        outcome = service.promote(REMOTE, "set-a", confirm=True)
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(outcome["status"], "published")
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0][1]), {ARCHIVE_ARTIFACT, DECLARATION_ARTIFACT})
        self.assertEqual([op for op, _key in self.drive.log],
                         ["put_file", "verify_file", "put"])
        self.assertEqual(self.drive.log[-1][1], "sets/set-a/manifest.json")
        manifest = verify_manifest(self.drive, "set-a")
        records = {item["name"]: item for item in manifest["artifacts"]}
        digest = hashlib.sha256(ARCHIVE).hexdigest()
        self.assertEqual(records[ARCHIVE_ARTIFACT]["sha256"], digest)
        server = manifest["provenance"]["server_capture"]
        self.assertEqual(set(server), {"schema_version", "request_id", "backup_operation_id",
                                       "archive_sha256", "archive_size", "members",
                                       "declarations_sha256", "inventory_summary",
                                       "captured_at", "promoted_at"})
        self.assertEqual(server["request_id"], facts["request"]["request_id"])
        self.assertEqual(server["archive_sha256"], digest)
        self.assertEqual(server["archive_size"], len(ARCHIVE))
        self.assertEqual(manifest["provenance"]["capture_contract_version"], 2)
        self.assertEqual(manifest["provenance"]["backup_operation_id"], "set-a")
        self.assertEqual(len(marks), 1)
        self.assertTrue(outcome["data"]["server_marked"])
        self.assertFalse(self.part().parent.exists())
        self.assertEqual(list(self.materialized.iterdir()), [])
        self.assertNotIn("retire", self.transport.calls)
        again = self.promote()
        self.assertEqual(again["status"], "already_published")
        self.assertEqual(len([op for op, _ in self.drive.log if op.startswith("put")]), 2)

    def test_publication_failure_keeps_transfer_and_reports_it(self):
        self.add()
        self.drive.fail["put_file"] = "drive_upload_failed"
        outcome = self.service().promote(REMOTE, "set-a", confirm=True)
        self.assertEqual(outcome["error"]["code"], "drive_upload_failed")
        self.assertEqual(outcome["data"]["local_transfer_bytes"], len(ARCHIVE))
        self.assertEqual(self.transport.marked, [])
        self.assertEqual(list(self.materialized.iterdir()), [])


class TestPendingPromote(PromoteCase):
    """T041: a locally pending verified ciphertext is finished first."""

    def make_pending(self):
        self.add()
        self.drive.fail["put"] = "drive_upload_failed"
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "drive_upload_failed")
        pending = self.base / "pending"
        cipher = pending / "set-a.archive.tar.gpg"
        self.assertTrue(cipher.is_file())
        self.assertTrue((pending / "set-a.manifest.json").is_file())
        return cipher, pending / "set-a.manifest.json"

    def offline(self):
        for name in ("observe", "list", "status", "read_receipt", "read_chunk",
                     "read_declaration", "mark_promoted"):
            self.transport.errors[name] = RecoveryError("offline", "remote_unavailable")

    def test_sidecar_path_uploads_same_bytes_and_removes_both_files(self):
        cipher, sidecar = self.make_pending()
        payload = cipher.read_bytes()
        self.offline()
        self.drive.log.clear()
        outcome = self.promote()
        self.assertTrue(outcome["ok"], outcome)
        self.assertTrue(outcome["data"]["from_pending"])
        self.assertFalse(outcome["data"]["server_marked"])
        self.assertEqual(self.drive.objects["sets/set-a/archive.tar.gpg"], payload)
        self.assertEqual([op for op, _key in self.drive.log], ["put_file", "verify_file", "put"])
        manifest = verify_manifest(self.drive, "set-a")
        self.assertIn("server_capture", manifest["provenance"])
        self.assertFalse(cipher.exists())
        self.assertFalse(sidecar.exists())

    def test_wrong_passphrase_keeps_files(self):
        cipher, sidecar = self.make_pending()
        outcome = self.promote(passphrase="another-passphrase")
        self.assertEqual(outcome["error"]["code"], "passphrase_not_current")
        self.assertTrue(cipher.exists())
        self.assertTrue(sidecar.exists())
        self.assertNotIn("sets/set-a/manifest.json", self.drive.objects)

    def test_ciphertext_not_matching_sidecar_is_invalid(self):
        cipher, sidecar = self.make_pending()
        cipher.write_bytes(cipher.read_bytes() + b"x")
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "pending_artifact_invalid")
        self.assertTrue(cipher.exists())
        self.assertTrue(sidecar.exists())

    def test_no_sidecar_derives_manifest_from_members(self):
        cipher, sidecar = self.make_pending()
        sidecar.unlink()
        self.offline()
        outcome = self.promote()
        self.assertTrue(outcome["ok"], outcome)
        manifest = verify_manifest(self.drive, "set-a")
        self.assertEqual(manifest["provenance"], {"pending_recovered": True})
        self.assertEqual(manifest["profiles"], ["amarsonar-bangla-prod", "control-plane"])
        self.assertEqual({item["name"] for item in manifest["artifacts"]},
                         {ARCHIVE_ARTIFACT, DECLARATION_ARTIFACT})
        records = {item["name"]: item for item in manifest["artifacts"]}
        self.assertEqual(records[ARCHIVE_ARTIFACT]["sha256"], hashlib.sha256(ARCHIVE).hexdigest())
        self.assertEqual(set(manifest["profile_bindings"]),
                         {"amarsonar-bangla-prod", "control-plane"})
        self.assertFalse(cipher.exists())
        self.assertEqual(self.transport.marked, [])

    def test_pending_leftover_for_id_published_elsewhere_is_conflict(self):
        # Drive holds set-a from another capture; the stale local pending file
        # must not be uploaded over it (FR-028 before FR-029).
        cipher, sidecar = self.make_pending()
        self.drive.objects.clear()
        other = self.materialized / "other"
        other.mkdir()
        (other / "file").write_bytes(b"other")
        self.capture().publish_files("set-a", {"control-plane/file": other / "file"},
                                     profiles=("control-plane",))
        published = dict(self.drive.objects)
        self.drive.log.clear()
        outcome = self.promote()
        self.assertEqual(outcome["error"]["code"], "set_id_conflict")
        self.assertEqual(self.drive.objects, published)
        self.assertEqual([op for op, _key in self.drive.log], [])
        self.assertTrue(cipher.exists())
        self.assertTrue(sidecar.exists())

    def test_pending_leftover_of_finished_publish_reports_existing(self):
        cipher, sidecar = self.make_pending()
        copies = (cipher.read_bytes(), sidecar.read_bytes())
        self.offline()
        self.assertTrue(self.promote()["ok"])
        # Simulate a publish whose local cleanup was lost after the upload.
        cipher.write_bytes(copies[0])
        sidecar.write_bytes(copies[1])
        published = dict(self.drive.objects)
        self.drive.log.clear()
        outcome = self.promote()
        self.assertTrue(outcome["ok"], outcome)
        self.assertEqual(outcome["status"], "already_published")
        self.assertTrue(outcome["data"]["from_pending"])
        self.assertEqual(self.drive.objects, published)
        self.assertEqual([op for op, _key in self.drive.log], [])
        self.assertFalse(cipher.exists())
        self.assertFalse(sidecar.exists())

    def test_pending_files_are_owner_only(self):
        cipher, sidecar = self.make_pending()
        self.assertEqual(cipher.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sidecar.stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.base / "pending").st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
