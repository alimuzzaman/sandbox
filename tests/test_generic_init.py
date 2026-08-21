"""Initialization-only generic ``sb init --type`` contract tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
import tempfile


import sandbox.commands.instances_cmd as command


class _FakeCore:
    CONFIG_BASENAMES = ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml")
    ConfigError = ValueError
    DEFAULTS = {
        "slug": None, "plugins": {}, "themes": [], "mappings": {},
        "mappings_inactive": {}, "phpVersion": None, "wpVersion": None,
        "multisite": False, "server": "nginx", "tld": "tst", "config": {},
        "port": None, "tests": {"suite": "auto"}, "pluginCheck": {},
    }

    @staticmethod
    def load_project_config(path):
        root = Path(path).expanduser().resolve()
        descriptor_path = root / "sandbox.config.json"
        if not descriptor_path.exists():
            return {"kind": "wordpress", "root": str(root),
                    "source": "defaults", "slug": root.name}
        document = json.loads(descriptor_path.read_text())
        kind = str(document.get("kind", "wordpress")).lower()
        generic = kind in {
            "compose", "generic", "astro", "laravel", "php", "node",
            "javascript", "js", "laravel-sail",
        }
        if generic:
            compose = document.get("compose") or {}
            return {
                "kind": "compose", "framework": document.get("framework"),
                "root": str(root), "source": descriptor_path.name,
                "compose_file": str(root / compose.get("file", "compose.yaml")),
                "service": compose.get("service", "web"),
                "internal_port": compose.get("internal_port", 80),
                "health_path": compose.get("health_path", "/"),
            }
        return {"kind": "wordpress", "root": str(root),
                "source": descriptor_path.name, "slug": root.name}


def _args(root, requested_type):
    return SimpleNamespace(project_dir=str(root), type=requested_type,
                           force=False, no_test_harness=True)


class TestGenericInitNoBoot(TestCase):
    def _compose_project(self, root: Path):
        (root / "compose.yaml").write_text(
            "services:\n  web:\n    image: nginx\n    ports: [\"8080:80\"]\n"
        )

    def test_every_explicit_generic_type_only_writes_reviewable_config(self):
        for requested_type in (
            "compose", "generic", "astro", "laravel", "php", "node", "javascript",
        ):
            with self.subTest(requested_type=requested_type), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if requested_type == "astro":
                    (root / "package.json").write_text(json.dumps({
                        "scripts": {"dev": "astro dev"},
                    }))
                else:
                    self._compose_project(root)

                out = io.StringIO()
                with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                        mock.patch.object(command, "runtime_service") as runtime_factory, \
                        mock.patch.object(command, "ensure_instance") as ensure, \
                        mock.patch.object(command, "_provision_test_harness") as harness, \
                        mock.patch.object(command.subprocess, "run") as process, \
                        redirect_stdout(out):
                    command.cmd_init({}, _args(root, requested_type))

                self.assertTrue((root / "sandbox.config.json").exists())
                self.assertIn("next: ./sb ensure", out.getvalue())
                runtime_factory.assert_not_called()
                ensure.assert_not_called()
                harness.assert_not_called()
                process.assert_not_called()

    def test_existing_specific_descriptor_conflict_refuses_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compose_project(root)
            descriptor = {
                "kind": "compose", "framework": "astro",
                "compose": {"file": "compose.yaml", "service": "web",
                             "internal_port": 80, "health_path": "/"},
            }
            path = root / "sandbox.config.json"
            path.write_text(json.dumps(descriptor, indent=2) + "\n")
            before = path.read_text()
            err = io.StringIO()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "runtime_service") as runtime_factory, \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(err):
                with self.assertRaises(SystemExit) as raised:
                    command.cmd_init({}, _args(root, "laravel"))

            self.assertEqual(raised.exception.code, 1)
            self.assertIn("conflicts", err.getvalue())
            self.assertEqual(path.read_text(), before)
            runtime_factory.assert_not_called()
            ensure.assert_not_called()

    def test_existing_wordpress_descriptor_conflict_refuses_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "sandbox.config.json"
            path.write_text(json.dumps({"kind": "wordpress"}) + "\n")
            err = io.StringIO()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "runtime_service") as runtime_factory, \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(err):
                with self.assertRaises(SystemExit):
                    command.cmd_init({}, _args(root, "compose"))
            self.assertIn("WordPress", err.getvalue())
            runtime_factory.assert_not_called()
            ensure.assert_not_called()

    def test_no_type_wordpress_init_keeps_legacy_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = {"instance": "fixture", "url": "http://localhost:8188",
                     "root": str(root), "wordpress_port": 8188,
                     "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "ensure_instance", return_value=entry) as ensure, \
                    mock.patch.object(command, "runtime_service") as runtime_factory, \
                    redirect_stdout(io.StringIO()):
                command.cmd_init({}, SimpleNamespace(
                    project_dir=str(root), type=None, force=False,
                    no_test_harness=True,
                ))
            ensure.assert_called_once_with(mock.ANY, str(root.resolve()))
            runtime_factory.assert_not_called()

    def _run_top_level(self, root: Path, requested_type: str):
        """Run the real parser/dispatch boundary with the runtime mocked."""
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        output, errors = io.StringIO(), io.StringIO()
        argv = ["sb", "init", "--project-dir", str(root),
                "--type", requested_type, "--no-test-harness"]
        with mock.patch.object(cli, "COMMANDS", {"init": command.cmd_init}), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance", return_value=None), \
                mock.patch.object(migrate, "maybe_auto_migrate") as maybe_migrate, \
                mock.patch.object(migrate, "finalize_auto_migration", return_value=False) as finalize, \
                mock.patch.object(cli, "write_compose_files") as compose, \
                mock.patch.object(cli, "write_env_for_compose") as env, \
                mock.patch.object(cli, "_ensure_wp_cli_phar") as wp_cli_phar, \
                mock.patch.object(command, "_core", return_value=_FakeCore()), \
                mock.patch.object(command, "runtime_service") as runtime_factory, \
                mock.patch.object(cli.sys, "argv", argv), \
                redirect_stdout(output), redirect_stderr(errors):
            try:
                cli.main()
            except SystemExit as exc:
                return (exc.code, output.getvalue(), errors.getvalue(),
                        (maybe_migrate, finalize, compose, env, wp_cli_phar,
                         runtime_factory))
        return (0, output.getvalue(), errors.getvalue(),
                (maybe_migrate, finalize, compose, env, wp_cli_phar,
                 runtime_factory))

    def test_top_level_generic_init_skips_migration_and_compose_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compose_project(root)
            code, output, errors, mocks = self._run_top_level(root, "compose")
            self.assertEqual(code, 0, errors)
            self.assertIn("next: ./sb ensure", output)
            self.assertEqual(errors, "")
            for observed in mocks:
                observed.assert_not_called()

    def test_top_level_conflicting_generic_init_skips_migration_and_compose_writers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compose_project(root)
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose", "framework": "astro",
                "compose": {"file": "compose.yaml", "service": "web",
                             "internal_port": 80, "health_path": "/"},
            }) + "\n")
            code, _output, errors, mocks = self._run_top_level(root, "laravel")
            self.assertEqual(code, 1)
            self.assertIn("conflicts", errors)
            for observed in mocks:
                observed.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
