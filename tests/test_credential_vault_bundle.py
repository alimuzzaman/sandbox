"""Offline validation of a completed proof bundle.

Every bundle here is synthetic. The validator's job is to refuse anything that
is not exactly the planned run, so most of these tests build a good bundle and
then break one thing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    bundle as bundle_module, fixtures, ledger as ledger_module,
    manifest as manifest_module,
)


REQUEST = "cv-proof-0001"
START = "2026-09-01T10:00:00Z"
END = "2026-09-01T12:00:00Z"


class BundleTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "bundle"
        self.root.mkdir(parents=True)
        self.manifest = manifest_module.validate_manifest(fixtures.manifest())

    def build(self, *, check_states=None, cleanup="complete",
              classification="passed_live", provenance="live_authorized_host",
              artifacts=("checks.json", "cleanup.json"), events=None,
              job=None, terminal_at=END):
        states = check_states or {
            name: "passed" for name in manifest_module.check_ids(self.manifest)
        }
        recorded = {}
        for name in artifacts:
            value = (fixtures.execution_artifact(self.manifest, states)
                     if name == "checks.json" else
                     fixtures.cleanup_artifact(self.manifest))
            payload = manifest_module.canonical_json(value).encode()
            (self.root / name).write_bytes(payload)
            recorded[name] = hashlib.sha256(payload).hexdigest()
        record = {
            "version": ledger_module.LEDGER_VERSION,
            "request_id": REQUEST,
            "manifest_digest": manifest_module.manifest_digest(self.manifest),
            "target": dict(self.manifest["target"]),
            "expected": dict(self.manifest["source"]),
            "job": job or {"state": "accepted", "job_id": "job-fixture-0001",
                           "accepted_at": "2026-09-01T10:01:00Z"},
            "started_at": START,
            "terminal_at": terminal_at,
            "checks": dict(states),
            "artifacts": recorded,
            "cleanup_state": cleanup,
            "classification": classification,
            "provenance": provenance,
        }
        (self.root / "run.json").write_text(
            manifest_module.canonical_json(record) + "\n")
        (self.root / "events.json").write_text(manifest_module.canonical_json(
            events if events is not None else fixtures.events(states),
        ))
        return record

    def validate(self, **kwargs):
        return bundle_module.validate_bundle(
            self.root, manifest=self.manifest, **kwargs)

    def assert_refused(self, code, **kwargs):
        with self.assertRaises(bundle_module.BundleError) as raised:
            self.validate(**kwargs)
        self.assertEqual(raised.exception.code, code)


class TestBundleValidator(BundleTestCase):
    def test_a_complete_live_bundle_is_accepted(self):
        self.build()
        result = self.validate(expected_request_id=REQUEST)
        self.assertTrue(result["ok"])
        self.assertEqual(result["classification"], "passed_live")
        self.assertEqual(result["artifact_count"], 2)
        self.assertEqual(result["required_failed"], ())
        self.assertEqual(result["cleanup_state"], "complete")

    def test_a_manifest_digest_mismatch_is_refused(self):
        self.build()
        other = manifest_module.validate_manifest(
            fixtures.manifest(manifest_id="credential-vault-proof-other"))
        with self.assertRaises(bundle_module.BundleError) as raised:
            bundle_module.validate_bundle(self.root, manifest=other)
        self.assertEqual(raised.exception.code, "manifest_digest_mismatch")

    def test_evidence_from_another_machine_or_epoch_is_refused(self):
        record = self.build()
        record["target"] = {**record["target"], "machine_id": "sb-ffffffffffff"}
        (self.root / "run.json").write_text(
            manifest_module.canonical_json(record) + "\n")
        self.assert_refused("target_mismatch")

    def test_a_mixed_revision_bundle_is_refused(self):
        record = self.build()
        record["expected"] = {**record["expected"], "sandbox_revision": "sandbox-9.9.9"}
        (self.root / "run.json").write_text(
            manifest_module.canonical_json(record) + "\n")
        self.assert_refused("revision_mismatch")

    def test_a_wrong_request_identity_is_refused(self):
        self.build()
        self.assert_refused("request_identity_mismatch",
                            expected_request_id="cv-proof-9999")

    def test_a_local_fake_run_is_never_accepted_as_live(self):
        self.build(provenance="local_injected_fake")
        self.assert_refused("provenance_not_live")

    def test_a_fake_marker_inside_live_evidence_is_refused(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        events = fixtures.events(states)
        events[0]["code"] = "simulated"
        self.build(events=events)
        self.assert_refused("fake_evidence_marker")

    def test_an_unknown_acceptance_is_refused_as_evidence(self):
        self.build(job={"state": "unknown", "job_id": None, "accepted_at": None},
                   classification="acceptance_unknown")
        self.assert_refused("acceptance_unknown")

    def test_a_missing_or_unplanned_artifact_is_refused(self):
        self.build(artifacts=("checks.json",))
        self.assert_refused("artifact_missing")
        self.setUp()
        self.build()
        (self.root / "extra.json").write_text("{}")
        self.assert_refused("artifact_unplanned")

    def test_a_tampered_artifact_digest_is_refused(self):
        self.build()
        (self.root / "checks.json").write_text('{"artifact":"tampered"}')
        self.assert_refused("artifact_digest_mismatch")

    def test_secret_like_material_anywhere_in_the_bundle_is_refused(self):
        self.build()
        (self.root / "checks.json").write_text(json.dumps(
            {"note": fixtures.SECRET_SHAPED["authorization_header"]}))
        self.assert_refused("secret_like_material")

    def test_event_ordering_duplicates_and_gaps_are_refused(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        cases = (
            ("events_not_monotonic", lambda events: events.__setitem__(
                1, {**events[1], "sequence": 9})),
            ("events_duplicate_terminal", lambda events: events.append({
                "sequence": len(events) + 1, "at": "2026-09-01T13:00:00Z",
                "check_id": events[1]["check_id"], "state": "passed",
                "code": "observed"})),
            ("events_terminal_without_start", lambda events: events.__setitem__(
                0, {**events[0], "state": "passed"})),
            ("events_check_unplanned", lambda events: events.__setitem__(
                0, {**events[0], "check_id": "not_a_planned_check"})),
        )
        for code, mutate in cases:
            with self.subTest(code=code):
                self.setUp()
                events = fixtures.events(states)
                mutate(events)
                self.build(events=events)
                self.assert_refused(code)

    def test_a_missing_terminal_event_is_refused(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        events = fixtures.events(states)
        del events[-1]
        self.build(events=events)
        self.assert_refused("events_terminal_missing")

    def test_contradictory_ledger_and_event_results_are_refused(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        events = fixtures.events(states)
        events[1] = {**events[1], "state": "failed"}
        self.build(events=events)
        self.assert_refused("result_contradiction")

    def test_an_incomplete_check_is_refused(self):
        # Ledger incompleteness is refused before event or artifact parsing.
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        first = next(iter(states))
        states[first] = "pending"
        events = fixtures.events({k: v for k, v in states.items() if v != "pending"})
        self.build(check_states=states, events=events)
        self.assert_refused("check_incomplete")

        self.setUp()
        full = fixtures.events({name: "passed" for name in states})
        self.build(check_states=states, events=full)
        self.assert_refused("check_incomplete")

    def test_success_with_incomplete_cleanup_is_refused(self):
        self.build(cleanup="incomplete", classification="passed_live")
        self.assert_refused("cleanup_artifact_state_mismatch")

    def test_a_pass_that_contains_a_failed_required_check_is_refused(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        states["lease_socket_owned"] = "failed"
        self.build(check_states=states, classification="passed_live")
        self.assert_refused("result_contradiction")

    def test_a_bundle_that_omits_required_checks_is_refused(self):
        # The manifest digest binds the plan; this binds the record's own check
        # set to it. Otherwise an omitted required check is neither failed nor
        # blocked, just absent, and a passed_live claim survives without it.
        states = {"os_release_supported": "passed"}
        self.build(check_states=states, events=fixtures.events(states))
        self.assert_refused("check_missing")

    def test_a_bundle_with_an_unplanned_check_is_refused(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        states["a_check_nobody_planned"] = "passed"
        self.build(check_states=states, events=fixtures.events(states))
        self.assert_refused("check_unplanned")

    def test_an_unplanned_file_in_a_nested_directory_is_refused(self):
        self.build()
        nested = self.root / "extra"
        nested.mkdir()
        (nested / "smuggled.json").write_text('{"note":"unplanned"}')
        self.assert_refused("artifact_unplanned")

    def test_evidence_dated_after_now_is_refused(self):
        self.build()
        self.assert_refused("evidence_from_the_future", now="2026-08-01T00:00:00Z")
        self.assertTrue(self.validate(now="2026-09-02T00:00:00Z")["ok"])

    def test_symlinked_or_oversize_artifacts_are_refused(self):
        self.build()
        (self.root / "checks.json").unlink()
        (self.root / "checks.json").symlink_to(self.root / "cleanup.json")
        self.assert_refused("evidence_symlink")

    def test_a_non_canonical_run_record_is_refused(self):
        record = self.build()
        (self.root / "run.json").write_text(json.dumps(record, indent=2))
        self.assert_refused("encoding_not_canonical")

    def test_a_failed_live_bundle_validates_but_does_not_claim_success(self):
        states = {name: "passed" for name in manifest_module.check_ids(self.manifest)}
        states["lease_socket_owned"] = "failed"
        events = fixtures.events(states)
        self.build(check_states=states, classification="failed_live", events=events)
        result = self.validate()
        self.assertEqual(result["classification"], "failed_live")
        self.assertEqual(result["required_failed"], ("lease_socket_owned",))

    def test_recorded_execution_metadata_cannot_claim_a_different_source_or_argv(self):
        for field, value, code in (
            ("source", "guest_probe", "execution_artifact_source_mismatch"),
            ("argv", ["/usr/bin/uname", "-r"],
             "execution_artifact_argv_mismatch"),
        ):
            with self.subTest(field=field):
                self.setUp()
                record = self.build()
                path = self.root / "checks.json"
                document = json.loads(path.read_text())
                document[0][field] = value
                raw = manifest_module.canonical_json(document).encode()
                path.write_bytes(raw)
                record["artifacts"]["checks.json"] = hashlib.sha256(raw).hexdigest()
                (self.root / "run.json").write_text(
                    manifest_module.canonical_json(record) + "\n")
                self.assert_refused(code)

    def test_cleanup_artifact_must_cover_every_exact_resource(self):
        record = self.build()
        path = self.root / "cleanup.json"
        document = json.loads(path.read_text())
        document["observations"].pop()
        raw = manifest_module.canonical_json(document).encode()
        path.write_bytes(raw)
        record["artifacts"]["cleanup.json"] = hashlib.sha256(raw).hexdigest()
        (self.root / "run.json").write_text(
            manifest_module.canonical_json(record) + "\n")
        self.assert_refused("cleanup_artifact_coverage_mismatch")

    def test_old_evidence_is_refused_even_when_every_digest_matches(self):
        self.build()
        self.assert_refused("evidence_stale", now="2026-09-03T12:00:01Z")


if __name__ == "__main__":
    unittest.main()
