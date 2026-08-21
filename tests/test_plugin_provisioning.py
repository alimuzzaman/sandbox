import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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
    def test_missing_inactive_query_monitor_is_installed_then_deactivated(
            self, wp_dir, plugins_dir, wpcli, _gate, _write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            provision._wire_project_plugins("fixture", str(root), {
                "plugins_resolved": {
                    "query-monitor": {
                        "source": {"kind": "org", "value": None},
                        "active": False,
                        "on_demand": False,
                    },
                },
            })

            calls = [call.args[0] for call in wpcli.call_args_list]
            self.assertEqual(calls, [
                ["plugin", "install", "query-monitor"],
                ["plugin", "deactivate", "query-monitor", "--skip-plugins"],
            ])
            self.assertFalse(any(call[:2] == ["plugin", "activate"] for call in calls))

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
                "fixture", {"optional": {"path": str((root / "missing-checkout").resolve())}})
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

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_missing_elementor_dependency_activation_fails_closed(
            self, wp_dir, plugins_dir, _gate, write_sources):
        class SentinelPluginError(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            (pdir / "elementor-pro").mkdir()
            (pdir / "elementor-pro" / "elementor-pro.php").write_text(
                "<?php\nPlugin Name: Elementor Pro\n"
                "Requires Plugins: elementor\n"
            )
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            def wpcli(args, **_kwargs):
                if args[:2] == ["plugin", "activate"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr="missing dependency")
                if args[:2] == ["plugin", "list"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps([{"name": "elementor-pro", "status": "inactive"}]),
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("sandbox.core._provision.wpcli", side_effect=wpcli) as mocked:
                with self.assertRaisesRegex(SentinelPluginError, "elementor-pro"):
                    provision._wire_project_plugins("fixture", str(root), {
                        "plugins_resolved": {
                            "elementor-pro": {
                                "source": {"kind": "org", "value": None},
                                "active": True,
                            },
                        },
                    }, error_factory=SentinelPluginError)

            write_sources.assert_not_called()
            activation = next(call for call in mocked.call_args_list
                              if call.args[0][:2] == ["plugin", "activate"])
            self.assertTrue(activation.kwargs["capture"])

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_nonzero_activation_is_tolerated_when_state_is_already_correct(
            self, wp_dir, plugins_dir, _gate, write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            (pdir / "managed").mkdir()
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            def wpcli(args, **_kwargs):
                if args[:2] == ["plugin", "activate"]:
                    return SimpleNamespace(returncode=1, stdout="already active", stderr="")
                if args[:2] == ["plugin", "list"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps([{"name": "managed", "status": "active"}]),
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("sandbox.core._provision.wpcli", side_effect=wpcli) as mocked:
                provision._wire_project_plugins("fixture", str(root), {
                    "plugins_resolved": {
                        "managed": {
                            "source": {"kind": "org", "value": None},
                            "active": True,
                        },
                    },
                })

            write_sources.assert_called_once_with("fixture", {})
            observed = next(call for call in mocked.call_args_list
                            if call.args[0][:2] == ["plugin", "list"])
            self.assertEqual(observed.args[0], [
                "plugin", "list", "--fields=name,status", "--format=json",
                "--skip-plugins",
            ])
            self.assertTrue(observed.kwargs["capture"])

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_install_failure_does_not_write_ready_state(
            self, wp_dir, plugins_dir, _gate, write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            def wpcli(args, **_kwargs):
                if args[:2] == ["plugin", "install"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr="download failed")
                if args[:2] == ["plugin", "list"]:
                    return SimpleNamespace(returncode=0, stdout="[]", stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="not installed")

            with patch("sandbox.core._provision.wpcli", side_effect=wpcli):
                with self.assertRaisesRegex(RuntimeError, "missing-plugin"):
                    provision._wire_project_plugins("fixture", str(root), {
                        "plugins_resolved": {
                            "missing-plugin": {
                                "source": {"kind": "org", "value": None},
                                "active": True,
                            },
                        },
                    })

            write_sources.assert_not_called()

    @patch("sandbox.core._provision._write_local_sources")
    @patch("sandbox.core._provision._managed_execution_gate", return_value=None)
    @patch("sandbox.core._provision.plugins_dir")
    @patch("sandbox.core._provision.wp_dir")
    def test_deactivate_failure_does_not_write_ready_state(
            self, wp_dir, plugins_dir, _gate, write_sources):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "wp-content" / "plugins"
            pdir.mkdir(parents=True)
            (pdir / "managed").mkdir()
            plugins_dir.return_value = pdir
            wp_dir.return_value = root

            def wpcli(args, **_kwargs):
                if args[:2] == ["plugin", "deactivate"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr="deactivate failed")
                if args[:2] == ["plugin", "list"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps([{"name": "managed", "status": "active"}]),
                        stderr="",
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch("sandbox.core._provision.wpcli", side_effect=wpcli):
                with self.assertRaisesRegex(RuntimeError, "managed"):
                    provision._wire_project_plugins("fixture", str(root), {
                        "plugins_resolved": {
                            "managed": {
                                "source": {"kind": "org", "value": None},
                                "active": False,
                            },
                        },
                    })

            write_sources.assert_not_called()


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
