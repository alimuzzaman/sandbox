import unittest
from pathlib import Path

from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.planner import build_plan


class TestRecoveryPlanner(unittest.TestCase):
    def test_plan_is_deterministic_and_excludes_disposable_state(self):
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        first = build_plan(catalog)
        second = build_plan(catalog)
        self.assertEqual(first, second)
        excluded = {row["class"] for row in first.excluded}
        self.assertIn("containers-and-images", excluded)
        self.assertIn("development-wordpress-state", excluded)
        amar = next(item for item in first.artifacts if item.profile_id == "amarsonar-bangla-prod")
        self.assertEqual(amar.capture_mode, "full")
        self.assertIn("full-wordpress-directory", amar.sources)


if __name__ == "__main__": unittest.main()
