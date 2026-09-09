import unittest
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sandbox.jobs.health import classify
from sandbox.jobs.process import ProcessIdentity


BOOT = "11111111-2222-3333-4444-555555555555"


class JobHealthTests(unittest.TestCase):
    def test_classification_table_covers_each_public_non_terminal_state(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(seconds=301)).isoformat()
        quiet = (now - timedelta(seconds=31)).isoformat()
        cases = {
            "active": {"lifecycle": "running", "process": {
                           "host_boot_id": BOOT, "child_pid": os.getpid(),
                           "child_start_identity": "child", "supervisor_nonce_hash": "nonce"},
                       "heartbeat": {"last_output_at": now.isoformat()}},
            "quiet": {"lifecycle": "running", "process": {
                      "host_boot_id": BOOT, "child_pid": os.getpid(),
                      "child_start_identity": "child", "supervisor_nonce_hash": "nonce"},
                      "heartbeat": {"last_output_at": quiet}},
            "suspected_stalled": {"lifecycle": "running", "stall_seconds": 300,
                                  "process": {}, "heartbeat": {"last_output_at": old}},
            "stuck": {"lifecycle": "running", "stall_seconds": 300, "process": {},
                      "heartbeat": {"last_output_at": (now - timedelta(seconds=601)).isoformat()}},
            "supervisor_unresponsive": {"lifecycle": "running", "stall_seconds": 300, "process": {},
                                         "heartbeat": {"supervisor_at": (now - timedelta(seconds=601)).isoformat()}},
            "orphaned": {"lifecycle": "running", "process": {
                         "host_boot_id": BOOT, "orphaned": True}},
            "process_missing": {"lifecycle": "running", "process": {
                                "host_boot_id": BOOT, "child_pid": 99999999,
                                "child_start_identity": "child", "supervisor_nonce_hash": "nonce"}},
            "unreachable": {"lifecycle": "running", "target_reachable": False},
            "unknown": {"lifecycle": "running"},
            "terminal": {"lifecycle": "succeeded"},
        }

        def capture(pid):
            if pid == os.getpid():
                return ProcessIdentity(BOOT, pid, "child", "observed")
            return None

        def kill(pid, _signal):
            if pid == os.getpid():
                return None
            raise ProcessLookupError(pid)

        with patch("sandbox.jobs.health.capture_process_identity", side_effect=capture), \
                patch("sandbox.jobs.health.process_absence_proven", return_value=True), \
                patch("sandbox.jobs.health.os.kill", side_effect=kill):
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

        health, evidence = classify({"lifecycle": "running", "process": {
            "host_boot_id": BOOT, "orphaned": True}})
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
        with patch("sandbox.jobs.health.capture_process_identity", return_value=None), \
                patch("sandbox.jobs.health.process_absence_proven", return_value=True), \
                patch("sandbox.jobs.health.os.kill", side_effect=ProcessLookupError):
            health, _ = classify({"lifecycle": "running", "stall_seconds": 300,
                "process": {"host_boot_id": BOOT, "child_pid": 99999999,
                            "child_start_identity": "child", "supervisor_nonce_hash": "nonce"},
                "heartbeat": {"last_output_at": old, "last_metric_at": recent,
                               "health_evidence": {"metric_movement": True}}})
        self.assertEqual(health.value, "process_missing")
