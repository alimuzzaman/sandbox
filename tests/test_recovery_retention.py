import unittest
from sandbox.recovery.retention import build_retention_plan
from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_timer


class TestRecoveryRetention(unittest.TestCase):
    def test_newest_set_is_protected_and_schedule_defaults_disabled(self):
        plan = build_retention_plan("sets/", ("a", "b"))
        self.assertEqual(plan.protected_sets, ("b",))
        self.assertEqual(plan.candidates, ("a",))
        policy = build_schedule_policy("daily", ("fixture",), "daily")
        self.assertFalse(policy.enabled)
        self.assertIn("RandomizedDelaySec", render_systemd_timer(policy))
