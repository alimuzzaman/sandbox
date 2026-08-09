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
