from __future__ import annotations

import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest

from sandbox.services.redaction import (
    REDACTION_FAILED,
    StreamingRedactor,
    argv_contains_credentials,
    redact_structure,
    redact_text,
    redact_url,
)
from tests.redaction_corpus import (
    ALL_FORBIDDEN,
    ASSIGNMENT_TOKEN,
    BASIC_PASSWORD,
    BASIC_USER,
    STRUCTURE_CASES,
    TEXT_CASES,
    nested_exception,
)


def _contains_fixture(rendered: object, values=ALL_FORBIDDEN) -> bool:
    text = rendered if isinstance(rendered, str) else json.dumps(rendered, sort_keys=True)
    return any(value in text for value in values)


class SharedRedactionTests(unittest.TestCase):
    def test_text_corpus_has_complete_value_absence(self):
        outcomes = {case.name: not _contains_fixture(redact_text(case.value), case.forbidden)
                    for case in TEXT_CASES}
        self.assertTrue(all(outcomes.values()))

    def test_url_preserves_safe_components_only(self):
        rendered = redact_url(
            f"https://{BASIC_USER}:{BASIC_PASSWORD}@example.test/path?mode=safe&token={ASSIGNMENT_TOKEN}"
        )
        self.assertFalse(_contains_fixture(rendered))
        self.assertIn("https://", rendered)
        self.assertIn("example.test/path", rendered)
        self.assertIn("mode=safe", rendered)
        self.assertIn("token=%5BREDACTED%5D", rendered)

    def test_url_redacts_exact_sandbox_autologin_query_only(self):
        rendered = redact_url(
            "https://example.test/wp-admin/?sandbox_autologin=fixture-login"
            "&public_sandbox_autologin=prefix-public"
            "&sandbox_autologin_hint=suffix-public&mode=safe"
        )
        self.assertEqual(
            rendered,
            "https://example.test/wp-admin/?sandbox_autologin=%5BREDACTED%5D"
            "&public_sandbox_autologin=prefix-public"
            "&sandbox_autologin_hint=suffix-public&mode=safe",
        )
        self.assertNotIn("fixture-login", rendered)

    def test_structure_and_exception_chains_are_recursive(self):
        outcomes = {
            case.name: not _contains_fixture(redact_structure(case.value), case.forbidden)
            for case in STRUCTURE_CASES
        }
        outcomes["nested_exception"] = not _contains_fixture(redact_structure(nested_exception()))
        self.assertTrue(all(outcomes.values()))

    def test_streaming_redacts_patterns_split_across_chunks(self):
        redactor = StreamingRedactor()
        source = f"token={ASSIGNMENT_TOKEN}\n".encode()
        cut = len(source) // 2
        rendered = redactor.feed(source[:cut]) + redactor.feed(source[cut:]) + redactor.finish()
        self.assertFalse(_contains_fixture(rendered.decode()))

    def test_streaming_retains_credential_context_at_every_split_through_flush(self):
        cases = {
            "authorization": f"prefix Authorization: Bearer {ASSIGNMENT_TOKEN}\n",
            "spaced_assignment": f"prefix token = {ASSIGNMENT_TOKEN}\n",
        }
        outcomes = {}
        for name, source in cases.items():
            raw = source.encode()
            for cut in range(1, len(raw)):
                redactor = StreamingRedactor()
                rendered = redactor.feed(raw[:cut]) + redactor.feed(raw[cut:]) + redactor.finish()
                outcomes[(name, cut)] = not _contains_fixture(rendered.decode())
        self.assertTrue(all(outcomes.values()))

    def test_streaming_overlong_unclassified_line_fails_closed(self):
        redactor = StreamingRedactor()
        rendered = redactor.feed(b"x" * (redactor.max_pending_bytes + 1)) + redactor.finish()
        self.assertEqual(rendered.decode(), REDACTION_FAILED)

    def test_credential_like_argv_is_detected_without_rewriting(self):
        unsafe = [
            ["tool", "--token", ASSIGNMENT_TOKEN],
            ["tool", f"api_key={ASSIGNMENT_TOKEN}"],
            ["tool", f"https://{BASIC_USER}:{BASIC_PASSWORD}@example.test"],
        ]
        self.assertTrue(all(argv_contains_credentials(item) for item in unsafe))
        self.assertFalse(argv_contains_credentials(["tool", "--mode", "safe"]))

    def test_argv_classification_ignores_safe_url_normalization(self):
        allowed = [
            ["tool", "https://example.test/path#safe-section"],
            ["tool", "https://example.test/path?next=a%2Fb#safe-section"],
        ]
        refused = [
            ["tool", f"https://{BASIC_USER}:{BASIC_PASSWORD}@example.test/path"],
            ["tool", f"https://example.test/path?token={ASSIGNMENT_TOKEN}"],
        ]
        self.assertTrue(all(not argv_contains_credentials(item) for item in allowed))
        self.assertTrue(all(argv_contains_credentials(item) for item in refused))

    def test_fail_closed_public_boundaries_do_not_echo_invalid_objects(self):
        self.assertEqual(redact_text(object()), REDACTION_FAILED)
        self.assertEqual(redact_url(object()), REDACTION_FAILED)


