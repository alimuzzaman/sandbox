from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest.mock import patch

from sandbox.feedback.service import (
    MAX_RECORD_BYTES, FeedbackRecordError, FeedbackService, FeedbackStore,
)


ROOT = Path(__file__).parent.parent


class TestFeedbackService(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "plugin-project"
        self.project.mkdir()
        self.service = FeedbackService(
            FeedbackStore(self.root / "feedback"),
            clock=lambda: datetime(2026, 8, 12, 8, 30, tzinfo=timezone.utc),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_submit_persists_owner_only_redacted_untrusted_record(self):
        payload = self.service.submit(
            "Harness failed with token=do-not-store",
            details="authorization: bearer hidden-value",
            category="incident",
            severity="high",
            source="codex agent",
            project_dir=str(self.project),
            remote="scaleway-sandbox",
            reference="job-123",
        )

        self.assertTrue(payload["ok"])
        record = payload["data"]["feedback"]
        self.assertEqual(record["trust"], "untrusted_data")
        self.assertTrue(record["redacted"])
        self.assertNotIn("do-not-store", json.dumps(record))
        self.assertNotIn("hidden-value", json.dumps(record))
        self.assertEqual(record["project"]["name"], "plugin-project")
        self.assertNotIn(str(self.project), json.dumps(record))

        stored = list((self.root / "feedback").glob("*.json"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stat.S_IMODE(stored[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(stored[0].parent.stat().st_mode), 0o700)

    def test_new_records_are_unreviewed_and_reviews_are_append_only(self):
        submitted = self.service.submit("needs review")
        self.assertTrue(submitted["ok"])
        feedback = submitted["data"]["feedback"]
        self.assertEqual(feedback["status"], "unreviewed")
        self.assertIsNone(feedback["closure"])
        self.assertFalse(self.service.store.review_path.exists())

        reviewed = self.service.review(
            feedback["feedback_id"],
            status="resolved",
            reviewer="codex",
            reason="Source and regression tests prove the fix.",
            evidence=["commit:abc1234", "tests:test_feedback"],
            confidence="high",
        )
        self.assertTrue(reviewed["ok"])
        self.assertEqual(reviewed["data"]["feedback"]["status"], "resolved")
        self.assertEqual(
            reviewed["data"]["feedback"]["closure"]["evidence"],
            ["commit:abc1234", "tests:test_feedback"],
        )
        self.assertEqual(
            len(list((self.root / "feedback").glob("*.json"))), 1,
        )
        self.assertEqual(stat.S_IMODE(self.service.store.review_path.stat().st_mode), 0o600)

        listed = self.service.list(10)
        self.assertEqual(listed["data"]["feedback"][0]["status"], "resolved")
        self.assertEqual(listed["data"]["invalid_review_count"], 0)
        exported = self.service.export(10)
        self.assertTrue(exported["ok"])
        self.assertEqual(exported["data"]["feedback"][0]["status"], "resolved")

    def test_review_redacts_reason_and_counts_statuses_with_filters(self):
        first = self.service.submit("first", category="bug", severity="high")
        second = self.service.submit("second", category="idea", severity="low")
        self.assertTrue(first["ok"] and second["ok"])
        first_id = first["data"]["feedback"]["feedback_id"]
        second_id = second["data"]["feedback"]["feedback_id"]
        self.assertTrue(self.service.review(
            first_id,
            status="blocked",
            reason="Waiting for token=redacted-value",
            evidence=["host:scaleway-sandbox"],
            confidence="medium",
        )["ok"])
        self.assertTrue(self.service.review(
            second_id,
            status="verified",
            reason="Independent acceptance evidence is present.",
            evidence=["tests:test_feedback"],
            confidence="high",
        )["ok"])

        counts = self.service.counts()
        self.assertTrue(counts["ok"])
        self.assertEqual(counts["data"]["count"], 2)
        self.assertEqual(counts["data"]["reviewed"], 2)
        self.assertEqual(counts["data"]["unreviewed"], 0)
        self.assertEqual(counts["data"]["by_status"]["blocked"], 1)
        self.assertEqual(counts["data"]["by_status"]["verified"], 1)
        self.assertEqual(counts["data"]["by_category"]["bug"], 1)
        self.assertNotIn("redacted-value", json.dumps(counts))

        filtered = self.service.list(10, status="blocked")
        self.assertEqual([item["feedback_id"] for item in filtered["data"]["feedback"]], [first_id])
        filtered_counts = self.service.counts(status="verified")
        self.assertEqual(filtered_counts["data"]["count"], 1)
        self.assertEqual(filtered_counts["data"]["by_status"]["verified"], 1)

    def test_invalid_review_lines_are_withheld_and_latest_event_wins(self):
        submitted = self.service.submit("event history")
        feedback_id = submitted["data"]["feedback"]["feedback_id"]
        self.assertTrue(self.service.review(
            feedback_id, status="open", reason="Initial triage", confidence="low",
        )["ok"])
        self.assertTrue(self.service.review(
            feedback_id, status="in_progress", reason="Work is underway", confidence="medium",
        )["ok"])
        with self.service.store.review_path.open("a", encoding="utf-8") as handle:
            handle.write("not-json\n")
        listed = self.service.list(10)
        self.assertEqual(listed["data"]["feedback"][0]["status"], "in_progress")
        self.assertEqual(listed["data"]["invalid_review_count"], 1)

    def test_cli_parser_exposes_review_and_counts_actions(self):
        from sandbox.commands.feedback import configure_parser

        parser = argparse.ArgumentParser()
        configure_parser(parser)
        parsed = parser.parse_args([
            "review", "a" * 32, "--status", "verified", "--reason", "accepted",
            "--evidence", "tests:test_feedback", "--json",
        ])
        self.assertEqual(parsed.action, "review")
        self.assertEqual(parsed.status, "verified")
        self.assertEqual(parser.parse_args(["counts"]).action, "counts")

    def test_list_is_newest_first_and_withholds_invalid_records(self):
        first = self.service.submit("first", category="bug")
        self.assertTrue(first["ok"])
        second_service = FeedbackService(
            self.service.store,
            clock=lambda: datetime(2026, 8, 12, 8, 31, tzinfo=timezone.utc),
        )
        self.assertTrue(second_service.submit("second", category="idea")["ok"])
        invalid = self.root / "feedback" / "99999999-invalid.json"
        invalid.write_text("not-json", encoding="utf-8")

        payload = self.service.list(10)

        self.assertTrue(payload["ok"])
        self.assertEqual([item["summary"] for item in payload["data"]["feedback"]], [
            "second", "first",
        ])
        self.assertEqual(payload["data"]["invalid_record_count"], 1)

    def test_invalid_values_refuse_without_creating_a_record(self):
        payload = self.service.submit("", category="bug")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_feedback")
        self.assertFalse((self.root / "feedback").exists())

        limit = self.service.list(101)
        self.assertFalse(limit["ok"])
        self.assertEqual(limit["error"]["code"], "invalid_feedback")

    def _write_feedback_record(self, feedback_id: str, *, stamp: str, summary: str = "safe") -> Path:
        path = self.root / "feedback"
        path.mkdir(exist_ok=True)
        target = path / f"{stamp}-{feedback_id}.json"
        target.write_text(json.dumps({
            "schema_version": 1,
            "feedback_id": feedback_id,
            "created_at": "2026-08-12T08:30:00Z",
            "category": "bug",
            "severity": "high",
            "source": "agent",
            "summary": summary,
            "details": "details",
            "reference": "",
            "remote": None,
            "project": None,
            "redacted": False,
            "trust": "untrusted_data",
        }), encoding="utf-8")
        return target

    def test_show_and_detail_resolve_unique_lower_hex_prefix(self):
        feedback_id = "0123abcd" + "e" * 24
        self._write_feedback_record(feedback_id, stamp="20260812T083000Z", summary="prefix target")

        shown = self.service.show("0123abcd")
        detailed = self.service.detail("0123abcd")

        self.assertTrue(shown["ok"])
        self.assertEqual(shown["data"]["feedback"]["feedback_id"], feedback_id)
        self.assertTrue(detailed["ok"])
        self.assertEqual(detailed["action"], "detail")
        self.assertEqual(detailed["data"]["feedback"]["summary"], "prefix target")

    def test_prefix_references_fail_closed_for_invalid_missing_and_ambiguous(self):
        store = self.service.store
        with patch.object(store, "_paths", side_effect=AssertionError("must not scan")):
            invalid = self.service.show("not-a-feedback-ref")
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "invalid_feedback")

        missing = self.service.show("0123abcd")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "feedback_not_found")

        first = "abcd1234" + "a" * 24
        second = "abcd1234" + "b" * 24
        self._write_feedback_record(first, stamp="20260812T083000Z", summary="candidate one")
        self._write_feedback_record(second, stamp="20260812T083001Z", summary="candidate two")
        ambiguous = self.service.show("abcd1234")
        self.assertFalse(ambiguous["ok"])
        self.assertEqual(ambiguous["error"]["code"], "feedback_id_ambiguous")
        self.assertEqual(ambiguous["data"], {})
        self.assertNotIn(first, json.dumps(ambiguous))
        self.assertNotIn(second, json.dumps(ambiguous))

    def test_prefix_duplicate_canonical_id_uses_newest_and_skips_invalid_records(self):
        feedback_id = "deadbeef" + "1" * 24
        self._write_feedback_record(feedback_id, stamp="20260812T083000Z", summary="old")
        self._write_feedback_record(feedback_id, stamp="20260812T083001Z", summary="new")
        feedback_dir = self.root / "feedback"
        (feedback_dir / "20260812T083002Z-deadbeef-invalid.json").write_text("not-json", encoding="utf-8")
        symlink_target = feedback_dir / "20260812T082900Z-deadbeef-symlink-target.json"
        symlink_target.write_text(json.dumps({"schema_version": 1, "feedback_id": feedback_id}), encoding="utf-8")
        (feedback_dir / "20260812T083003Z-deadbeef-symlink.json").symlink_to(symlink_target)

        resolved = self.service.show("deadbeef")
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["data"]["feedback"]["summary"], "new")

    def test_cli_and_mcp_show_detail_forward_the_same_prefix_reference(self):
        from sandbox.commands import feedback as cli_feedback

        class Service:
            def __init__(self):
                self.calls = []

            def show(self, reference):
                self.calls.append(("show", reference))
                return {"ok": True, "action": "show", "data": {"feedback": {}}}

            def detail(self, reference):
                self.calls.append(("detail", reference))
                return {"ok": True, "action": "detail", "data": {"feedback": {}}}

        service = Service()
        for action in ("show", "detail"):
            args = SimpleNamespace(
                action=action, feedback_id="0123abcd", feedback_id_option=None,
                json=True,
            )
            with patch.object(cli_feedback, "feedback_service", return_value=service), \
                    patch("sys.stdout", new_callable=io.StringIO):
                cli_feedback.cmd_feedback(None, args)

        path = ROOT / "mcp" / "wp-server" / "tools" / "feedback.py"
        spec = importlib.util.spec_from_file_location("feedback_prefix_parity", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        module._service_factory = lambda: service
        module.feedback_list(action="show", feedback_id="0123abcd")
        module.feedback_list(action="detail", feedback_id="0123abcd")

        self.assertEqual(service.calls, [
            ("show", "0123abcd"), ("detail", "0123abcd"),
            ("show", "0123abcd"), ("detail", "0123abcd"),
        ])

    def test_invalid_limit_reports_the_supported_range(self):
        payload = self.service.list(101)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["message"], "limit must be between 1 and 100")

    def test_cli_limit_help_discloses_range_and_default(self):
        from sandbox.commands.feedback import configure_parser

        parser = argparse.ArgumentParser()
        configure_parser(parser)

        limit = next(action for action in parser._actions if action.dest == "limit")
        expected = "maximum records to return (1-100; default: 20)"
        self.assertEqual(limit.help, expected)
        self.assertEqual(limit.default, 20)
        self.assertIn(expected, " ".join(parser.format_help().split()))

    def test_regression_f90c671_invalid_count_is_independent_of_display_limit(self):
        self.assertTrue(self.service.submit("valid")["ok"])
        # Sorts after the valid record, so the old implementation stopped
        # before seeing it when limit=1.
        (self.root / "feedback" / "000-invalid.json").write_text("not-json", encoding="utf-8")

        payload = self.service.list(1)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["count"], 1)
        self.assertEqual(payload["data"]["invalid_record_count"], 1)

    def test_regression_f90c671_explicit_invalid_limits_fail_before_reading(self):
        for invalid in (0, -1, 101, True, False, "1"):
            with self.subTest(limit=invalid):
                payload = self.service.list(invalid)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "invalid_feedback")

    def test_regression_untrusted_legacy_keys_are_not_disclosed_by_reads(self):
        tampered = {
            "schema_version": 1,
            "feedback_id": "a" * 32,
            "created_at": "2026-08-13T00:00:00Z",
            "category": "bug",
            "severity": "high",
            "source": "agent",
            "summary": "safe summary",
            "details": "safe details",
            "reference": "safe reference",
            "remote": None,
            "project": {
                "identity": "b" * 64,
                "name": "fixture",
                "legacy_nested": {"password": "PROJECT-NESTED-PASSWORD"},
            },
            "redacted": False,
            "trust": "untrusted_data",
            "unknown_top_level": "TOP-LEVEL-SECRET",
            "legacy_nested": {"token": "TOP-LEVEL-NESTED-TOKEN"},
        }
        path = self.root / "feedback"
        path.mkdir()
        record_path = path / ("20260813T000000Z-" + "a" * 32 + ".json")
        record_path.write_text(json.dumps(tampered), encoding="utf-8")

        listed = self.service.list(20)
        shown = self.service.show("a" * 32)
        detailed = self.service.detail("a" * 32)
        exported = self.service.export(20)

        self.assertTrue(listed["ok"])
        self.assertTrue(shown["ok"])
        self.assertTrue(exported["ok"])
        for payload in (listed, shown, detailed):
            feedback = payload["data"]["feedback"]
            if isinstance(feedback, list):
                self.assertEqual(len(feedback), 1)
                feedback = feedback[0]
            self.assertNotIn("unknown_top_level", feedback)
            self.assertNotIn("legacy_nested", feedback)
            self.assertNotIn("legacy_nested", feedback["project"])
            rendered = json.dumps(payload)
            for secret in ("TOP-LEVEL-SECRET", "TOP-LEVEL-NESTED-TOKEN", "PROJECT-NESTED-PASSWORD"):
                self.assertNotIn(secret, rendered)
        self.assertNotIn("unknown_top_level", exported["data"]["content"])
        self.assertNotIn("legacy_nested", exported["data"]["content"])
        for secret in ("TOP-LEVEL-SECRET", "TOP-LEVEL-NESTED-TOKEN", "PROJECT-NESTED-PASSWORD"):
            self.assertNotIn(secret, exported["data"]["content"])
        self.assertEqual(record_path.read_text(encoding="utf-8"), json.dumps(tampered))

        from sandbox.commands.feedback import _emit
        output = io.StringIO()
        with redirect_stdout(output):
            _emit(listed, True)
        self.assertNotIn("unknown_top_level", output.getvalue())
        self.assertNotIn("TOP-LEVEL-SECRET", output.getvalue())

    def test_regression_81f43e6f_provider_tokens_and_basic_auth_are_redacted(self):
        payload = self.service.submit(
            "AKIAIOSFODNN7EXAMPLE xoxb-123456789012-123456789012-abc",
            details=(
                "sk_live_ABCDEFGHIJKLMNOPQRST AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890 "
                "Bearer eyJhbGciOiJIUzI1NiJ9 https://alice:secret@example.com"
            ),
        )

        self.assertTrue(payload["ok"])
        record_json = json.dumps(payload["data"]["feedback"])
        for secret in (
            "AKIAIOSFODNN7EXAMPLE", "xoxb-123456789012-123456789012-abc",
            "sk_live_ABCDEFGHIJKLMNOPQRST", "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            "eyJhbGciOiJIUzI1NiJ9", "alice:secret",
        ):
            self.assertNotIn(secret, record_json)
        self.assertTrue(payload["data"]["feedback"]["redacted"])

    def test_regression_2b080bf5_project_context_is_explicit_and_export_is_path_free(self):
        submitted = self.service.submit("explicit", project_dir=str(self.project))
        self.assertTrue(submitted["ok"])
        record = submitted["data"]["feedback"]
        self.assertEqual(record["project"]["name"], "plugin-project")

        exported = self.service.export(10)

        self.assertTrue(exported["ok"])
        self.assertNotIn(str(self.project), exported["data"]["content"])
        self.assertEqual(exported["data"]["feedback"][0]["project"]["name"], "plugin-project")

    def test_regression_ad190c71_filters_cursor_show_and_bounded_export(self):
        first = self.service.submit("bug one", category="bug", severity="high", source="worker")
        self.assertTrue(first["ok"])
        second_service = FeedbackService(
            self.service.store,
            clock=lambda: datetime(2026, 8, 12, 8, 31, tzinfo=timezone.utc),
        )
        second = second_service.submit("idea two", category="idea", severity="low", source="worker")
        self.assertTrue(second["ok"])

        filtered = self.service.list(1, category="bug")
        self.assertEqual([item["summary"] for item in filtered["data"]["feedback"]], ["bug one"])
        self.assertFalse(filtered["data"]["has_more"])
        self.assertTrue(self.service.show(first["data"]["feedback"]["feedback_id"])["ok"])
        page = self.service.list(1)
        self.assertTrue(page["data"]["has_more"])
        resumed = self.service.list(1, page["data"]["next_cursor"])
        self.assertEqual([item["summary"] for item in resumed["data"]["feedback"]], ["bug one"])
        exported = self.service.export(1, max_bytes=100_000)
        self.assertTrue(exported["ok"])
        self.assertLessEqual(exported["data"]["bytes"], 100_000)

    def test_regression_eb496b17_date_filters_return_json_safe_receipts(self):
        self.assertTrue(self.service.submit("dated feedback", category="bug")["ok"])

        payload = self.service.list(
            10,
            since="2026-08-12T08:00:00Z",
            until=datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["filters"]["since"], "2026-08-12T08:00:00Z")
        self.assertEqual(payload["data"]["filters"]["until"], "2026-08-12T09:00:00Z")
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_malformed_typed_record_is_withheld_from_since_page_as_valid_json(self):
        malformed = self._write_feedback_record(
            "f" * 32, stamp="20260812T083100Z", summary="malformed",
        )
        document = json.loads(malformed.read_text(encoding="utf-8"))
        document["feedback_id"] = []
        malformed.write_text(json.dumps(document), encoding="utf-8")

        payload = self.service.list(1, since="2026-08-12T08:00:00Z")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["feedback"], [])
        self.assertEqual(payload["data"]["invalid_record_count"], 1)
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_malformed_record_reader_raises_stable_typed_error(self):
        malformed = self._write_feedback_record(
            "e" * 32, stamp="20260812T083100Z", summary="malformed",
        )
        document = json.loads(malformed.read_text(encoding="utf-8"))
        document["created_at"] = {"hostile": True}
        malformed.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaises(FeedbackRecordError) as caught:
            self.service.store._read(malformed)

        self.assertEqual(caught.exception.code, "feedback_record_field_invalid")
        self.assertEqual(caught.exception.field, "created_at")

    def test_oversized_record_is_not_parsed_and_is_counted_as_invalid(self):
        oversized = self.root / "feedback" / (
            "20260812T083100Z-" + "d" * 32 + ".json"
        )
        oversized.parent.mkdir(parents=True)
        oversized.write_bytes(b"{" + b"x" * MAX_RECORD_BYTES + b"}")

        with patch("sandbox.feedback.service.json.loads") as loads:
            payload = self.service.list(1, since="2026-08-12T08:00:00Z")

        loads.assert_not_called()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["feedback"], [])
        self.assertEqual(payload["data"]["invalid_record_count"], 1)
        self.assertEqual(json.loads(json.dumps(payload)), payload)

        with self.assertRaises(FeedbackRecordError) as caught:
            self.service.store._read(oversized)
        self.assertEqual(caught.exception.code, "feedback_record_too_large")

    def test_symlink_and_non_regular_record_entries_are_counted_without_reading(self):
        records = self.root / "feedback"
        records.mkdir(parents=True)
        outside = self.root / "outside.json"
        outside.write_text('{"secret":"must-not-be-read"}', encoding="utf-8")
        symlink = records / ("20260812T083100Z-" + "b" * 32 + ".json")
        symlink.symlink_to(outside)
        non_regular = records / ("20260812T083101Z-" + "c" * 32 + ".json")
        non_regular.mkdir()

        payload = self.service.list(10)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["feedback"], [])
        self.assertEqual(payload["data"]["invalid_record_count"], 2)
        self.assertNotIn("must-not-be-read", json.dumps(payload))
        for path in (symlink, non_regular):
            with self.assertRaises(FeedbackRecordError) as caught:
                self.service.store._read(path)
            self.assertEqual(caught.exception.code, "feedback_record_not_regular")

    def test_regression_retention_and_prune_never_delete_without_confirmation(self):
        old_service = FeedbackService(
            self.service.store,
            clock=lambda: datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        old = old_service.submit("old")
        self.assertTrue(old["ok"])
        retained_path = next((self.root / "feedback").glob("*.json"))

        planned = self.service.prune(retention_days=0)

        self.assertTrue(planned["ok"])
        self.assertEqual(planned["data"]["deleted"], 0)
        self.assertTrue(retained_path.exists())

        applied = self.service.prune(retention_days=0, confirm=True)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["data"]["deleted"], 0)
        self.assertEqual(applied["data"]["deletion"], "disabled_append_only")
        self.assertTrue(retained_path.exists())

    def test_regression_cli_and_mcp_do_not_infer_different_project_contexts(self):
        import importlib.util
        from sandbox.commands import feedback as cli_feedback

        class Service:
            def __init__(self):
                self.kwargs = None

            def submit(self, _summary, **kwargs):
                self.kwargs = kwargs
                return {"ok": True, "action": "submit", "status": "recorded", "data": {"feedback": {}}}

        cli_service = Service()
        args = SimpleNamespace(
            action="submit", summary="same", details="", category=None, severity=None,
            source=None, project_dir=None, project_name=None, remote=None, reference="",
            json=True,
        )
        with patch.object(cli_feedback, "feedback_service", return_value=cli_service):
            cli_feedback.cmd_feedback(None, args)

        path = ROOT / "mcp" / "wp-server" / "tools" / "feedback.py"
        spec = importlib.util.spec_from_file_location("feedback_tool_context_parity", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        mcp_service = Service()
        module._service_factory = lambda: mcp_service
        module.feedback_submit("same")

        self.assertIsNone(cli_service.kwargs["project_dir"])
        self.assertIsNone(mcp_service.kwargs["project_dir"])
        self.assertEqual(
            cli_service.kwargs.get("project_name"),
            mcp_service.kwargs.get("project_name"),
        )


class TestFeedbackMcpAdapter(unittest.TestCase):
    def test_feedback_list_docstring_discloses_range_and_default(self):
        path = ROOT / "mcp" / "wp-server" / "tools" / "feedback.py"
        spec = importlib.util.spec_from_file_location("feedback_tool_docstring", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        self.assertIn("maximum records to return (1-100; default: 20)",
                      module.feedback_list.__doc__ or "")

    def test_adapter_registers_and_delegates_to_shared_service(self):
        path = ROOT / "mcp" / "wp-server" / "tools" / "feedback.py"
        spec = importlib.util.spec_from_file_location("feedback_tool_under_test", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        class Service:
            def __init__(self):
                self.calls = []

            def submit(self, summary, **kwargs):
                self.calls.append(("submit", summary, kwargs))
                return {"ok": True, "action": "submit"}

            def list(self, limit):
                self.calls.append(("list", limit))
                return {"ok": True, "action": "list"}

        class Dependencies:
            def __init__(self, service):
                self.service = service

            def require(self, name):
                self.test_name = name
                return self.service

        class Server:
            def __init__(self):
                self.names = []

            def tool(self):
                def decorator(function):
                    self.names.append(function.__name__)
                    return function
                return decorator

        service = Service()
        dependencies = Dependencies(lambda: service)
        server = Server()
        module.register(server, dependencies)

        self.assertEqual(dependencies.test_name, "feedback_service_factory")
        self.assertEqual(server.names, ["feedback_submit", "feedback_list"])
        self.assertTrue(module.feedback_submit("finding", category="bug")["ok"])
        self.assertTrue(module.feedback_list(5)["ok"])
        self.assertEqual(service.calls[0][0:2], ("submit", "finding"))
        self.assertEqual(service.calls[1], ("list", 5))

    def test_regression_untrusted_legacy_keys_are_not_disclosed_by_mcp_reads(self):
        path = ROOT / "mcp" / "wp-server" / "tools" / "feedback.py"
        spec = importlib.util.spec_from_file_location("feedback_tool_read_safety", path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = FeedbackStore(root / "feedback")
            store.root.mkdir()
            feedback_id = "c" * 32
            (store.root / ("20260813T000000Z-" + feedback_id + ".json")).write_text(
                json.dumps({
                    "schema_version": 1,
                    "feedback_id": feedback_id,
                    "created_at": "2026-08-13T00:00:00Z",
                    "category": "bug",
                    "severity": "high",
                    "source": "agent",
                    "summary": "safe",
                    "details": "safe",
                    "reference": "",
                    "remote": None,
                    "project": {"identity": "d" * 64, "name": "fixture", "extra": {"secret": "MCP-NESTED-SECRET"}},
                    "redacted": False,
                    "trust": "untrusted_data",
                    "legacy": {"api_key": "MCP-TOP-SECRET"},
                }),
                encoding="utf-8",
            )
            service = FeedbackService(store)
            module._service_factory = lambda: service

            listed = module.feedback_list(20)
            shown = module.feedback_list(action="show", feedback_id=feedback_id)
            detailed = module.feedback_list(action="detail", feedback_id=feedback_id)
            exported = module.feedback_list(action="export", limit=20)

            for payload in (listed, shown, detailed, exported):
                rendered = json.dumps(payload)
                self.assertNotIn("MCP-TOP-SECRET", rendered)
                self.assertNotIn("MCP-NESTED-SECRET", rendered)
                self.assertNotIn('"legacy"', rendered)
                self.assertNotIn('"extra"', rendered)


if __name__ == "__main__":
    unittest.main()
