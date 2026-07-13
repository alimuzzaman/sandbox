import unittest

from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_units, run_with_lock
from tests.fakes.recovery import RecordingLock


class TestRecoveryScheduler(unittest.TestCase):
    def test_units_are_disabled_separate_and_use_lock_and_random_delay(self):
        policy = build_schedule_policy("daily", ("fixture",), "daily")
        units = render_systemd_units(policy)
        self.assertIn("ExecStart=/usr/bin/flock -n", units["service"])
        self.assertNotIn("ExecStart", units["timer"])
        self.assertIn("RandomizedDelaySec", units["timer"])
        self.assertEqual(units["enabled"], "false")

    def test_lock_and_resource_gates_skip_without_running_action(self):
        lock = RecordingLock(); lock.acquire(); called = []
        self.assertEqual(run_with_lock(lock, lambda: called.append(1))["reason"], "lock_held")
        lock.release()
        self.assertEqual(run_with_lock(lock, lambda: called.append(1), resource_ok=lambda: False)["reason"], "resource_gate")
        self.assertEqual(called, [])
