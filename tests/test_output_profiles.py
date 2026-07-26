import unittest

from sandbox.jobs.models import OutputProfile
from sandbox.jobs.output import present_output


class OutputProfileTests(unittest.TestCase):
    def test_sampled_errors_quiet_and_smart_are_display_only(self):
        page = {"encoding": "utf8", "data": "one\nerror two\none\nthree\n", "events": []}
        self.assertEqual(present_output(page, OutputProfile("s", mode="sampled", every_lines=2))["data"], "error two\nthree\n")
        self.assertEqual(present_output(page, OutputProfile("e", mode="errors"))["data"], "error two\n")
        self.assertEqual(present_output(page, OutputProfile("q", mode="quiet"))["data"], "")
        self.assertEqual(present_output(page, OutputProfile("smart", deduplicate=True))["data"], "one\nerror two\nthree\n")

    def test_declarative_custom_rules_keep_context_sampling_metadata_and_budgets(self):
        page = {
            "encoding": "utf8",
            "data": "start\nwarn\nFAIL build\nnoise\nend\n",
            "events": [
                {"stream": "stdout", "timestamp": 1}, {"stream": "stdout", "timestamp": 2},
                {"stream": "stderr", "timestamp": 3}, {"stream": "stdout", "timestamp": 4},
                {"stream": "stdout", "timestamp": 5},
            ],
        }
        contextual = present_output(page, OutputProfile("agent", mode="smart", include=("fail",),
            before=1, after=1, timestamps=True, stream_prefixes=True, heartbeat_seconds=30,
            max_bytes=200, max_events=3))
        self.assertEqual(contextual["data"], "[stdout] [2] warn\n[stderr] [3] FAIL build\n[stdout] [4] noise\n")
        self.assertEqual(contextual["events_read"], 3)
        self.assertEqual(contextual["presentation_heartbeat"]["interval_seconds"], 30)
        sampled = present_output(page, OutputProfile("sample", mode="sampled", every_lines=2,
            max_bytes=200, max_events=5))
        self.assertEqual(sampled["data"], "warn\nnoise\n")
        self.assertEqual(sampled["events_read"], 2)
        every_event = present_output(page, OutputProfile("events", mode="sampled", every_events=2,
            max_bytes=200, max_events=5))
        self.assertEqual(every_event["data"], "warn\nnoise\n")
        every_time = present_output(page, OutputProfile("time", mode="sampled", every_seconds=2,
            max_bytes=200, max_events=5))
        self.assertEqual(every_time["data"], "start\nFAIL build\nend\n")

    def test_declarative_patterns_and_budgets_do_not_change_retained_page(self):
        page = {"encoding": "utf8", "data": "alpha\nkeep\nexclude\n", "events": []}
        profile = OutputProfile("filtered", mode="smart", include=("a",), exclude=("exclude",),
                                max_bytes=6, max_events=1)
        result = present_output(page, profile)
        self.assertEqual(result["data"], "alpha\n")
        self.assertEqual(page["data"], "alpha\nkeep\nexclude\n")
