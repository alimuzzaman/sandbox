import json
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


class TestRuntimeTestModes(unittest.TestCase):
    def test_explicit_instance_pins_wordpress_test_to_local_target(self):
        import sandbox.commands.debug as debug

        captured = []
        args = SimpleNamespace(
            project_dir="/fixture", label=None, instance="fixture-qa",
            mode="unit", provision_only=False, local=False, remote=None,
            workspace=None, timeout=60, output_profile="smart", json=False,
            passthrough=[],
        )

        class RegistryFacade:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_path, label=None):
                return {"root": "/fixture", "tests": {"suite": "unit"}}

            @staticmethod
            def registry_list_for_root(_root):
                return [{"instance": "fixture-qa", "label": "qa"}]

            @staticmethod
            def registry_get(_root, label=None):
                raise AssertionError("explicit instance must not fall back to registry_get")

        target = SimpleNamespace(kind="local", project_root="/fixture",
                                 remote_name=None, workspace_label="default")
        with patch.object(debug, "_core", return_value=RegistryFacade()), \
                patch("sandbox.application.context.durable_job_dependencies", return_value={
                    "target_service": SimpleNamespace(
                        resolve=lambda request: captured.append(request) or target),
                }), \
                patch("sandbox.application.context.managed_native_instance_selected",
                      return_value=None), \
                patch.object(debug, "_ensure_test_runner_tools", return_value={
                    "phpunit": "phpunit", "composer": "composer",
                }), \
                patch.object(debug, "_run_tests_unit", return_value=0):
            debug.cmd_test({}, args)

        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].local)
        self.assertIsNone(captured[0].remote)

    def test_remote_wordpress_workspace_selection_builds_isolated_matrix_leaves(self):
        import sandbox.commands.debug as debug

        target = SimpleNamespace(project_root="/fixture", remote_name="vps",
                                 sources={"identity": "project:fixture"})
        submissions = debug._remote_test_matrix_submissions(
            target, "integration", ["--filter", "Smoke"], ["wp-a", "wp-b"], 120, "smart")
        self.assertEqual([item.workspace_label for item in submissions], ["wp-a", "wp-b"])
        self.assertTrue(all(item.workspace_mode == "isolated" for item in submissions))
        self.assertEqual({item.project_identity for item in submissions}, {"project:fixture"})
        self.assertEqual(
            {item.source.identity for item in submissions},
            {"sha256:" + hashlib.sha256("/fixture".encode()).hexdigest()},
        )
        self.assertTrue(all(item.argv == (
            "sb", "test", "--local", "--project-dir", ".", "integration", "--", "--filter", "Smoke")
            for item in submissions))

    def test_trailing_json_is_not_forwarded_to_phpunit(self):
        import sandbox.commands.debug as debug

        captured = []
        args = SimpleNamespace(
            project_dir="/fixture", label=None, mode="integration",
            provision_only=False, local=True, remote=None, workspace=None,
            timeout=60, output_profile="smart", json=False,
            passthrough=["--json"],
        )

        class RegistryFacade:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_path, label=None):
                return {"root": "/fixture", "tests": {"suite": "integration"}}

            @staticmethod
            def registry_get(_root, label=None):
                return {"instance": "fixture", "label": "default"}

            @staticmethod
            def registry_list_for_root(_root):
                return [{"label": "default"}]

        with patch.object(debug, "_core", return_value=RegistryFacade()), \
                patch("sandbox.application.context.durable_job_dependencies", return_value={
                    "target_service": SimpleNamespace(resolve=lambda _request: SimpleNamespace(kind="local")),
                }), \
                patch.object(debug, "_provision_test_harness", return_value={
                    "suite": "/suite", "tools": {"phpunit": "phpunit", "composer": "composer", "polyfills": "polyfills"},
                    "config": "/config",
                }), \
                patch.object(debug, "_run_tests", side_effect=lambda *_args: captured.append(_args[-1]) or 0):
            debug.cmd_test({}, args)

        self.assertEqual(captured, [[]])

    def test_shipped_pure_unit_fixture_resolves_to_unit(self):
        from sandbox.core._tests import detect_test_mode

        self.assertEqual(detect_test_mode(ROOT / "tests" / "fixtures" / "pure-unit"), "unit")

    def project(self, *, composer=None, phpunit=None, test_source=None):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        if composer is not None:
            (root / "composer.json").write_text(json.dumps(composer))
        if phpunit is not None:
            (root / "phpunit.xml.dist").write_text(phpunit)
        if test_source is not None:
            tests = root / "tests"
            tests.mkdir()
            (tests / "ExampleTest.php").write_text(test_source)
        return tmp, root

    def test_unambiguous_brain_monkey_project_resolves_to_unit(self):
        from sandbox.core._tests import detect_test_mode, resolve_test_mode

        tmp, root = self.project(
            composer={"require-dev": {"brain/monkey": "^2.6"}},
            test_source="<?php use Brain\\Monkey; class ExampleTest {}",
        )
        with tmp:
            self.assertEqual(detect_test_mode(root), "unit")
            self.assertEqual(resolve_test_mode(root), "unit")

    def test_wordpress_marker_wins_over_unit_marker(self):
        from sandbox.core._tests import detect_test_mode

        tmp, root = self.project(
            composer={"require-dev": {"brain/monkey": "^2.6"}},
            phpunit="<phpunit bootstrap=\"tests/bootstrap.php\" />",
            test_source="<?php require getenv('WP_TESTS_DIR') . '/includes/bootstrap.php'; use Brain\\Monkey;",
        )
        with tmp:
            self.assertEqual(detect_test_mode(root), "integration")

    def test_unknown_project_falls_back_to_integration(self):
        from sandbox.core._tests import detect_test_mode, resolve_test_mode

        tmp, root = self.project(test_source="<?php class ExampleTest {}")
        with tmp:
            self.assertEqual(detect_test_mode(root), "integration")
            self.assertEqual(resolve_test_mode(root, configured="unit"), "unit")
            self.assertEqual(resolve_test_mode(root, explicit="integration"), "integration")

    def test_symlinked_evidence_outside_root_falls_back_to_integration(self):
        from sandbox.core._tests import detect_test_mode

        tmp, root = self.project(test_source="<?php use Brain\\Monkey;")
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp) / "outside.php"
            outside.write_text("<?php use Brain\\Monkey;")
            (root / "tests" / "outside-link.php").symlink_to(outside)
            with tmp:
                self.assertEqual(detect_test_mode(root), "integration")

    def test_mode_validation_rejects_unknown_values(self):
        from sandbox.core._tests import normalize_test_mode

        for value in ("coverage", "", None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_test_mode(value, allow_none=False)

    def test_cli_rejects_invalid_wordpress_mode_before_remote_target_resolution(self):
        import sandbox.commands.debug as debug

        args = SimpleNamespace(project_dir="/fixture", label=None, mode="coverage",
                               provision_only=False, local=False, remote="vps", workspace=None,
                               timeout=60, output_profile="smart", json=False, passthrough=[])

        class RegistryFacade:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_path, label=None):
                return {"root": "/fixture", "tests": {"suite": "auto"}}

        with patch.object(debug, "_core", return_value=RegistryFacade()), \
                patch("sandbox.application.context.durable_job_dependencies") as dependencies:
            with self.assertRaises(SystemExit):
                debug.cmd_test({}, args)

        dependencies.assert_not_called()

    def test_unit_runner_does_not_mount_wordpress_environment(self):
        from sandbox.core._tests import _run_tests_unit

        tmp, root = self.project()
        tools = {"phpunit": root / "phpunit.phar", "composer": root / "composer.phar"}
        with tmp, patch("sandbox.core._tests._ensure_project_dependencies_docker") as deps, \
                patch("sandbox.core._tests.compose", return_value=SimpleNamespace(returncode=0)) as compose:
            result = _run_tests_unit("fixture", str(root), tools, ["--filter", "Example"])

        self.assertEqual(result, 0)
        deps.assert_called_once_with("fixture", str(root), tools["composer"])
        command = compose.call_args.args
        self.assertIn("--no-deps", command)
        self.assertNotIn("WP_TESTS_DIR", command)
        self.assertNotIn("/wordpress-phpunit", command)
        self.assertNotIn("/wp-phpunit-polyfills", command)
        self.assertEqual(command[-2:], ("--filter", "Example"))

    def test_managed_unit_runner_uses_guest_phpunit_without_host_mount(self):
        from sandbox.core._tests import (
            MANAGED_NATIVE_COMPOSER, MANAGED_NATIVE_PHPUNIT, _run_tests_unit,
        )

        tmp, root = self.project()
        tools = {"phpunit": MANAGED_NATIVE_PHPUNIT,
                 "composer": MANAGED_NATIVE_COMPOSER}
        managed = SimpleNamespace(returncode=0)
        with tmp, patch("sandbox.core._tests._ensure_project_dependencies_docker") as deps, \
                patch("sandbox.core._tests._managed_execution_gate",
                      return_value=managed) as gate, \
                patch("sandbox.core._tests.compose") as compose:
            result = _run_tests_unit("fixture", str(root), tools, [])

        self.assertEqual(result, 0)
        deps.assert_called_once_with("fixture", str(root), MANAGED_NATIVE_COMPOSER)
        self.assertEqual(gate.call_args.args[3], (
            "php", "/usr/local/libexec/sandbox-phpunit.phar",
        ))
        compose.assert_not_called()

    def test_managed_unit_command_never_downloads_host_test_tools(self):
        import sandbox.commands.debug as debug

        captured = []
        args = SimpleNamespace(
            project_dir="/fixture", label=None, mode="unit",
            provision_only=False, local=True, remote=None, workspace=None,
            timeout=60, output_profile="smart", json=False, passthrough=[],
        )

        class RegistryFacade:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_path, label=None):
                return {"root": "/fixture", "tests": {"suite": "unit"}}

            @staticmethod
            def registry_get(_root, label=None):
                return {"instance": "fixture", "label": "default"}

            @staticmethod
            def registry_list_for_root(_root):
                return [{"label": "default"}]

        with patch.object(debug, "_core", return_value=RegistryFacade()), \
                patch("sandbox.application.context.durable_job_dependencies", return_value={
                    "target_service": SimpleNamespace(
                        resolve=lambda _request: SimpleNamespace(kind="local")),
                }), \
                patch("sandbox.application.context.managed_native_instance_selected",
                      return_value=("/fixture", "default")), \
                patch.object(debug, "_ensure_test_runner_tools") as host_tools, \
                patch.object(debug, "_run_tests_unit",
                             side_effect=lambda *_args: captured.append(_args[2]) or 0):
            debug.cmd_test({}, args)

        host_tools.assert_not_called()
        self.assertEqual(str(captured[0]["phpunit"]),
                         "/usr/local/libexec/sandbox-phpunit.phar")
        self.assertEqual(str(captured[0]["composer"]), "/usr/bin/composer")

    def test_provision_only_rejects_unit_before_harness(self):
        import sandbox.commands.debug as debug

        tmp, root = self.project()
        project_root = str(root)

        class RegistryFacade:
            ConfigError = ValueError

            @staticmethod
            def load_project_config(_path, label=None):
                return {"root": project_root, "tests": {"suite": "unit"}}

            @staticmethod
            def registry_get(_root, label=None):
                return {"instance": "fixture", "label": label or "default"}

            @staticmethod
            def registry_list_for_root(_root):
                return [{"label": "default"}]

        args = SimpleNamespace(project_dir=project_root, label=None,
                               mode="unit", provision_only=True, passthrough=[])
        with tmp, patch.object(debug, "_core", return_value=RegistryFacade()), \
                patch.object(debug, "_provision_test_harness") as provision:
            with self.assertRaises(SystemExit):
                debug.cmd_test({}, args)
        provision.assert_not_called()


if __name__ == "__main__":
    unittest.main()