class SurfaceParityTests(unittest.TestCase):
    def test_names_and_counts_evidence_contains_no_fixture_value(self):
        evidence_path = (
            Path(__file__).parent.parent / "specs/041-safe-secret-inspection/evidence/redaction-parity.json"
        )
        evidence_text = evidence_path.read_text(encoding="utf-8")
        evidence = json.loads(evidence_text)
        self.assertFalse(_contains_fixture(evidence_text))
        self.assertEqual(evidence["aggregate"]["fixture_value_matches"], 0)
        self.assertEqual(evidence["aggregate"]["surface_pattern_failures"], 0)

    def test_named_surface_matrix_has_zero_complete_fixture_values(self):
        surface_results = {}
        corpus_text = "\n".join(str(case.value) for case in TEXT_CASES)

        from sandbox.commands.secrets import _emit
        cli_outputs = []
        for as_json in (False, True):
            output = io.StringIO()
            with redirect_stdout(output):
                _emit({"ok": True, "operation": "run", "result": {
                    "output": corpus_text, "termination": "exited",
                }}, as_json)
            cli_outputs.append(output.getvalue())
        surface_results["cli"] = not _contains_fixture(cli_outputs)

        mcp_root = Path(__file__).parent.parent / "mcp/wp-server"
        sys.path.insert(0, str(mcp_root))
        try:
            from tools import secrets as mcp_secrets
            surface_results["mcp"] = not _contains_fixture(
                mcp_secrets._safe(lambda: {"ok": True, "details": [
                    case.value for case in TEXT_CASES
                ]})
            )
        finally:
            sys.path.remove(str(mcp_root))

        from sandbox.feedback.service import FeedbackService, FeedbackStore
        with tempfile.TemporaryDirectory() as temp:
            feedback = FeedbackService(FeedbackStore(Path(temp) / "feedback"))
            receipt = feedback.submit("synthetic redaction corpus", details=corpus_text)
            stored = "".join(path.read_text() for path in (Path(temp) / "feedback").glob("*.json"))
            surface_results["feedback"] = not _contains_fixture((receipt, stored))

        from sandbox.transports.remote_jobs import _last_json
        remote_job = _last_json(json.dumps({
            "ok": True, "details": [case.value for case in TEXT_CASES],
        }))
        surface_results["remote_job"] = not _contains_fixture(remote_job)

        from sandbox.transports.remote_workspaces import _parse_envelope
        remote_workspace = _parse_envelope(json.dumps({
            "ok": False, "code": "fixture_failed",
            "details": [case.value for case in TEXT_CASES],
        }))
        surface_results["remote_workspace"] = not _contains_fixture(remote_workspace)

        from sandbox.core._remote import redact_ssh_connection
        remote_detail = redact_ssh_connection(corpus_text)
        surface_results["remote_verification"] = not _contains_fixture(remote_detail)

        from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity
        from sandbox.jobs.output import JobOutputStore
        from sandbox.jobs.registry import JobRepository
        from sandbox.jobs.storage import JobStorage
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "jobs.sqlite")
            try:
                job, _ = repository.accept(JobSubmission(
                    "test", temp, "fixture", "local", "unit", ("tool", "safe"), 10,
                    SourceIdentity("fixture"),
                ))
                storage = JobStorage(temp, free_disk_reserve=0)
                storage.job_dir(job["job_id"], create=True)
                retained = JobOutputStore(storage, repository, job["job_id"])
                retained.append("stderr", (corpus_text + "\n").encode())
                retained.finish("stderr")
                surface_results["job_output"] = not _contains_fixture(
                    retained.read(OutputQuery())["data"]
                )
            finally:
                repository.close()

        from sandbox.services.process import BoundedProcessRunner
        process_result = BoundedProcessRunner().run(
            [sys.executable, "-c", "import os,sys;sys.stderr.write(os.environ['FIXTURE_OUTPUT'])"],
            env={"FIXTURE_OUTPUT": corpus_text}, timeout=5,
        )
        surface_results["process_stderr"] = not _contains_fixture(process_result.stderr)

        from sandbox.secrets.runner import run_with_secret
        child_outputs = []
        for case in TEXT_CASES:
            child = run_with_secret(
                [sys.executable, "-c", "import os;print(os.environ['FIXTURE_VALUE'])"],
                destination="FIXTURE_VALUE", value=str(case.value), timeout_seconds=5,
            )
            child_outputs.append(child.output)
        surface_results["secret_child"] = not _contains_fixture(child_outputs)

        self.assertEqual(set(surface_results), {
            "cli", "mcp", "feedback", "remote_job", "remote_workspace",
            "remote_verification", "job_output", "process_stderr", "secret_child",
        })
        self.assertTrue(all(surface_results.values()))

    def test_job_output_store_redacts_whitespace_splits_before_durable_write(self):
        from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity
        from sandbox.jobs.output import JobOutputStore
        from sandbox.jobs.registry import JobRepository
        from sandbox.jobs.storage import JobStorage

        chunks = []
        for source, cut in (
            (f"Authorization: Bearer {ASSIGNMENT_TOKEN}\n".encode(), 22),
            (f"Authorization: Bearer {ASSIGNMENT_TOKEN}\n".encode(), 23),
            (f"token = {ASSIGNMENT_TOKEN}\n".encode(), 7),
            (f"token = {ASSIGNMENT_TOKEN}\n".encode(), 8),
        ):
            chunks.extend((source[:cut], source[cut:]))
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "jobs.sqlite")
            try:
                job, _ = repository.accept(JobSubmission(
                    "test", temp, "fixture", "local", "unit", ("tool", "safe"), 10,
                    SourceIdentity("fixture"),
                ))
                storage = JobStorage(temp, free_disk_reserve=0)
                storage.job_dir(job["job_id"], create=True)
                retained = JobOutputStore(storage, repository, job["job_id"])
                for chunk in chunks:
                    retained.append("stderr", chunk)
                retained.finish("stderr")
                retained.complete()
                rendered = retained.read(OutputQuery())["data"]
                self.assertFalse(_contains_fixture(rendered))
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
