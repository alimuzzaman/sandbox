"""Offline CLI verbs and deterministic report rendering.

The runner never executes a live check, opens a socket, or contacts a host.
These tests drive it entirely through temporary files.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from credential_vault_proof import (  # noqa: E402
    cleanup as cleanup_module, cli, fixtures, ledger as ledger_module,
    manifest as manifest_module, report as report_module,
)


REQUEST = "cv-proof-0001"
START = "2026-09-01T10:00:00Z"
END = "2026-09-01T12:00:00Z"


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = manifest_module.validate_manifest(fixtures.manifest())
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(
            manifest_module.canonical_json(self.manifest) + "\n")
        self.ledger_path = self.root / "ledger"

    def run_cli(self, *argv):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = cli.run(list(argv))
        return code, stream.getvalue()

    def json_cli(self, *argv):
        code, output = self.run_cli(*argv)
        return code, json.loads(output)


class TestOfflineRunner(RunnerTestCase):
    def test_validate_manifest_reports_a_stable_digest(self):
        code, document = self.json_cli("validate-manifest", "--manifest",
                                       str(self.manifest_path))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(document["ok"])
        self.assertEqual(document["manifest_digest"],
                         manifest_module.manifest_digest(self.manifest))
        self.assertEqual(document["check_count"], 8)

    def test_plan_emits_bounded_argv_entries_only(self):
        code, document = self.json_cli("plan", "--manifest", str(self.manifest_path))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(len(document["entries"]), 8)
        for entry in document["entries"]:
            self.assertLessEqual(entry["timeout_seconds"], 120)
            self.assertLessEqual(entry["max_output_bytes"], 65536)
            for token in entry["argv"]:
                self.assertNotIn(";", token)

    def test_record_acceptance_persists_a_replay_safe_identity(self):
        code, document = self.json_cli(
            "record-acceptance", "--manifest", str(self.manifest_path),
            "--ledger", str(self.ledger_path), "--request-id", REQUEST,
            "--at", START, "--acceptance", json.dumps(fixtures.acceptance()),
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue(document["ok"])
        store = ledger_module.ProofRunLedger(self.ledger_path)
        self.assertFalse(store.should_launch(REQUEST)["launch"])

    def test_an_empty_acceptance_is_recorded_as_unknown_and_blocks(self):
        code, document = self.json_cli(
            "record-acceptance", "--manifest", str(self.manifest_path),
            "--ledger", str(self.ledger_path), "--request-id", REQUEST,
            "--at", START, "--acceptance", "{}",
        )
        self.assertEqual(code, cli.EXIT_BLOCKED)
        self.assertFalse(document["ok"])
        self.assertEqual(document["code"], "acceptance_unknown")

    def test_record_artifact_hashes_a_real_file_and_refuses_a_symlink(self):
        self.json_cli("record-acceptance", "--manifest", str(self.manifest_path),
                      "--ledger", str(self.ledger_path), "--request-id", REQUEST,
                      "--at", START, "--acceptance",
                      json.dumps(fixtures.acceptance()))
        artifact = self.root / "checks.json"
        artifact.write_text('{"check":"passed"}')
        code, document = self.json_cli(
            "record-artifact", "--ledger", str(self.ledger_path),
            "--request-id", REQUEST, "--artifact", "checks.json",
            "--artifact-path", str(artifact),
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(document["sha256"],
                         hashlib.sha256(artifact.read_bytes()).hexdigest())

        link = self.root / "link.json"
        link.symlink_to(artifact)
        code, document = self.json_cli(
            "record-artifact", "--ledger", str(self.ledger_path),
            "--request-id", REQUEST, "--artifact", "cleanup.json",
            "--artifact-path", str(link),
        )
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(document["code"], "artifact_missing")

    def test_finalize_reports_the_classification_and_exit_code(self):
        store = ledger_module.ProofRunLedger(self.ledger_path)
        store.open_run(request_id=REQUEST, manifest=self.manifest, started_at=START)
        store.record_acceptance(REQUEST, fixtures.acceptance())
        for check_id in manifest_module.check_ids(self.manifest):
            store.record_check(REQUEST, check_id, "passed")
        store.record_cleanup(REQUEST, "complete")
        code, document = self.json_cli(
            "finalize", "--manifest", str(self.manifest_path),
            "--ledger", str(self.ledger_path), "--request-id", REQUEST, "--at", END,
        )
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(document["code"], "passed_live")

    def test_every_failure_prints_a_bounded_code_and_no_exception_text(self):
        cases = (
            ("validate-manifest",),
            ("validate-manifest", "--manifest", str(self.root / "absent.json")),
            ("plan", "--manifest", str(self.root / "absent.json")),
            ("finalize", "--manifest", str(self.manifest_path), "--ledger",
             str(self.ledger_path), "--request-id", REQUEST, "--at", END),
            ("render-report", "--manifest", str(self.manifest_path), "--ledger",
             str(self.ledger_path), "--request-id", REQUEST),
        )
        for argv in cases:
            with self.subTest(argv=argv[0]):
                code, output = self.run_cli(*argv)
                self.assertEqual(code, cli.EXIT_REFUSED)
                self.assertNotIn("Traceback", output)
                self.assertNotIn("File \"", output)
                document = json.loads(output)
                self.assertFalse(document["ok"])
                self.assertRegex(document["code"], r"^[a-z0-9_]+$")

    def test_an_unknown_verb_is_refused_without_a_stack_trace(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code, output = self.run_cli("execute-live-check")
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(output)["code"], "arguments_invalid")

    def test_the_runner_exposes_no_execution_verb(self):
        self.assertEqual(set(cli.VERBS), {
            "validate-manifest", "plan", "record-acceptance", "record-artifact",
            "finalize", "validate-bundle", "render-report",
        })
        # Check for real execution or network capability, not for the words in
        # the module's own prose about not having any.
        source = Path(cli.__file__).read_text()
        for forbidden in ("import subprocess", "import socket", "import ssl",
                          "import http", "import urllib", "import requests",
                          "os.system", "popen(", "check_output"):
            self.assertNotIn(forbidden, source)
        package = Path(cli.__file__).parent
        for module in sorted(package.glob("*.py")):
            body = module.read_text()
            for forbidden in ("import subprocess", "import socket", "import ssl",
                              "import urllib", "os.system", "popen("):
                self.assertNotIn(forbidden, body, msg=module.name)


class TestReportRendering(RunnerTestCase):
    def _record(self, **overrides):
        store = ledger_module.ProofRunLedger(self.ledger_path)
        record = store.open_run(request_id=REQUEST, manifest=self.manifest,
                                started_at=START, **overrides)
        return store, record

    def test_a_report_separates_local_harness_from_live_evidence(self):
        store, _record = self._record()
        store.record_acceptance(REQUEST, fixtures.acceptance())
        for check_id in manifest_module.check_ids(self.manifest):
            store.record_check(REQUEST, check_id, "passed")
        store.record_cleanup(REQUEST, "complete")
        record = store.finalize(
            REQUEST, required=manifest_module.required_check_ids(self.manifest),
            terminal_at=END)
        built = report_module.build_report(
            manifest=self.manifest, record=record,
            harness_tests=("manifest", "ledger", "probes"),
        )
        self.assertEqual(built["classification"], "passed_live")
        self.assertEqual(len(built["live_checks_passed"]), 8)
        self.assertEqual(built["harness_locally_tested"],
                         ("ledger", "manifest", "probes"))
        self.assertEqual(built["support_tier"], "implemented_unproven")
        self.assertFalse(built["adoptable"])
        self.assertIsNone(built["evidence_id"])
        self.assertIn("t031_independent_review", built["independent_review_pending"])

    def test_a_local_fake_run_reports_no_live_checks_at_all(self):
        store, _record = self._record(provenance="local_injected_fake")
        store.record_acceptance(REQUEST, fixtures.acceptance())
        for check_id in manifest_module.check_ids(self.manifest):
            store.record_check(REQUEST, check_id, "passed")
        record = store.record_cleanup(REQUEST, "complete")
        built = report_module.build_report(manifest=self.manifest, record=record)
        self.assertEqual(built["live_checks_passed"], ())
        self.assertEqual(len(built["checks_blocked"]), 8)
        self.assertIn("t022_helper_service_proof",
                      built["independent_review_pending"])

    def test_rendering_is_deterministic_and_free_of_exception_text(self):
        store, record = self._record()
        built = report_module.build_report(manifest=self.manifest, record=record)
        first = report_module.render(built)
        second = report_module.render(report_module.build_report(
            manifest=self.manifest, record=record))
        self.assertEqual(first, second)
        self.assertIn("support_tier: implemented_unproven", first)
        self.assertIn("adoptable: false", first)
        self.assertIn("evidence_id: null", first)
        self.assertIn("local harness tests are not live proof", first)
        self.assertNotIn("Traceback", first)
        for section in report_module.SECTIONS:
            self.assertIn(f"  {section}:", first)

    def test_a_report_shows_retained_cleanup_items(self):
        store, _record = self._record()
        store.record_acceptance(REQUEST, fixtures.acceptance())
        record = store.record_cleanup(REQUEST, "incomplete")
        observations = list(fixtures.cleanup_observations(self.manifest))
        observations[0] = {**observations[0], "state": "foreign", "owned": False}
        verified = cleanup_module.verify(self.manifest, observations)
        built = report_module.build_report(
            manifest=self.manifest, record=record, cleanup=verified)
        self.assertTrue(built["cleanup_incomplete"])
        self.assertIn("foreign_resource", built["cleanup_incomplete"][0])
        self.assertIn("cleanup_incomplete: 1", report_module.render(built))

    def test_an_invalid_report_renders_a_bounded_line(self):
        self.assertEqual(report_module.render(None),
                         "credential-vault-proof: report_invalid\n")


if __name__ == "__main__":
    unittest.main()
