import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.core import _provision as provision


class PluginActivationOrderTests(unittest.TestCase):
    def test_declared_dependencies_activate_before_dependents(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugins = Path(tmp)
            for slug, requires in (
                ("elementor-pro", "elementor"),
                ("templately", ""),
                ("elementor", ""),
                ("essential-addons-for-elementor-lite", "elementor"),
            ):
                directory = plugins / slug
                directory.mkdir()
                (directory / f"{slug}.php").write_text(
                    "<?php\nPlugin Name: Fixture\n"
                    + (f"Requires Plugins: {requires}\n" if requires else "")
                )

            self.assertEqual(
                provision._plugin_activation_order(
                    plugins,
                    ["elementor-pro", "templately", "elementor",
                     "essential-addons-for-elementor-lite"],
                ),
                ["elementor", "elementor-pro", "templately",
                 "essential-addons-for-elementor-lite"],
            )

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.wpcli")
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_installs_all_sources_before_quiet_dependency_ordered_activation(
            self, wp_dir, plugins_dir, wpcli, _gate, _write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            def install_fixture(args, **_kwargs):
                if args[:2] != ["plugin", "install"]:
                    return
                for entry in args[2:]:
                    slug = "elementor" if "elementor." in entry else "elementor-pro"
                    directory = pdir / slug
                    directory.mkdir(exist_ok=True)
                    requires = "Requires Plugins: elementor\n" if slug == "elementor-pro" else ""
                    (directory / f"{slug}.php").write_text(
                        f"<?php\nPlugin Name: Fixture\n{requires}"
                    )

            wpcli.side_effect = install_fixture
            provision._wire_project_plugins("fixture", str(root), {
                "plugins_resolved": {
                    "elementor-pro": {
                        "source": {"kind": "zip", "value": "https://example.test/elementor-pro.zip"},
                        "active": True,
                    },
                    "elementor": {
                        "source": {"kind": "zip", "value": "https://example.test/elementor.4.1.2.zip"},
                        "active": True,
                    },
                },
            })

            calls = [call.args[0] for call in wpcli.call_args_list]
            self.assertNotIn("--activate", calls[0])
            self.assertEqual(
                calls[-1],
                ["plugin", "activate", "elementor", "elementor-pro", "--skip-plugins"],
            )

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.wpcli")
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_reconciles_active_plugin_to_inactive_without_touching_other_plugins(
            self, wp_dir, plugins_dir, wpcli, _gate, _write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            (pdir / "managed").mkdir()
            (pdir / "user-plugin").mkdir()
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            provision._wire_project_plugins("fixture", str(root), {
                "plugins_resolved": {
                    "managed": {"source": {"kind": "org", "value": None}, "active": False},
                },
            })

            self.assertIn(
                ["plugin", "deactivate", "managed", "--skip-plugins"],
                [call.args[0] for call in wpcli.call_args_list],
            )
            self.assertNotIn("user-plugin", str(wpcli.call_args_list))

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.wpcli")
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_missing_ondemand_path_stays_registered_for_fail_closed_install(
            self, wp_dir, plugins_dir, wpcli, _gate, write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            provision._wire_project_plugins("fixture", str(root), {
                "plugins_resolved": {
                    "optional": {
                        "source": {"kind": "path", "value": "missing-checkout"},
                        "on_demand": True,
                    },
                },
            })

            write_sources.assert_called_once_with(
                "fixture", {"optional": {"path": str(root / "missing-checkout")}})
            self.assertEqual(wpcli.call_count, 0)

    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_rejects_traversal_slug_before_any_filesystem_mutation(
            self, wp_dir, plugins_dir, _gate):
        with self.assertRaises(ValueError):
            provision._wire_project_plugins("fixture", "/tmp/project", {
                "plugins_resolved": {
                    "../outside": {"source": {"kind": "path", "value": "/tmp/source"}},
                },
            })
        plugins_dir.assert_not_called()
        wp_dir.assert_not_called()


class ThemeProvisioningTests(unittest.TestCase):
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.wpcli")
    @patch("sandbox.core._provision.wp_dir")
    def test_install_and_activation_skip_project_plugins(self, wp_dir, wpcli, _gate):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wp-content" / "themes").mkdir(parents=True)
            wp_dir.return_value = root

            provision._wire_project_themes("fixture", str(root), {
                "themes": ["https://example.test/fixture-theme.1.0.zip"],
            })

            calls = [call.args[0] for call in wpcli.call_args_list]
            self.assertEqual(calls, [
                ["theme", "install", "https://example.test/fixture-theme.1.0.zip",
                 "--skip-plugins"],
                ["theme", "activate", "fixture-theme", "--skip-plugins"],
            ])


if __name__ == "__main__":
    unittest.main()
