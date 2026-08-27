"""The whole harness lifecycle, driven offline with injected fakes.

This walks validate -> plan -> accept -> record -> finalize -> validate bundle
-> report, exactly as the future authorized run will, and then asserts the one
thing that matters most: a run made of local fakes is never accepted as live
proof, no matter how complete it looks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    bundle as bundle_module, cleanup as cleanup_module, fixtures,
    ledger as ledger_module, manifest as manifest_module, probes,
    report as report_module,
)


REQUEST = "cv-proof-lifecycle"
START = "2026-09-01T10:00:00Z"
END = "2026-09-01T12:00:00Z"


class TestHarnessLifecycle(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = manifest_module.validate_manifest(fixtures.manifest())
        self.ledger = ledger_module.ProofRunLedger(self.root / "ledger")

    def _drive(self, *, provenance):
        self.ledger.open_run(request_id=REQUEST, manifest=self.manifest,
                             started_at=START, provenance=provenance)
        decision = self.ledger.should_launch(REQUEST)
        self.assertTrue(decision["launch"])
        self.ledger.record_acceptance(REQUEST, fixtures.acceptance())
        self.assertFalse(self.ledger.should_launch(REQUEST)["launch"])

        for entry in probes.plan(self.manifest):
            expected = list(entry["expected"])
            parsed = probes.parse(entry["check_id"], {
                "returncode": 0, "stdout": " ".join(expected), "stderr": "",
                "timed_out": False, "expected": expected,
            }, self.manifest)
            self.ledger.record_check(REQUEST, entry["check_id"], parsed["state"])

        verified = cleanup_module.verify(
            self.manifest, fixtures.cleanup_observations(self.manifest))
        self.ledger.record_cleanup(
            REQUEST, "complete" if verified["ok"] else "incomplete")

        bundle_root = self.root / "bundle"
        bundle_root.mkdir(exist_ok=True)
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END)
        for name in manifest_module.artifact_names(self.manifest):
            if name == "checks.json":
                document = fixtures.check_artifact(self.manifest, record)
            else:
                document = fixtures.cleanup_artifact(self.manifest, record)
            payload = manifest_module.canonical_json(document).encode()
            (bundle_root / name).write_bytes(payload)
            self.ledger.record_artifact(REQUEST, name,
                                        hashlib.sha256(payload).hexdigest())
        record = self.ledger.read(REQUEST)
        states = {name: "passed" for name in record["checks"]}
        (bundle_root / "events.json").write_text(
            manifest_module.canonical_json(fixtures.events(states)))
        (bundle_root / "run.json").write_text(
            manifest_module.canonical_json(record) + "\n")
        return record, bundle_root, verified

    def test_a_fully_driven_local_run_is_never_accepted_as_live_proof(self):
        record, bundle_root, _verified = self._drive(provenance="local_injected_fake")
        # Everything passed and cleanup was clean, and it still is not proof.
        self.assertEqual(set(record["checks"].values()), {"passed"})
        self.assertEqual(record["cleanup_state"], "complete")
        self.assertEqual(record["classification"], "blocked")
        with self.assertRaises(bundle_module.BundleError) as raised:
            bundle_module.validate_bundle(bundle_root, manifest=self.manifest)
        self.assertEqual(raised.exception.code, "provenance_not_live")

        report = report_module.build_report(manifest=self.manifest, record=record)
        self.assertEqual(report["live_checks_passed"], ())
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertIsNone(report["evidence_id"])
        rendered = report_module.render(report)
        self.assertIn("local harness tests are not live proof", rendered)

    def test_the_same_lifecycle_with_live_provenance_validates_end_to_end(self):
        # This is the shape a real authorized run must produce. It is still a
        # fixture: the provenance label here is asserted by the operator on the
        # proof host, never by this test.
        record, bundle_root, verified = self._drive(provenance="live_authorized_host")
        self.assertEqual(record["classification"], "passed_live")
        self.assertTrue(verified["ok"])
        result = bundle_module.validate_bundle(
            bundle_root, manifest=self.manifest, expected_request_id=REQUEST,
            now="2026-09-02T00:00:00Z")
        self.assertTrue(result["ok"])
        self.assertEqual(result["classification"], "passed_live")

        report = report_module.build_report(
            manifest=self.manifest, record=record, bundle=result, cleanup=verified)
        self.assertEqual(len(report["live_checks_passed"]), 8)
        self.assertEqual(report["evidence_missing"], ())
        self.assertEqual(report["cleanup_incomplete"], ())
        # Even a clean live bundle does not promote anything on its own.
        self.assertEqual(report["support_tier"], "implemented_unproven")
        self.assertFalse(report["adoptable"])
        self.assertIsNone(report["evidence_id"])
        self.assertIn("t031_independent_review", report["independent_review_pending"])

    def test_a_retry_after_an_unknown_acceptance_never_launches_again(self):
        self.ledger.open_run(request_id=REQUEST, manifest=self.manifest,
                             started_at=START)
        self.ledger.record_acceptance(REQUEST, {})
        for _attempt in range(3):
            decision = self.ledger.should_launch(REQUEST)
            self.assertFalse(decision["launch"])
            self.assertEqual(decision["code"], "acceptance_unknown")
        record = self.ledger.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END)
        self.assertEqual(record["classification"], "acceptance_unknown")

    def test_the_whole_run_surface_stays_free_of_secret_like_material(self):
        from credential_vault_proof import scanner

        record, bundle_root, verified = self._drive(provenance="live_authorized_host")
        self.assertTrue(scanner.is_clean(scanner.scan_directory(bundle_root)))
        self.assertTrue(scanner.is_clean(scanner.scan_document(record)))
        report = report_module.build_report(
            manifest=self.manifest, record=record, cleanup=verified)
        self.assertTrue(scanner.is_clean(scanner.scan_text(
            report_module.render(report))))


if __name__ == "__main__":
    unittest.main()
