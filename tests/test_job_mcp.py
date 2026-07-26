import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MCP_ROOT = Path(__file__).parent.parent / "mcp" / "wp-server"
sys.path.insert(0, str(MCP_ROOT))


class JobMcpTests(unittest.TestCase):
    def test_follow_returns_bounded_monotonic_request_progress(self):
        from tools import jobs

        page = {"ok": True, "cursor": "next", "events_read": 3, "has_more": False}
        with patch.object(jobs, "job_output", return_value=page) as output:
            result = jobs.job_follow("a" * 32, cursor="start", max_updates=3,
                                     max_duration_seconds=2, progress_token="request-1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["cursor"], "next")
        self.assertEqual(len(result["updates"]), 1)
        self.assertEqual(result["progress"], [{"token": "request-1", "current": 1,
                                                "total": 3, "events_observed": 3}])
        self.assertEqual(output.call_args.kwargs["wait_seconds"], 1)

    def test_follow_rejects_unbounded_or_invalid_request_progress_inputs(self):
        from tools import jobs

        for kwargs in (
            {"max_updates": 0}, {"max_updates": 21}, {"max_duration_seconds": 0},
            {"max_duration_seconds": 21}, {"progress_token": ""},
        ):
            with self.subTest(kwargs=kwargs):
                result = jobs.job_follow("a" * 32, **kwargs)
                self.assertEqual(result["code"], "invalid_follow_query")
