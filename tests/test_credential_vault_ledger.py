"""Replay safety, acceptance handling, and classification for the proof ledger.

Offline only: no job is launched, no host is contacted, and every record here
carries the fixture provenance so it can never be mistaken for live proof.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    fixtures, ledger as ledger_module, manifest as manifest_module,
)


START = "2026-09-01T10:00:00Z"
END = "2026-09-01T11:00:00Z"
REQUEST = "cv-proof-0001"


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "ledger"
        self.manifest = manifest_module.validate_manifest(fixtures.manifest())
        self.ledger = ledger_module.ProofRunLedger(self.root)

    def open_run(self, *, provenance="live_authorized_host", request_id=REQUEST,
                 manifest=None):
        return self.ledger.open_run(
            request_id=request_id, manifest=manifest or self.manifest,
            started_at=START, provenance=provenance,
        )

    def pass_everything(self, *, request_id=REQUEST):
        for check_id in manifest_module.check_ids(self.manifest):
            self.ledger.record_check(request_id, check_id, "passed")
        self.ledger.record_cleanup(request_id, "complete")


class TestProofRunLedger(LedgerTestCase):
    def test_a_new_run_starts_pending_with_every_check_unresolved(self):
        record = self.open_run()
        self.assertEqual(record["job"], {"state": "pending", "job_id": None,
                                         "accepted_at": None})
        self.assertEqual(set(record["checks"].values()), {"pending"})
        self.assertEqual(record["cleanup_state"], "pending")
        self.assertIsNone(record["classification"])
        self.assertEqual(record["manifest_digest"],
                         manifest_module.manifest_digest(self.manifest))

    def test_reusing_one_request_id_with_different_inputs_is_refused(self):
        self.open_run()
        other = manifest_module.validate_manifest(
            fixtures.manifest(manifest_id="credential-vault-proof-other"),
        )
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.open_run(request_id=REQUEST, manifest=other, started_at=START)
        self.assertEqual(raised.exception.code, "request_id_conflict")
        with self.assertRaises(ledger_module.LedgerError):
            self.open_run(provenance="local_injected_fake")

    def test_reattaching_the_same_inputs_returns_the_existing_run(self):
        first = self.open_run()
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        again = self.open_run()
        self.assertEqual(again["started_at"], first["started_at"])
        self.assertEqual(again["job"]["job_id"], "job-fixture-0001")

    def test_empty_or_malformed_acceptance_is_acceptance_unknown(self):
        for acceptance in ({}, {"job_id": ""}, {"job_id": None}, "", None,
                           {"job_id": "x"}, {"accepted": True}):
            with self.subTest(acceptance=acceptance):
                request_id = f"cv-proof-{abs(hash(repr(acceptance))) % 10000:04d}"
                self.open_run(request_id=request_id)
                record = self.ledger.record_acceptance(request_id, acceptance)
                self.assertEqual(record["job"]["state"], "unknown")
                self.assertIsNone(record["job"]["job_id"])
                self.assertEqual(record["classification"], "acceptance_unknown")

    def test_an_explicit_refusal_is_not_an_unknown_acceptance(self):
        self.open_run()
        record = self.ledger.record_acceptance(
            REQUEST, fixtures.acceptance(accepted=False),
        )
        self.assertEqual(record["job"]["state"], "refused")
        self.assertIsNone(record["job"]["job_id"])

    def test_a_retry_consults_the_ledger_before_launching(self):
        self.assertTrue(self.ledger.should_launch(REQUEST)["launch"])
        self.open_run()
        self.assertTrue(self.ledger.should_launch(REQUEST)["launch"])
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        decision = self.ledger.should_launch(REQUEST)
        self.assertFalse(decision["launch"])
        self.assertEqual(decision["code"], "job_already_accepted")
        self.assertEqual(decision["job_id"], "job-fixture-0001")

    def test_an_unknown_acceptance_blocks_a_second_launch(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, {})
        decision = self.ledger.should_launch(REQUEST)
        self.assertFalse(decision["launch"])
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["code"], "acceptance_unknown")

    def test_a_conflicting_acceptance_for_an_accepted_run_is_refused(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_acceptance(REQUEST, fixtures.acceptance(
                job_id="job-fixture-0002",
            ))
        self.assertEqual(raised.exception.code, "acceptance_conflict")

    def test_contradicting_a_recorded_check_or_artifact_is_refused(self):
        self.open_run()
        self.ledger.record_check(REQUEST, "os_release_supported", "passed")
        self.ledger.record_check(REQUEST, "os_release_supported", "passed")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_check(REQUEST, "os_release_supported", "failed")
        self.assertEqual(raised.exception.code, "check_contradiction")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_check(REQUEST, "not_a_planned_check", "passed")
        self.assertEqual(raised.exception.code, "check_unknown")
        self.ledger.record_artifact(REQUEST, "checks.json", "a" * 64,
                                    manifest=self.manifest)
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_artifact(REQUEST, "checks.json", "b" * 64,
                                        manifest=self.manifest)
        self.assertEqual(raised.exception.code, "artifact_conflict")

    def test_cleanup_failure_can_never_be_walked_back(self):
        self.open_run()
        self.ledger.record_cleanup(REQUEST, "incomplete")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_cleanup(REQUEST, "complete")
        self.assertEqual(raised.exception.code, "cleanup_contradiction")

    def test_a_complete_live_run_classifies_as_passed_live(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        self.pass_everything()
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END,
        )
        self.assertEqual(record["classification"], "passed_live")
        self.assertEqual(record["terminal_at"], END)

    def test_a_local_fake_run_never_classifies_as_passed_live(self):
        self.open_run(provenance="local_injected_fake")
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        self.pass_everything()
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END,
        )
        self.assertEqual(record["classification"], "blocked")

    def test_partial_evidence_never_becomes_passed_live(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        self.ledger.record_check(REQUEST, "os_release_supported", "passed")
        self.ledger.record_cleanup(REQUEST, "complete")
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END,
        )
        self.assertEqual(record["classification"], "blocked")

    def test_cleanup_failure_overrides_a_clean_result(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        for check_id in manifest_module.check_ids(self.manifest):
            self.ledger.record_check(REQUEST, check_id, "passed")
        self.ledger.record_cleanup(REQUEST, "incomplete")
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END,
        )
        self.assertEqual(record["classification"], "cleanup_incomplete")

    def test_a_failed_required_check_classifies_as_failed_live(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        for check_id in manifest_module.check_ids(self.manifest):
            state = "failed" if check_id == "lease_socket_owned" else "passed"
            self.ledger.record_check(REQUEST, check_id, state)
        self.ledger.record_cleanup(REQUEST, "complete")
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END,
        )
        self.assertEqual(record["classification"], "failed_live")

    def test_an_unknown_acceptance_survives_finalization(self):
        self.open_run()
        self.ledger.record_acceptance(REQUEST, {})
        self.pass_everything()
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END,
        )
        self.assertEqual(record["classification"], "acceptance_unknown")

    def test_acceptance_unknown_is_sticky_against_later_success(self):
        self.open_run()
        first = self.ledger.record_acceptance(REQUEST, {})
        self.assertEqual(first["classification"], "acceptance_unknown")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        self.assertEqual(raised.exception.code, "acceptance_unknown_sticky")
        self.assertEqual(self.ledger.read(REQUEST)["job"]["state"], "unknown")

    def test_acceptance_requires_exact_fresh_request_provenance(self):
        cases = (
            {"request_id": "cv-proof-other"},
            {"manifest_digest": "f" * 64},
            {"machine_id": "sb-ffffffffffff"},
            {"accepted_at": "2026-09-01T10:06:00Z"},
            {"accepted_at": "2026-09-01T09:59:59Z"},
        )
        for index, overrides in enumerate(cases):
            with self.subTest(overrides=tuple(overrides)):
                request_id = f"cv-proof-fresh-{index}"
                self.open_run(request_id=request_id)
                acceptance = fixtures.acceptance(
                    manifest_document=self.manifest, request_id=request_id)
                acceptance.update(overrides)
                record = self.ledger.record_acceptance(request_id, acceptance)
                self.assertEqual(record["classification"], "acceptance_unknown")

    def test_caller_owned_symlink_ancestor_and_temp_collision_are_refused(self):
        real = Path(self.temporary.name) / "real"
        real.mkdir(mode=0o700)
        linked = Path(self.temporary.name) / "linked"
        linked.symlink_to(real, target_is_directory=True)
        unsafe = ledger_module.ProofRunLedger(linked / "ledger")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            unsafe.open_run(request_id=REQUEST, manifest=self.manifest,
                            started_at=START)
        self.assertEqual(raised.exception.code, "ledger_ancestor_symlink")

        self.root.mkdir(mode=0o700)
        temporary = self.root / f".{REQUEST}.json.{os.getpid()}.fixed.tmp"
        temporary.write_text("occupied")
        with mock.patch.object(ledger_module.secrets, "token_hex", return_value="fixed"):
            with self.assertRaises(ledger_module.LedgerError) as raised:
                self.open_run()
        self.assertEqual(raised.exception.code, "record_temp_conflict")
        self.assertEqual(temporary.read_text(), "occupied")

    def test_write_is_owner_only_atomic_and_syncs_file_and_directory(self):
        with mock.patch.object(ledger_module.os, "replace",
                               wraps=ledger_module.os.replace) as replace, \
                mock.patch.object(ledger_module.os, "fsync",
                                  wraps=ledger_module.os.fsync) as fsync:
            self.open_run()
        replace.assert_called_once()
        self.assertGreaterEqual(fsync.call_count, 2)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / f"{REQUEST}.json").stat().st_mode),
                         0o600)

    def test_an_overpermissive_ledger_directory_is_refused(self):
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o755)
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.open_run()
        self.assertEqual(raised.exception.code, "ledger_root_permissions")

    def test_artifact_recording_is_bound_to_the_run_manifest(self):
        self.open_run()
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_artifact(REQUEST, "unplanned.json", "a" * 64,
                                        manifest=self.manifest)
        self.assertEqual(raised.exception.code, "artifact_unknown")
        other = fixtures.manifest(manifest_id="credential-vault-proof-other")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.record_artifact(REQUEST, "checks.json", "a" * 64,
                                        manifest=other)
        self.assertEqual(raised.exception.code, "manifest_digest_mismatch")

    def test_records_are_canonical_owner_only_and_refuse_tampering(self):
        self.open_run()
        path = self.root / f"{REQUEST}.json"
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        raw = path.read_bytes()
        self.assertEqual(raw, manifest_module.canonical_json(
            json.loads(raw.decode()),
        ).encode() + b"\n")

        path.write_text(json.dumps(json.loads(raw.decode()), indent=2))
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.read(REQUEST)
        self.assertEqual(raised.exception.code, "encoding_not_canonical")

        path.write_text("{not json")
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.read(REQUEST)
        self.assertEqual(raised.exception.code, "record_corrupt")

        path.write_bytes(b"x" * (ledger_module.MAX_RECORD_BYTES + 1))
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.read(REQUEST)
        self.assertEqual(raised.exception.code, "record_oversize")

    def test_symlinked_and_foreign_owned_records_are_refused(self):
        self.open_run()
        path = self.root / f"{REQUEST}.json"
        link_id = "cv-proof-link"
        (self.root / f"{link_id}.json").symlink_to(path)
        with self.assertRaises(ledger_module.LedgerError) as raised:
            self.ledger.read(link_id)
        self.assertEqual(raised.exception.code, "record_symlink")

        foreign = ledger_module.ProofRunLedger(self.root, owner_uid=os.getuid() + 1)
        with self.assertRaises(ledger_module.LedgerError) as raised:
            foreign.read(REQUEST)
        self.assertEqual(raised.exception.code, "record_foreign_owner")

    def test_records_refuse_secret_like_or_unknown_fields(self):
        record = self.open_run()
        tainted = dict(record)
        tainted["target"] = dict(record["target"])
        tainted["target"]["host_label"] = \
            fixtures.SECRET_SHAPED["authorization_header"]
        with self.assertRaises(ledger_module.LedgerError) as raised:
            ledger_module.validate_record(tainted)
        self.assertEqual(raised.exception.code, "secret_like_material")

        extra = dict(record)
        extra[fixtures.SECRET_SHAPED["internal_identifier"]] = "op-1"
        with self.assertRaises(ledger_module.LedgerError):
            ledger_module.validate_record(extra)

    def test_a_terminal_timestamp_must_not_precede_the_start(self):
        record = self.open_run()
        backwards = dict(record)
        backwards["terminal_at"] = "2026-08-31T10:00:00Z"
        with self.assertRaises(ledger_module.LedgerError) as raised:
            ledger_module.validate_record(backwards)
        self.assertEqual(raised.exception.code, "timestamp_not_monotonic")

    def test_artifact_digests_are_stable_and_bounded(self):
        self.assertEqual(len(ledger_module.artifact_digest(b"payload")), 64)
        self.assertEqual(ledger_module.artifact_digest("payload"),
                         ledger_module.artifact_digest(b"payload"))
        with self.assertRaises(ledger_module.LedgerError):
            ledger_module.artifact_digest(7)


if __name__ == "__main__":
    unittest.main()
