import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


class TestRuntimeTestModes(unittest.TestCase):
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
