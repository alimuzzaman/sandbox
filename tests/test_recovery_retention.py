import unittest
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.retention import apply_retention, build_retention_plan
from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_timer


class TestRecoveryRetention(unittest.TestCase):
    def test_newest_set_is_protected_and_schedule_defaults_disabled(self):
        plan = build_retention_plan("sets/", ("a", "b"))
        self.assertEqual(plan.protected_sets, ("b",))
        self.assertEqual(plan.candidates, ("a",))
        policy = build_schedule_policy("daily", ("fixture",), "daily")
        self.assertFalse(policy.enabled)
        self.assertIn("RandomizedDelaySec", render_systemd_timer(policy))

    def test_only_complete_verified_current_sets_in_prefix_are_candidates(self):
        plan = build_retention_plan("sets/", (
            {"id": "old", "prefix": "sets/", "status": "complete", "verified": True, "passphrase_current": True, "created_at": "1"},
            {"id": "new", "prefix": "sets/", "status": "complete", "verified": True, "passphrase_current": True, "created_at": "2"},
            {"id": "partial", "prefix": "sets/", "status": "incomplete", "verified": True, "passphrase_current": True},
            {"id": "old-key", "prefix": "sets/", "status": "complete", "verified": True, "passphrase_current": False},
            {"id": "elsewhere", "prefix": "other/", "status": "complete", "verified": True, "passphrase_current": True},
        ))
        self.assertEqual(plan.protected_sets, ("new",))
        self.assertEqual(plan.candidates, ("old",))
        with self.assertRaises(RecoveryError): apply_retention(plan, lambda item: None)
        with self.assertRaises(RecoveryError): apply_retention(plan, lambda item: None, confirm=True, fresh_candidates=("changed",))
