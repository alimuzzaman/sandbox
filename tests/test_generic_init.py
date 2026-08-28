"""Initialization-only generic ``sb init --type`` contract tests."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
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

    def test_astro_dangling_descriptor_symlink_refuses_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            (root / "package.json").write_text(json.dumps({
                "scripts": {"dev": "astro dev"},
            }))
            outside = parent / "missing-outside.json"
            (root / "sandbox.config.json").symlink_to(outside)
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "runtime_service") as runtime_factory, \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, "astro"))
            self.assertFalse(outside.exists())
            self.assertFalse((root / "sandbox.compose.yml").exists())
            ensure.assert_not_called()
            runtime_factory.assert_not_called()

    def test_astro_compose_output_symlink_refuses_before_descriptor_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            (root / "package.json").write_text(json.dumps({
                "scripts": {"dev": "astro dev"},
            }))
            outside = parent / "outside-compose.yml"
            outside.write_text("outside-bytes\n")
            before = outside.read_bytes()
            (root / "sandbox.compose.yml").symlink_to(outside)
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "runtime_service") as runtime_factory, \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, "astro"))
            self.assertEqual(outside.read_bytes(), before)
            self.assertFalse((root / "sandbox.config.json").exists())
            ensure.assert_not_called()
            runtime_factory.assert_not_called()

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

    def test_fresh_explicit_target_does_not_inherit_parent_markers(self):
        for marker in ("git", "descriptor"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
                parent = Path(tmp)
                if marker == "git":
                    (parent / ".git").mkdir()
                else:
                    (parent / "sandbox.config.json").write_text(json.dumps({
                        "slug": "parent", "plugins": {"parent": "."},
                    }))
                child = parent / "fresh-child"
                child.mkdir()
                parent_before = {
                    path.relative_to(parent): path.read_bytes()
                    for path in parent.iterdir() if path.is_file()
                }
                entry = {"instance": "fixture", "url": "http://localhost:8188",
                         "root": str(child), "wordpress_port": 8188,
                         "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}
                with mock.patch.object(command, "ensure_instance", return_value=entry) as ensure, \
                        mock.patch.object(command, "_provision_test_harness"), \
                        redirect_stdout(io.StringIO()):
                    command.cmd_init({}, _args(child, None))

                self.assertTrue((child / "sandbox.config.json").is_file())
                self.assertEqual(
                    {path.relative_to(parent): path.read_bytes()
                     for path in parent.iterdir() if path.is_file()},
                    parent_before,
                )
                ensure.assert_called_once_with(mock.ANY, str(child.resolve()))

    def test_fresh_cwd_is_exact_init_boundary(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            parent = Path(tmp)
            (parent / ".git").mkdir()
            child = parent / "fresh-cwd"
            child.mkdir()
            entry = {"instance": "fixture", "url": "http://localhost:8188",
                     "root": str(child), "wordpress_port": 8188,
                     "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}
            args = SimpleNamespace(project_dir=None, type=None, force=False,
                                   no_test_harness=True)
            with mock.patch.object(command.os, "getcwd", return_value=str(child)), \
                    mock.patch.object(command, "ensure_instance", return_value=entry) as ensure, \
                    redirect_stdout(io.StringIO()):
                command.cmd_init({}, args)
            self.assertTrue((child / "sandbox.config.json").is_file())
            self.assertFalse((parent / "sandbox.config.json").exists())
            ensure.assert_called_once_with(mock.ANY, str(child.resolve()))

    def test_exact_home_is_refused_before_writes_or_ensure(self):
        with tempfile.TemporaryDirectory() as fake_home:
            root = Path(fake_home)
            (root / ".git").mkdir()
            before = sorted(path.name for path in root.iterdir())
            with mock.patch.object(command.Path, "home", return_value=root), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, None))
            self.assertEqual(sorted(path.name for path in root.iterdir()), before)
            ensure.assert_not_called()

    def test_home_marker_does_not_capture_fresh_child_target(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            (home / ".git").mkdir()
            child = home / "fresh-child"
            child.mkdir()
            entry = {"instance": "fixture", "url": "http://localhost:8188",
                     "root": str(child), "wordpress_port": 8188,
                     "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}
            with mock.patch.object(command.Path, "home", return_value=home), \
                    mock.patch.object(command, "ensure_instance", return_value=entry) as ensure, \
                    redirect_stdout(io.StringIO()):
                command.cmd_init({}, _args(child, None))
            self.assertTrue((child / "sandbox.config.json").is_file())
            self.assertFalse((home / "sandbox.config.json").exists())
            ensure.assert_called_once_with(mock.ANY, str(child.resolve()))

    def test_root_descriptor_symlink_is_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            outside = parent / "outside.json"
            outside.write_text('{"slug":"outside"}\n')
            (root / "sandbox.config.json").symlink_to(outside)
            before = outside.read_bytes()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, None))
            self.assertEqual(outside.read_bytes(), before)
            ensure.assert_not_called()

    def test_nested_descriptor_symlink_is_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            parent = Path(tmp)
            root = parent / "project"
            nested = root / ".config" / "sandbox"
            nested.mkdir(parents=True)
            outside = parent / "outside.json"
            outside.write_text('{"slug":"outside"}\n')
            (nested / "sandbox.config.json").symlink_to(outside)
            before = outside.read_bytes()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, None))
            self.assertEqual(outside.read_bytes(), before)
            ensure.assert_not_called()

    def test_wp_env_symlink_is_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            outside = parent / "outside-wp-env.json"
            outside.write_text('{"core":"WordPress/WordPress"}\n')
            (root / ".wp-env.json").symlink_to(outside)
            before = outside.read_bytes()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, None))
            self.assertEqual(outside.read_bytes(), before)
            ensure.assert_not_called()

    def test_non_regular_native_descriptor_is_refused_before_ensure(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "sandbox.config.json").mkdir()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, None))
            ensure.assert_not_called()

    def test_external_nested_config_home_is_refused_without_writes(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            parent = Path(tmp)
            root = parent / "project"
            (root / ".config").mkdir(parents=True)
            outside = parent / "outside-config"
            outside.mkdir()
            descriptor = outside / "sandbox.config.json"
            descriptor.write_text('{"slug":"outside"}\n')
            (root / ".config" / "sandbox").symlink_to(outside)
            before = descriptor.read_bytes()
            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, _args(root, None))
            self.assertEqual(descriptor.read_bytes(), before)
            ensure.assert_not_called()

    def test_destination_swap_is_refused_and_never_follows_symlink(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            destination = root / "sandbox.config.json"
            destination.write_text(json.dumps({
                "slug": "project", "plugins": {"project": "."},
            }))
            outside = parent / "outside.json"
            outside.write_text("outside-bytes\n")
            before = outside.read_bytes()
            args = SimpleNamespace(project_dir=str(root), type=None, force=True,
                                   no_test_harness=True)
            real_fsync = os.fsync
            swapped = False

            def swap_destination(fd):
                nonlocal swapped
                real_fsync(fd)
                if not swapped:
                    swapped = True
                    destination.unlink()
                    destination.symlink_to(outside)

            with mock.patch.object(command, "_core", return_value=_FakeCore()), \
                    mock.patch.object(command.os, "fsync", side_effect=swap_destination), \
                    mock.patch.object(command, "ensure_instance") as ensure, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                command.cmd_init({}, args)
            self.assertTrue(swapped)
            self.assertEqual(outside.read_bytes(), before)
            ensure.assert_not_called()

    def test_fresh_scaffold_excludes_global_catalog_but_resolution_keeps_it_on_demand(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp) / "fixture-plugin"
            root.mkdir()
            global_path = Path(tmp) / "global.json"
            global_path.write_text(json.dumps({
                "plugins": {"paid-pro": str(Path(tmp) / "paid-pro")},
            }))
            observed = {}

            def ensure(_cfg, target):
                observed.update(sandbox_core.load_project_config(target))
                return {"instance": "fixture", "url": "http://localhost:8188",
                        "root": target, "wordpress_port": 8188,
                        "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}

            with mock.patch.dict(os.environ, {"SANDBOX_USER_CONFIG": str(global_path)}), \
                    mock.patch.object(command, "ensure_instance", side_effect=ensure), \
                    redirect_stdout(io.StringIO()):
                command.cmd_init({}, _args(root, None))

            document = json.loads((root / "sandbox.config.json").read_text())
            self.assertEqual(set(document["plugins"]), {
                "fixture-plugin", "query-monitor", "plugin-check", "mcp-adapter",
            })
            self.assertNotIn("paid-pro", document["plugins"])
            self.assertFalse(document["plugins"]["query-monitor"])
            self.assertTrue(document["plugins"]["plugin-check"])
            self.assertIn("github.com/WordPress/mcp-adapter", document["plugins"]["mcp-adapter"])
            self.assertFalse(observed["plugins_resolved"]["paid-pro"]["active"])
            self.assertTrue(observed["plugins_resolved"]["paid-pro"]["on_demand"])

    def test_force_preserves_existing_project_plugin_declarations(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp) / "fixture-plugin"
            root.mkdir()
            declarations = {
                "fixture-plugin": ".",
                "project-helper": {"path": "../helper", "onDemand": True},
            }
            (root / "sandbox.config.json").write_text(json.dumps({
                "slug": "fixture-plugin", "plugins": declarations,
            }))
            global_path = Path(tmp) / "global.json"
            global_path.write_text(json.dumps({
                "plugins": {"paid-pro": str(Path(tmp) / "paid-pro")},
            }))
            entry = {"instance": "fixture", "url": "http://localhost:8188",
                     "root": str(root), "wordpress_port": 8188,
                     "db_port": 3318, "mailpit_port": 8125, "server": "nginx"}
            args = SimpleNamespace(project_dir=str(root), type=None, force=True,
                                   no_test_harness=True)
            with mock.patch.dict(os.environ, {"SANDBOX_USER_CONFIG": str(global_path)}), \
                    mock.patch.object(command, "ensure_instance", return_value=entry), \
                    redirect_stdout(io.StringIO()):
                command.cmd_init({}, args)

            document = json.loads((root / "sandbox.config.json").read_text())
            self.assertEqual(document["plugins"], declarations)
            self.assertNotIn("paid-pro", document["plugins"])

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
