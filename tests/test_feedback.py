from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

from sandbox.feedback.service import FeedbackService, FeedbackStore


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


class TestFeedbackMcpAdapter(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
