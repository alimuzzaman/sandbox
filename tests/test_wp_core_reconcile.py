"""Coverage for the WordPress core reconcile that `sb apply` performs.

Core lives in the instance's bind mount, so nothing about a container recreate
re-versions WordPress. Without this reconcile an instance keeps whatever core it
was installed with forever — the failure mode where an edited (or deleted)
`wpVersion` pin only affected NEW instances.
"""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core._instances as instances  # noqa: E402


class FakeWpCli:
    """Stand-in for `wpcli` that models a real core version on disk."""

    def __init__(self, version="6.8.1", latest="6.8.3", update_rc=0):
        self.version = version
        self.latest = latest
        self.update_rc = update_rc
        self.calls = []

    def __call__(self, args, instance, check=False, capture=False, **kwargs):
        self.calls.append(list(args))
        if args[:2] == ["core", "version"]:
            if self.version is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=f"{self.version}\n",
                                   stderr="")
        if args[:2] == ["core", "update"]:
            if self.update_rc == 0:
                pinned = [a for a in args if a.startswith("--version=")]
                self.version = (pinned[0].split("=", 1)[1] if pinned
                                else self.latest)
            return SimpleNamespace(returncode=self.update_rc, stdout="",
                                   stderr="core update failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def ran(self, prefix):
        return [c for c in self.calls if c[:len(prefix)] == prefix]


class TestReconcileWpCore(unittest.TestCase):
    def reconcile(self, wp, inst_cfg=None, pconf=None):
        with patch.object(instances, "wpcli", wp), \
                patch.object(instances, "info"), \
                patch.object(instances, "ok"):
            return instances._reconcile_wp_core(
                "fixture", inst_cfg or {}, pconf or {})

    def test_pin_matching_live_core_touches_nothing(self):
        wp = FakeWpCli(version="6.8.2")
        state = self.reconcile(wp, pconf={"wpVersion": "6.8.2"})

        self.assertFalse(state["changed"])
        self.assertEqual(wp.ran(["core", "update"]), [])

    def test_pin_forces_the_exact_build_in_either_direction(self):
        wp = FakeWpCli(version="7.0.4")
        state = self.reconcile(wp, pconf={"wpVersion": "7.0"})

        self.assertEqual((state["from"], state["to"]), ("7.0.4", "7.0"))
        self.assertTrue(state["changed"])
        self.assertEqual(wp.ran(["core", "update"])[0],
                         ["core", "update", "--version=7.0", "--force"])
        self.assertEqual(wp.ran(["core", "update-db"]), [["core", "update-db"]])

    def test_no_pin_moves_a_stale_instance_to_the_current_release(self):
        wp = FakeWpCli(version="7.0", latest="7.0.4")
        state = self.reconcile(wp, pconf={})

        self.assertEqual((state["from"], state["to"]), ("7.0", "7.0.4"))
        # No --version: "unpinned" means the current release, whatever it is.
        self.assertEqual(wp.ran(["core", "update"])[0], ["core", "update"])

    def test_no_pin_on_a_current_site_skips_the_schema_upgrade(self):
        wp = FakeWpCli(version="7.0.4", latest="7.0.4")
        state = self.reconcile(wp, pconf={})

        self.assertFalse(state["changed"])
        self.assertEqual(wp.ran(["core", "update-db"]), [])

    def test_multisite_upgrades_the_whole_network_schema(self):
        wp = FakeWpCli(version="6.8.1", latest="6.8.3")
        self.reconcile(wp, inst_cfg={"multisite": "subdirectory"}, pconf={})

        self.assertEqual(wp.ran(["core", "update-db"]),
                         [["core", "update-db", "--network"]])

    def test_a_failed_update_is_reported_not_raised(self):
        wp = FakeWpCli(version="6.8.1", update_rc=1)
        state = self.reconcile(wp, pconf={"wpVersion": "6.8.3"})

        self.assertFalse(state["changed"])
        self.assertEqual(state["from"], "6.8.1")
        self.assertIn("core update failed", state["error"])
        self.assertEqual(wp.ran(["core", "update-db"]), [])

    def test_an_uninstalled_instance_is_skipped(self):
        wp = FakeWpCli(version=None)
        state = self.reconcile(wp, pconf={"wpVersion": "6.8.3"})

        self.assertEqual(state, {"changed": False, "reason": "not-installed"})
        self.assertEqual(wp.ran(["core", "update"]), [])


if __name__ == "__main__":
    unittest.main()
