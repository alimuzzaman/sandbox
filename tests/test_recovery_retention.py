import unittest
from datetime import datetime, timedelta, timezone
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.retention import apply_retention, build_retention_plan
from sandbox.recovery.scheduler import build_schedule_policy, render_systemd_timer


class TestRecoveryRetention(unittest.TestCase):
    NOW = datetime(2026, 1, 10, tzinfo=timezone.utc)

    def test_newest_set_is_protected_and_schedule_defaults_disabled(self):
        plan = build_retention_plan("sets/", (
            {"id": "a", "prefix": "sets/", "status": "complete", "verified": True,
             "passphrase_current": True, "created_at": "2026-01-01T00:00:00+00:00"},
            {"id": "b", "prefix": "sets/", "status": "complete", "verified": True,
             "passphrase_current": True, "created_at": "2026-01-02T00:00:00+00:00"},
        ), now=self.NOW)
        self.assertEqual(plan.protected_sets, ("b",))
        self.assertEqual(plan.candidates, ("a",))
        policy = build_schedule_policy("daily", ("fixture",), "daily")
        self.assertFalse(policy.enabled)
        self.assertIn("RandomizedDelaySec", render_systemd_timer(policy))

    def test_only_complete_verified_current_sets_in_prefix_are_candidates(self):
        plan = build_retention_plan("sets/", (
            {"id": "old", "prefix": "sets/", "status": "complete", "verified": True, "passphrase_current": True, "created_at": "2025-12-01T00:00:00+00:00"},
            {"id": "new", "prefix": "sets/", "status": "complete", "verified": True, "passphrase_current": True, "created_at": "2026-01-02T00:00:00+00:00"},
            {"id": "partial", "prefix": "sets/", "status": "incomplete", "verified": True, "passphrase_current": True},
            {"id": "old-key", "prefix": "sets/", "status": "complete", "verified": True, "passphrase_current": False},
            {"id": "elsewhere", "prefix": "other/", "status": "complete", "verified": True, "passphrase_current": True},
        ))
        self.assertEqual(plan.protected_sets, ("new",))
        self.assertEqual(plan.candidates, ("old",))
        with self.assertRaises(RecoveryError): apply_retention(plan, lambda item: None)
        with self.assertRaises(RecoveryError): apply_retention(plan, lambda item: None, confirm=True, fresh_candidates=("changed",))

    def test_keep_count_and_age_floor_protect_additional_sets(self):
        sets = tuple({"id": f"set-{day}", "prefix": "sets/", "status": "complete", "verified": True,
                      "passphrase_current": True, "created_at": f"2026-01-{day:02d}T00:00:00+00:00"}
                     for day in range(1, 9))
        plan = build_retention_plan("sets/", sets, keep_count=3,
                                    minimum_age=timedelta(days=5), now=self.NOW)
        self.assertEqual(plan.protected_sets, ("set-6", "set-7", "set-8"))
        self.assertEqual(plan.candidates, ("set-1", "set-2", "set-3", "set-4", "set-5"))

    def test_invalid_timestamps_are_protected_and_invalid_policy_fails_closed(self):
        plan = build_retention_plan("sets/", ({"id": "unknown", "prefix": "sets/", "status": "complete",
            "verified": True, "passphrase_current": True, "created_at": "not-a-time"},), now=self.NOW)
        self.assertEqual(plan.candidates, ())
        with self.assertRaises(RecoveryError): build_retention_plan("sets/", (), keep_count=0)
        with self.assertRaises(RecoveryError): build_retention_plan("sets/", (), minimum_age=timedelta(days=-1))
        with self.assertRaisesRegex(RecoveryError, "metadata"):
            build_retention_plan("sets/", ("legacy-id",))

    def test_retention_rejects_unsafe_candidate_ids_before_delete(self):
        plan = type("Plan", (), {"destination_prefix": "sets/", "candidates": ("../outside",)})()
        with self.assertRaisesRegex(RecoveryError, "unsafe"):
            apply_retention(plan, lambda _path: self.fail("delete must not run"), confirm=True)
