"""Coverage for the WordPress core reconcile that `sb apply` performs.

Core lives in the instance's bind mount, so nothing about a container recreate
re-versions WordPress. Without this reconcile an instance keeps whatever core it
was installed with forever — the failure mode where an edited (or deleted)
`wpVersion` pin only affected NEW instances.
"""
from pathlib import Path
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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

    def test_no_pin_preserves_a_stale_instance_during_apply(self):
        wp = FakeWpCli(version="7.0", latest="7.0.4")
        state = self.reconcile(wp, pconf={})

        self.assertEqual((state["from"], state["to"]), ("7.0", "7.0"))
        self.assertEqual(state["reason"], "unpinned-preserved")
        self.assertEqual(wp.ran(["core", "update"]), [])

    def test_no_pin_on_a_current_site_still_skips_the_schema_upgrade(self):
        wp = FakeWpCli(version="7.0.4", latest="7.0.4")
        state = self.reconcile(wp, pconf={})

        self.assertFalse(state["changed"])
        self.assertEqual(wp.ran(["core", "update-db"]), [])

    def test_multisite_upgrades_the_whole_network_schema(self):
        wp = FakeWpCli(version="6.8.1", latest="6.8.3")
        self.reconcile(
            wp,
            inst_cfg={"multisite": "subdirectory"},
            pconf={"wpVersion": "6.8.3"},
        )

        self.assertEqual(wp.ran(["core", "update-db"]),
                         [["core", "update-db", "--network"]])

    def test_a_failed_update_is_reported_not_raised(self):
        wp = FakeWpCli(version="6.8.1", update_rc=1)
        state = self.reconcile(wp, pconf={"wpVersion": "6.8.3"})

        self.assertFalse(state["changed"])
        self.assertEqual(state["from"], "6.8.1")
        self.assertIn("core update failed", state["error"])
        self.assertEqual(wp.ran(["core", "update-db"]), [])

    def test_apply_must_not_publish_ready_when_core_reconcile_reports_error(self):
        import sandbox.core._instances as module

        root = Path(tempfile.mkdtemp(prefix="sb-apply-core-error-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        existing = {
            "instance": "fixture", "label": "default", "status": "ready",
            "wordpress_port": 8188, "db_port": 3318, "mailpit_port": 8125,
            "server": "nginx",
        }
        pconf = {"root": str(root), "server": "nginx"}
        block = {"server": "nginx"}
        resolved = {"fixture": {
            "server": "nginx", "wordpress_port": 8188,
            "db_port": 3318, "mailpit_port": 8125,
        }}
        registry_put = Mock(side_effect=AssertionError(
            "failed apply must not publish a ready registry record"))

        class FakeCore:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_project, label=None):
                return pconf

            @staticmethod
            def registry_get(_root, label="default"):
                return existing

            @staticmethod
            def registry_list_for_root(_root):
                return [existing]

            @staticmethod
            @contextmanager
            def project_lock(_root):
                yield

        FakeCore.registry_put = registry_put

        rollback = Mock(return_value={"ok": True})
        with ExitStack() as stack:
            stack.enter_context(patch.object(module, "_core", return_value=FakeCore()))
            stack.enter_context(patch.object(
                module, "_local_yaml",
                return_value={"instances": {"fixture": {}}}))
            stack.enter_context(patch.object(module, "_write_local_yaml"))
            stack.enter_context(patch.object(module, "_assert_apply_runtime_dependencies"))
            stack.enter_context(patch.object(
                module, "_capture_apply_rollback_state", return_value={}))
            stack.enter_context(patch.object(module, "_build_instance_block", return_value=block))
            stack.enter_context(patch.object(
                module, "prepare_php_extension_runtime", return_value=None))
            stack.enter_context(patch.object(module, "load_config", return_value={}))
            stack.enter_context(patch.object(module, "write_compose_files"))
            stack.enter_context(patch.object(
                module, "resolve_instances", return_value=resolved))
            stack.enter_context(patch.object(
                module, "compose", return_value=SimpleNamespace(
                    returncode=0, stdout="", stderr="")))
            stack.enter_context(patch.object(module, "_wait_reachable", return_value=True))
            stack.enter_context(patch.object(module, "_wire_project_plugins"))
            stack.enter_context(patch.object(module, "_wire_project_themes"))
            stack.enter_context(patch.object(
                module, "_reconcile_wp_core", return_value={
                    "changed": False, "error": "PHP Fatal error: plugin bootstrap"
                }))
            stack.enter_context(patch.object(
                module, "_restore_apply_rollback_state", rollback))
            stack.enter_context(patch.object(module, "wp_dir", return_value=root))
            for helper in (
                "_write_mail_muplugin", "_write_loopback_muplugin",
                "_write_dl_cache_muplugin", "_write_ondemand_muplugin",
                "_write_host_runtime_muplugins", "_write_licensing_muplugin",
                "_remove_obsolete_builder_authoring_assets",
            ):
                stack.enter_context(patch.object(module, helper))
            with self.assertRaisesRegex(
                    ValueError,
                    "WordPress core reconcile failed: PHP Fatal error.*rollback=succeeded"):
                module.apply_config({}, str(root))

        rollback.assert_called_once()
        registry_put.assert_not_called()

    def test_an_uninstalled_instance_is_skipped(self):
        wp = FakeWpCli(version=None)
        state = self.reconcile(wp, pconf={"wpVersion": "6.8.3"})

        self.assertEqual(state, {"changed": False, "reason": "not-installed"})
        self.assertEqual(wp.ran(["core", "update"]), [])


if __name__ == "__main__":
    unittest.main()
