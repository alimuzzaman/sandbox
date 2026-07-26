import unittest
import os
from datetime import datetime, timedelta, timezone

from sandbox.jobs.health import classify


class JobHealthTests(unittest.TestCase):
    def test_classification_table_covers_each_public_non_terminal_state(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=301)).isoformat()
        quiet = (now - timedelta(seconds=31)).isoformat()
        cases = {
            "active": {"lifecycle": "running", "process": {"child_pid": os.getpid()},
                       "heartbeat": {"last_output_at": now.isoformat()}},
            "quiet": {"lifecycle": "running", "process": {"child_pid": os.getpid()},
                      "heartbeat": {"last_output_at": quiet}},
            "suspected_stalled": {"lifecycle": "running", "stall_seconds": 300,
                                  "process": {}, "heartbeat": {"last_output_at": old}},
            "stuck": {"lifecycle": "running", "stall_seconds": 300, "process": {},
                      "heartbeat": {"last_output_at": (now - timedelta(seconds=601)).isoformat()}},
            "supervisor_unresponsive": {"lifecycle": "running", "stall_seconds": 300, "process": {},
                                         "heartbeat": {"supervisor_at": (now - timedelta(seconds=601)).isoformat()}},
            "orphaned": {"lifecycle": "running", "process": {"orphaned": True}},
            "process_missing": {"lifecycle": "running", "process": {"child_pid": 99999999}},
            "unreachable": {"lifecycle": "running", "target_reachable": False},
            "unknown": {"lifecycle": "running"},
            "terminal": {"lifecycle": "succeeded"},
        }
        for expected, snapshot in cases.items():
            with self.subTest(expected=expected):
                health, evidence = classify(snapshot, now=now)
                self.assertEqual(health.value, expected)
                self.assertTrue(evidence["reasons"])

    def test_terminal_and_stalled_are_evidence_based(self):
        health, _ = classify({"lifecycle": "succeeded"})
        self.assertEqual(health.value, "terminal")
        old = (datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat()
        health, evidence = classify({"lifecycle": "running", "stall_seconds": 300,
            "process": {}, "heartbeat": {"last_output_at": old}})
        self.assertEqual(health.value, "suspected_stalled")
        self.assertIn("stall", evidence["reasons"][0])

    def test_unreachable_and_explicit_orphaned_records_are_not_reported_healthy(self):
        health, evidence = classify({"lifecycle": "running", "target_reachable": False})
        self.assertEqual(health.value, "unreachable")
        self.assertIn("unreachable", evidence["reasons"][0])

        health, evidence = classify({"lifecycle": "running", "process": {"orphaned": True}})
        self.assertEqual(health.value, "orphaned")
        self.assertIn("invalid ownership", evidence["reasons"][0])

    def test_sustained_inactivity_is_stuck_and_recent_metrics_keep_quiet_jobs_healthy(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat()
        health, evidence = classify({"lifecycle": "running", "stall_seconds": 300,
            "process": {}, "heartbeat": {"last_output_at": old, "last_activity_at": old,
            "last_progress_at": old, "last_metric_at": old}})
        self.assertEqual(health.value, "stuck")
        self.assertIn("second threshold", evidence["reasons"][-1])

        recent = datetime.now(timezone.utc).isoformat()
        health, _ = classify({"lifecycle": "running", "stall_seconds": 300,
            "process": {"child_pid": 99999999},
            "heartbeat": {"last_output_at": old, "last_metric_at": recent,
                           "health_evidence": {"metric_movement": True}}})
        self.assertEqual(health.value, "process_missing")
