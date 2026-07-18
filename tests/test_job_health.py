import unittest
from datetime import datetime, timedelta, timezone

from sandbox.jobs.health import classify


class JobHealthTests(unittest.TestCase):
    def test_terminal_and_stalled_are_evidence_based(self):
        health, _ = classify({"lifecycle": "succeeded"})
        self.assertEqual(health.value, "terminal")
        old = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
        health, evidence = classify({"lifecycle": "running", "stall_seconds": 300,
            "process": {}, "heartbeat": {"last_output_at": old}})
        self.assertEqual(health.value, "suspected_stalled")
        self.assertIn("stall", evidence["reasons"][0])
