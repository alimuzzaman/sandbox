import unittest
import tempfile
from pathlib import Path

from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.errors import RecoveryError
from sandbox.recovery.planner import PathResolver, build_plan


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
        self.assertTrue(any("symbolic host roots" in warning for warning in first.warnings))
        self.assertTrue(any("multiple sources" in warning for warning in first.warnings))

    def test_resolver_rejects_escape_and_absent_sources(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root); allowed = root_path / "allowed"; allowed.mkdir()
            (allowed / "ok").mkdir()
            resolver = PathResolver({"root": allowed})
            self.assertEqual(resolver.resolve("root", "ok"), (allowed / "ok").resolve())
            with self.assertRaises(RecoveryError):
                resolver.resolve("root", "../outside")
            with self.assertRaises(RecoveryError):
                resolver.resolve("root", str(allowed / "ok"))
            with self.assertRaises(RecoveryError):
                resolver.resolve("root", "bad\npath")
            with self.assertRaises(RecoveryError):
                resolver.resolve("root", "missing")

    def test_profile_selection_rejects_non_string_values(self):
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        with self.assertRaisesRegex(RecoveryError, "selection"):
            build_plan(catalog, (1,))
        with self.assertRaisesRegex(RecoveryError, "selection"):
            build_plan(catalog, ["control-plane"])


if __name__ == "__main__": unittest.main()
