import unittest

from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_units, run_with_lock
from tests.fakes.recovery import RecordingLock


class TestRecoveryScheduler(unittest.TestCase):
    def test_units_are_disabled_separate_and_use_lock_and_random_delay(self):
        policy = build_schedule_policy("daily", ("fixture",), "daily")
        units = render_systemd_units(policy)
        self.assertIn("ExecStart=/usr/bin/flock -n", units["service"])
        self.assertIn("sb recovery create --confirm --profile fixture", units["service"])
        self.assertNotIn("ExecStart", units["timer"])
        self.assertIn("RandomizedDelaySec", units["timer"])
        self.assertEqual(units["enabled"], "false")

    def test_policy_delay_and_timeout_are_preserved_in_units(self):
        policy = build_schedule_policy("bounded", ("fixture",), "hourly",
                                       randomized_delay="7m", timeout="45m")
        units = render_systemd_units(policy)
        self.assertIn("RandomizedDelaySec=7m", units["timer"])
        self.assertIn("TimeoutStartSec=45m", units["service"])

    def test_unit_values_and_policy_id_fail_closed(self):
        with self.assertRaises(ValueError):
            build_schedule_policy("bad/id", ("fixture",), "daily")
        with self.assertRaises(ValueError):
            build_schedule_policy("daily", ("fixture",), "daily\nExecStart=unsafe")
        with self.assertRaises(ValueError):
            build_schedule_policy("daily", ("fixture",), "daily", timeout="6h\n")
        with self.assertRaises(ValueError):
            build_schedule_policy("daily", ("bad/profile",), "daily")
        with self.assertRaises(ValueError):
            build_schedule_policy("daily", (1,), "daily")
        with self.assertRaises(ValueError):
            build_schedule_policy("daily", ("fixture",), "daily", remote=1)
        with self.assertRaises(ValueError):
            build_schedule_policy("daily", ("fixture",), "daily", timeout=6)

    def test_scheduled_command_carries_reviewed_profiles_and_remote(self):
        policy = build_schedule_policy("daily", ("control-plane", "lenzora-prod"),
                                       "daily", remote="scaleway-sandbox")
        service = render_systemd_units(policy)["service"]
        self.assertIn("--profile control-plane --profile lenzora-prod --remote scaleway-sandbox", service)

    def test_lock_and_resource_gates_skip_without_running_action(self):
        lock = RecordingLock(); lock.acquire(); called = []
        self.assertEqual(run_with_lock(lock, lambda: called.append(1))["reason"], "lock_held")
        lock.release()
        self.assertEqual(run_with_lock(lock, lambda: called.append(1), resource_ok=lambda: False)["reason"], "resource_gate")
        self.assertEqual(called, [])
