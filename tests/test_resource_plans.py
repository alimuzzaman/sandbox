from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from sandbox.resources.models import CleanupCandidate, CleanupPlan
from sandbox.resources.plans import PlanStore, ResourcePlanError
from tests.resource_fixtures import NOW, observation, target


class TestPlanStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = NOW
        self.store = PlanStore(self.root, clock=lambda: self.now)
        self.plan = CleanupPlan.create(
            target(), "cache",
            (CleanupCandidate.from_observation(observation()),),
            (), now=self.now,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_create_is_atomic_and_private(self):
        saved = self.store.save(self.plan)
        path = self.root / f"{saved.plan_id}.json"
        self.assertTrue(path.is_file())
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(self.store.load(saved.plan_id), saved)

    def test_expired_plan_is_refused_and_marked(self):
        self.store.save(self.plan)
        self.now += timedelta(minutes=16)
        with self.assertRaisesRegex(ResourcePlanError, "expired"):
            self.store.begin(self.plan.plan_id, target())
        self.assertEqual(self.store.load(self.plan.plan_id).state, "expired")

    def test_target_mismatch_and_replay_are_refused(self):
        self.store.save(self.plan)
        with self.assertRaisesRegex(ResourcePlanError, "target"):
            self.store.begin(
                self.plan.plan_id,
                target(name="remote-a", identity="remote-fixture"),
            )
        started = self.store.begin(self.plan.plan_id, target())
        self.assertEqual(started.state, "in_progress")
        finished = self.store.finish(started.plan_id, "completed")
        self.assertEqual(finished.state, "completed")
        with self.assertRaisesRegex(ResourcePlanError, "already"):
            self.store.begin(self.plan.plan_id, target())

    def test_unknown_and_invalid_ids_fail_closed(self):
        with self.assertRaises(ResourcePlanError):
            self.store.load("../../bad")
        with self.assertRaises(ResourcePlanError):
            self.store.load("missing")


if __name__ == "__main__":
    unittest.main()
