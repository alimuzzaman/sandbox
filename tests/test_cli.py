"""End-to-end CLI tests for the per-project resolution gate (spec 001).

These run the real `sb` entry as a subprocess (no Docker — the gate + registry
read happen before any container work), so they exercise the actual bootstrap,
package import, registry dispatch, and the no-`main` resolution behavior.
"""
import os
import io
import json
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "sb"


def run_sb(*args, cwd="/tmp"):
    return subprocess.run(
        [str(SB), *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "SANDBOX_INSTANCE": ""}, timeout=90)


class TestResolutionGate(unittest.TestCase):
    def test_remote_instance_control_flags_reach_registered_handlers(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        invocations = (
            (["sb", "instances", "--remote", "remote-a", "--json"],
             "instances", None),
            (["sb", "instance", "delete", "preview-a", "--remote", "remote-a",
              "--yes"], "instance", "delete"),
        )
        for argv, command, action in invocations:
            observed = []
            with self.subTest(argv=argv), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(cli, "COMMANDS", {
                        command: lambda _cfg, args: observed.append(args),
                    }), \
                    mock.patch.object(cli, "load_config", return_value={}), \
                    mock.patch.object(cli, "resolve_instances", return_value={}), \
                    mock.patch.object(cli, "_cwd_instance", return_value=None), \
                    mock.patch.object(cli, "_core", return_value=SimpleNamespace(
                        registry_all=lambda: {},
                    )), \
                    mock.patch.object(migrate, "maybe_auto_migrate"), \
                    mock.patch.object(migrate, "finalize_auto_migration",
                                      return_value=False), \
                    mock.patch.object(cli, "write_compose_files"), \
                    mock.patch.object(cli, "write_env_for_compose"):
                cli.main()

            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0].remote, "remote-a")
            self.assertFalse(observed[0].local)
            if action is not None:
                self.assertEqual(observed[0].action, action)

    def test_test_routing_options_after_mode_are_not_forwarded_to_phpunit(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        argv = [
            "sb", "test", "unit", "--project-dir", "/fixture",
            "--label", "pr-123", "--timeout", "500", "--remote", "remote-a",
            "--workspace", "unit-a", "--json", "--", "--filter", "Smoke",
        ]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(cli, "COMMANDS", {
                    "test": lambda _cfg, args: observed.append(args),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance", return_value=None), \
                mock.patch.object(cli, "_core", return_value=SimpleNamespace(
                    registry_all=lambda: {},
                )), \
                mock.patch.object(migrate, "maybe_auto_migrate"), \
                mock.patch.object(migrate, "finalize_auto_migration",
                                  return_value=False), \
                mock.patch.object(cli, "write_compose_files"), \
                mock.patch.object(cli, "write_env_for_compose"):
            cli.main()

        self.assertEqual(len(observed), 1)
        args = observed[0]
        self.assertEqual(args.mode, "unit")
        self.assertEqual(args.project_dir, "/fixture")
        self.assertEqual(args.label, "pr-123")
        self.assertEqual(args.timeout, 500)
        self.assertEqual(args.remote, "remote-a")
        self.assertEqual(args.workspace, ["unit-a"])
        self.assertTrue(args.json)
        self.assertEqual(args.passthrough, ["--filter", "Smoke"])

    def test_test_passthrough_flag_without_mode_is_not_consumed_as_mode(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        argv = [
            "sb", "test", "--project-dir", "/fixture",
            "--", "--testsuite", "unit",
        ]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(cli, "COMMANDS", {
                    "test": lambda _cfg, args: observed.append(args),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance", return_value=None), \
                mock.patch.object(cli, "_core", return_value=SimpleNamespace(
                    registry_all=lambda: {},
                )), \
                mock.patch.object(migrate, "maybe_auto_migrate"), \
                mock.patch.object(migrate, "finalize_auto_migration",
                                  return_value=False), \
                mock.patch.object(cli, "write_compose_files"), \
                mock.patch.object(cli, "write_env_for_compose"):
            cli.main()

        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0].mode)
        self.assertEqual(observed[0].project_dir, "/fixture")
        self.assertEqual(observed[0].passthrough, ["--testsuite", "unit"])

    def test_registry_wide_setup_rejects_instance_and_label_selectors_before_side_effects(self):
        import sandbox.cli as cli
        import sandbox.commands.config_setup as config_setup
        import sandbox.commands.lifecycle as lifecycle
        import sandbox.commands.migrate as migrate

        invocations = [
            ["sb", "--instance", "demo", "setup"],
            ["sb", "setup", "--instance", "demo"],
            ["sb", "--label", "qa", "setup"],
            ["sb", "setup", "--label", "qa"],
        ]
        for argv in invocations:
            with self.subTest(argv=argv):
                errors = StringIO()
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                        mock.patch.object(cli, "load_config") as load_config, \
                        mock.patch.object(config_setup, "_docker_preflight") as docker_preflight, \
                        mock.patch.object(lifecycle, "cmd_up") as cmd_up, \
                        redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main()

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(
                    errors.getvalue(),
                    "error: setup is registry-wide; use `sb apply --instance NAME` "
                    "or `sb ensure --project-dir DIR` for project-scoped setup.\n",
                )
                auto_migrate.assert_not_called()
                load_config.assert_not_called()
                docker_preflight.assert_not_called()
                cmd_up.assert_not_called()

    def test_project_routed_ensure_rejects_instance_before_side_effects(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        invocations = [
            ["sb", "--instance", "demo", "ensure", "--project-dir", "/tmp/project"],
            ["sb", "ensure", "--instance", "demo", "--project-dir", "/tmp/project"],
            ["sb", "ensure", "--instance=demo", "--project-dir", "/tmp/project"],
        ]
        expected = (
            "error: ensure is project-scoped and cannot target --instance NAME; use "
            "`sb ensure --project-dir DIR` with `--label LABEL` (and `--create` "
            "for a new label), or `sb apply --instance NAME` for an existing "
            "named instance.\n"
        )
        for argv in invocations:
            with self.subTest(argv=argv):
                errors = StringIO()
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                        mock.patch.object(cli, "load_config") as load_config, \
                        mock.patch.object(cli, "write_compose_files") as compose, \
                        mock.patch.object(cli, "write_env_for_compose") as env, \
                        mock.patch.object(cli, "COMMANDS", {
                            "ensure": mock.Mock(name="ensure_handler"),
                        }) as commands, \
                        redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main()

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(errors.getvalue(), expected)
                auto_migrate.assert_not_called()
                load_config.assert_not_called()
                compose.assert_not_called()
                env.assert_not_called()
                commands["ensure"].assert_not_called()

    def test_project_routed_init_rejects_instance_before_side_effects(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        invocations = [
            ["sb", "--instance", "fixture", "init", "--project-dir", "/tmp/project"],
            ["sb", "init", "--instance", "fixture", "--project-dir", "/tmp/project"],
            ["sb", "init", "--instance=fixture", "--project-dir", "/tmp/project"],
        ]
        expected = (
            "error: init is project-scoped and cannot target --instance NAME; use "
            "`sb init --project-dir DIR` to select the project; use "
            "`sb ensure --project-dir DIR --label LABEL --create` for an "
            "additional instance.\n"
        )
        for argv in invocations:
            with self.subTest(argv=argv):
                errors = StringIO()
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                        mock.patch.object(cli, "load_config") as load_config, \
                        mock.patch.object(cli, "write_compose_files") as compose, \
                        mock.patch.object(cli, "write_env_for_compose") as env, \
                        mock.patch.object(cli, "COMMANDS", {
                            "init": mock.Mock(name="init_handler"),
                        }) as commands, \
                        redirect_stderr(errors):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main()

                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(errors.getvalue(), expected)
                auto_migrate.assert_not_called()
                load_config.assert_not_called()
                compose.assert_not_called()
                env.assert_not_called()
                commands["init"].assert_not_called()

    def test_project_routed_init_skips_shared_writes_before_handler(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        with mock.patch.object(sys, "argv", [
                "sb", "init", "--project-dir", "/srv/fixture",
        ]), \
                mock.patch.object(cli, "COMMANDS", {
                    "init": lambda cfg, args: observed.append((cfg, args.project_dir)),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance",
                                  side_effect=AssertionError("controller cwd consulted")), \
                mock.patch.object(cli, "write_compose_files") as compose, \
                mock.patch.object(cli, "write_env_for_compose") as env, \
                mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                mock.patch.object(migrate, "finalize_auto_migration") as finalize:
            cli.main()

        self.assertEqual(observed, [({}, "/srv/fixture")])
        compose.assert_not_called()
        env.assert_not_called()
        auto_migrate.assert_not_called()
        finalize.assert_not_called()

    def test_ensure_help_explains_instance_selector_boundary(self):
        result = run_sb("ensure", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("invalid for ensure", result.stdout)
        self.assertIn("sb apply --instance NAME", result.stdout)

    def test_project_routed_ensure_skips_compose_and_env_predispatch_writes(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        with mock.patch.object(sys, "argv", ["sb", "ensure", "--local", "--project-dir", "/tmp/project"]), \
                mock.patch.object(cli, "COMMANDS", {
                    "ensure": lambda cfg, args: observed.append((cfg, args.project_dir)),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance", return_value=None), \
                mock.patch.object(cli, "write_compose_files") as compose, \
                mock.patch.object(cli, "write_env_for_compose") as env, \
                mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                mock.patch.object(migrate, "finalize_auto_migration") as finalize:
            cli.main()

        self.assertEqual(observed, [({}, "/tmp/project")])
        compose.assert_not_called()
        env.assert_not_called()
        auto_migrate.assert_not_called()
        finalize.assert_not_called()

    def test_project_routed_ensure_preserves_label_and_create(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        with mock.patch.object(
                sys, "argv",
                ["sb", "--label", "qa", "ensure", "--local",
                 "--project-dir", "/tmp/project", "--create"]), \
                mock.patch.object(cli, "COMMANDS", {
                    "ensure": lambda cfg, args: observed.append((
                        cfg, args.project_dir, args.label, args.create)),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance", return_value=None), \
                mock.patch.object(cli, "write_compose_files") as compose, \
                mock.patch.object(cli, "write_env_for_compose") as env, \
                mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                mock.patch.object(migrate, "finalize_auto_migration") as finalize:
            cli.main()

        self.assertEqual(observed, [({}, "/tmp/project", "qa", True)])
        compose.assert_not_called()
        env.assert_not_called()
        auto_migrate.assert_not_called()
        finalize.assert_not_called()

    def test_outer_remote_project_observation_skips_every_local_gate(self):
        """A controller-only remote observation needs no local instance."""
        import sandbox.cli as cli
        import sandbox.commands.lifecycle as lifecycle
        import sandbox.commands.migrate as migrate

        remote_result = {"ok": True, "status": "ready"}
        for command in ("status", "logs"):
            argv = [
                "sb", command, "--remote", "fixture-remote",
                "--project-dir", "/srv/staged-project", "--workspace", "outer",
            ]
            with self.subTest(command=command), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(lifecycle, "_remote_lifecycle",
                                      return_value=(remote_result if command == "status"
                                                    else {"ok": True, "output": "ready\n"})) as remote, \
                    mock.patch.object(cli, "load_config",
                                      side_effect=AssertionError("local config loaded")), \
                    mock.patch.object(cli, "resolve_instances",
                                      side_effect=AssertionError("local registry loaded")), \
                    mock.patch.object(cli, "_cwd_instance",
                                      side_effect=AssertionError("controller cwd consulted")), \
                    mock.patch.object(cli, "preflight_instance_capability",
                                      side_effect=AssertionError("local capability checked")), \
                    mock.patch.object(migrate, "maybe_auto_migrate",
                                      side_effect=AssertionError("migration attempted")), \
                    mock.patch.object(migrate, "finalize_auto_migration",
                                      side_effect=AssertionError("finalization attempted")), \
                    mock.patch.object(cli, "write_compose_files",
                                      side_effect=AssertionError("compose written")), \
                    mock.patch.object(cli, "write_env_for_compose",
                                      side_effect=AssertionError("env written")):
                cli.main()

            remote.assert_called_once()
            observed_args = remote.call_args.args[1]
            self.assertIsNone(observed_args.resolved_instance)
            self.assertEqual(observed_args.project_dir, "/srv/staged-project")

    def test_direct_remote_instance_observation_skips_local_gate(self):
        """An explicit remote instance does not need a local project checkout."""
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        for command in ("status", "logs"):
            argv = [
                "sb", command, "--remote", "fixture-remote",
                "--instance", "remote-instance", "--json",
            ]
            with self.subTest(command=command), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(cli, "COMMANDS", {
                        command: lambda _cfg, args: observed.append((
                            args.resolved_instance, args.instance, args.project_dir,
                        )),
                    }), \
                    mock.patch.object(cli, "load_config",
                                      side_effect=AssertionError("local config loaded")), \
                    mock.patch.object(cli, "resolve_instances",
                                      side_effect=AssertionError("local registry loaded")), \
                    mock.patch.object(cli, "_cwd_instance",
                                      side_effect=AssertionError("controller cwd consulted")), \
                    mock.patch.object(migrate, "maybe_auto_migrate",
                                      side_effect=AssertionError("migration attempted")), \
                    mock.patch.object(migrate, "finalize_auto_migration",
                                      side_effect=AssertionError("finalization attempted")), \
                    mock.patch.object(cli, "write_compose_files",
                                      side_effect=AssertionError("compose written")), \
                    mock.patch.object(cli, "write_env_for_compose",
                                      side_effect=AssertionError("env written")):
                cli.main()

        self.assertEqual(observed, [(None, "remote-instance", None),
                                    (None, "remote-instance", None)])

    def test_project_routed_local_observation_uses_root_selector_and_skips_writes(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        for selector, record, expected in (
            (None, {"instance": "inner-default"}, "inner-default"),
            ("qa", {"instance": "inner-qa", "label": "qa"}, "inner-qa"),
        ):
            argv = ["sb", "status", "--local", "--project-dir", "/srv/staged-project"]
            if selector:
                argv.extend(["--label", selector])
            observed = []
            core = mock.Mock()
            core.registry_all.return_value = {}
            core.find_project_root.return_value = "/srv/staged-project"
            core.registry_list_for_root.return_value = [record]
            with self.subTest(selector=selector), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(cli, "COMMANDS", {
                        "status": lambda _cfg, args: observed.append(args.resolved_instance),
                    }), \
                    mock.patch.object(cli, "load_config", return_value={}), \
                    mock.patch.object(cli, "resolve_instances", return_value={expected: {}}), \
                    mock.patch.object(cli, "_core", return_value=core), \
                    mock.patch.object(cli, "resolve_registered_instance",
                                      return_value=record) as resolve_root, \
                    mock.patch.object(cli, "_cwd_instance",
                                      side_effect=AssertionError("controller cwd consulted")), \
                    mock.patch.object(cli, "write_compose_files") as compose, \
                    mock.patch.object(cli, "write_env_for_compose") as env, \
                    mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                    mock.patch.object(migrate, "finalize_auto_migration") as finalize:
                cli.main()

            self.assertEqual(observed, [expected])
            resolve_root.assert_called_once_with("/srv/staged-project", label=selector)
            compose.assert_not_called()
            env.assert_not_called()
            auto_migrate.assert_not_called()
            finalize.assert_not_called()

    def test_project_dir_status_is_local_by_default(self):
        """A local project selector must not require a redundant --local flag."""
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        record = {"instance": "inner-default", "label": "default"}
        core = mock.Mock()
        core.registry_all.return_value = {}
        with mock.patch.object(sys, "argv", [
                "sb", "status", "--project-dir", "/srv/staged-project",
        ]), \
                mock.patch.object(cli, "COMMANDS", {
                    "status": lambda _cfg, args: observed.append(args.resolved_instance),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances",
                                  return_value={"inner-default": {}}), \
                mock.patch.object(cli, "_core", return_value=core), \
                mock.patch.object(cli, "resolve_registered_instance",
                                  return_value=record) as resolve_root, \
                mock.patch.object(cli, "_cwd_instance",
                                  side_effect=AssertionError("controller cwd consulted")), \
                mock.patch.object(cli, "write_compose_files") as compose, \
                mock.patch.object(cli, "write_env_for_compose") as env, \
                mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                mock.patch.object(migrate, "finalize_auto_migration") as finalize:
            cli.main()

        self.assertEqual(observed, ["inner-default"])
        resolve_root.assert_called_once_with("/srv/staged-project", label=None)
        compose.assert_not_called()
        env.assert_not_called()
        auto_migrate.assert_not_called()
        finalize.assert_not_called()

    def test_explicit_instance_status_does_not_consult_controller_cwd(self):
        """A named local instance is sufficient when invoked outside its project."""
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        core = mock.Mock()
        core.registry_all.return_value = {}
        with mock.patch.object(sys, "argv", [
                "sb", "status", "--instance", "known",
        ]), \
                mock.patch.object(cli, "COMMANDS", {
                    "status": lambda _cfg, args: observed.append(args.resolved_instance),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances",
                                  return_value={"known": {}}), \
                mock.patch.object(cli, "_core", return_value=core), \
                mock.patch.object(cli, "_cwd_instance",
                                  side_effect=AssertionError("controller cwd consulted")), \
                mock.patch.object(cli, "write_compose_files") as compose, \
                mock.patch.object(cli, "write_env_for_compose") as env, \
                mock.patch.object(migrate, "maybe_auto_migrate") as auto_migrate, \
                mock.patch.object(migrate, "finalize_auto_migration",
                                  return_value=False) as finalize:
            cli.main()

        self.assertEqual(observed, ["known"])

    def test_project_routed_local_observation_preserves_known_and_unknown_explicit_instance(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        for instance, known, expected_error in (
            ("known", {"known": {}}, None),
            ("missing", {}, "unknown instance 'missing'"),
        ):
            observed = []
            core = mock.Mock()
            core.registry_all.return_value = {}
            argv = [
                "sb", "status", "--local", "--project-dir", "/srv/staged-project",
                "--instance", instance,
            ]
            errors = StringIO()
            with self.subTest(instance=instance), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(cli, "COMMANDS", {
                        "status": lambda _cfg, args: observed.append(args.resolved_instance),
                    }), \
                    mock.patch.object(cli, "load_config", return_value={}), \
                    mock.patch.object(cli, "resolve_instances", return_value=known), \
                    mock.patch.object(cli, "_core", return_value=core), \
                    mock.patch.object(cli, "resolve_registered_instance",
                                      side_effect=AssertionError("explicit selector replaced")), \
                    mock.patch.object(cli, "_cwd_instance",
                                      side_effect=AssertionError("controller cwd consulted")), \
                    mock.patch.object(cli, "write_compose_files"), \
                    mock.patch.object(cli, "write_env_for_compose"), \
                    mock.patch.object(migrate, "maybe_auto_migrate"), \
                    mock.patch.object(migrate, "finalize_auto_migration", return_value=False), \
                    redirect_stderr(errors):
                if expected_error:
                    with self.assertRaises(SystemExit) as raised:
                        cli.main()
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn(expected_error, errors.getvalue())
                else:
                    cli.main()
            if expected_error is None:
                self.assertEqual(observed, [instance])

    def test_project_routed_local_observation_refuses_ambiguity_and_missing_root(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        import sandbox_core
        for failure, expected in (
            (sandbox_core.ConfigError("project /srv/staged-project has multiple registered instances (default, qa); pass an exact --label"),
             "multiple registered instances"),
            (None, "no sandbox instance for project directory"),
        ):
            errors = StringIO()
            core = mock.Mock()
            core.registry_all.return_value = {}
            resolve_kwargs = {"side_effect": failure} if failure is not None else {
                "return_value": None,
            }
            with self.subTest(expected=expected), \
                    mock.patch.object(sys, "argv", [
                        "sb", "logs", "--local", "--project-dir", "/srv/staged-project",
                    ]), \
                    mock.patch.object(cli, "COMMANDS", {"logs": mock.Mock()}), \
                    mock.patch.object(cli, "load_config", return_value={}), \
                    mock.patch.object(cli, "resolve_instances", return_value={}), \
                    mock.patch.object(cli, "_core", return_value=core), \
                    mock.patch.object(cli, "resolve_registered_instance",
                                      **resolve_kwargs), \
                    mock.patch.object(cli, "_cwd_instance",
                                      side_effect=AssertionError("controller cwd consulted")), \
                    mock.patch.object(cli, "write_compose_files") as compose, \
                    mock.patch.object(cli, "write_env_for_compose") as env, \
                    mock.patch.object(migrate, "maybe_auto_migrate"), \
                    mock.patch.object(migrate, "finalize_auto_migration", return_value=False), \
                    redirect_stderr(errors):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn(expected, errors.getvalue())
            compose.assert_not_called()
            env.assert_not_called()

    def test_outer_remote_project_observation_rejects_instance_in_every_parser_position(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        for argv in (
            ["sb", "--instance", "inner", "status", "--remote", "r", "--project-dir", "/srv/p"],
            ["sb", "status", "--instance", "inner", "--remote", "r", "--project-dir", "/srv/p"],
            ["sb", "--instance=inner", "logs", "--remote", "r", "--project-dir", "/srv/p"],
        ):
            errors = StringIO()
            with self.subTest(argv=argv), \
                    mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(cli, "load_config",
                                      side_effect=AssertionError("local config loaded")), \
                    mock.patch.object(cli, "COMMANDS", {
                        "status": mock.Mock(), "logs": mock.Mock(),
                    }), \
                    mock.patch.object(migrate, "maybe_auto_migrate",
                                      side_effect=AssertionError("migration attempted")), \
                    redirect_stderr(errors):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("cannot combine --instance", errors.getvalue())

    def test_wordpress_only_legacy_commands_have_capability_gates(self):
        import sandbox.cli as cli
        self.assertEqual(cli.CLI_CAPABILITIES["wp"], "wordpress.cli")
        self.assertEqual(cli.CLI_CAPABILITIES["snapshot"], "wordpress.snapshot")
        self.assertEqual(cli.CLI_CAPABILITIES["shell"], "wordpress.exec")
        # Browser inspection is URL-scoped and works for generic Compose too;
        # it must not be rejected by a WordPress-only REST capability gate.
        self.assertNotIn("visit", cli.CLI_CAPABILITIES)
        self.assertNotIn("up", cli.CLI_CAPABILITIES)
        self.assertNotIn("open", cli.CLI_CAPABILITIES)

    def test_generic_init_types_are_parser_visible(self):
        r = run_sb("init", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("astro", r.stdout)
        self.assertIn("compose", r.stdout)

    def test_version_flag_reports_checked_in_version_without_setup(self):
        r = run_sb("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        expected = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(r.stdout.strip(), f"sandbox {expected}")
        self.assertEqual(r.stderr, "")

    def test_singular_instance_list_help_exposes_inventory_alias(self):
        r = run_sb("instance", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("list", r.stdout)

    def test_final_public_command_inventory_matches_the_owned_manifest(self):
        from sandbox.commands.manifest import load_builtin_commands, validate_builtin_command_coverage
        from sandbox.registry import COMMANDS, COMMAND_SPECS

        load_builtin_commands()
        self.assertEqual(len(COMMANDS), 89)
        self.assertEqual(tuple(sorted(COMMANDS)), tuple(sorted(COMMAND_SPECS.names())))
        self.assertEqual(validate_builtin_command_coverage(), ())

    def test_instance_scoped_command_errors_outside_project(self):
        # `status` is instance-scoped; from a non-registered dir it must abort
        # with guidance and a non-zero exit — never silently target `main`.
        r = run_sb("status")
        self.assertNotEqual(r.returncode, 0)
        out = (r.stderr + r.stdout).lower()
        self.assertIn("no sandbox instance", out)
        self.assertNotIn("instance: main", out)

    def test_registry_wide_command_runs_anywhere(self):
        # `instances` is registry-wide → works from any directory.
        r = run_sb("instances")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unknown_instance_is_rejected(self):
        r = run_sb("status", "--instance", "definitely-not-a-real-instance")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown instance", (r.stderr + r.stdout).lower())

    def test_help_lists_selftest(self):
        r = run_sb("--help")
        self.assertIn("selftest", r.stdout + r.stderr)

    def test_cli_first_runtime_commands_are_visible(self):
        r = run_sb("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("exec", r.stdout)
        self.assertIn("guide", r.stdout)

    def test_guide_is_available_without_an_instance(self):
        r = run_sb("guide", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        guide = json.loads(r.stdout)
        self.assertEqual(guide["mode"], "cli-first")
        self.assertEqual(guide["project_kind"], "compose")
        self.assertIn("sandbox-cli", guide["skill"])

    def test_guide_catalog_covers_public_registry_with_explicit_exclusions(self):
        r = run_sb("guide", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        guide = json.loads(r.stdout)
        from sandbox.commands.manifest import load_builtin_commands
        from sandbox.registry import COMMAND_SPECS
        load_builtin_commands()
        names = {item["name"] for item in guide["command_catalog"]}
        exclusions = set(guide["command_catalog_exclusions"])
        self.assertEqual(names | exclusions, set(COMMAND_SPECS.names()))
        self.assertTrue(names)

    def test_guide_falls_back_to_module_invocation_without_wrapper(self):
        import sandbox.commands.runtime as runtime
        from types import SimpleNamespace
        from unittest import mock

        with mock.patch.object(runtime.Path, "cwd", return_value=Path("/tmp/no-sb-wrapper")), \
                mock.patch.object(runtime.Path, "exists", return_value=False), \
                mock.patch.object(runtime.shutil, "which", return_value=None), \
                mock.patch.object(runtime.sys, "argv", ["/tmp/no-sb-wrapper/sandbox-cli"]):
            self.assertIn("-m sandbox.cli", runtime._guide_invocation())

    def test_incomplete_cli_venv_is_recreated_before_bootstrap(self):
        import sandbox.core._config as config

        with tempfile.TemporaryDirectory() as directory:
            incomplete = Path(directory) / ".cli-venv"
            incomplete.mkdir()
            (incomplete / "partial.marker").write_text("interrupted")
            calls = []

            with mock.patch.object(config, "CLI_VENV", incomplete), \
                    mock.patch.dict(sys.modules, {"yaml": None}), \
                    mock.patch.object(config.subprocess, "check_call",
                                      side_effect=lambda argv: calls.append(argv)), \
                    mock.patch.object(config.sys, "prefix", str(incomplete)):
                config.ensure_pyyaml()

            self.assertFalse(incomplete.exists())
            self.assertEqual(calls[0][1:3], ["-m", "venv"])
            self.assertEqual(calls[1][1:3], ["install", "--quiet"])

    def test_global_label_before_subcommand_is_preserved(self):
        import sandbox.cli as cli
        self.assertEqual(cli._global_label_before_subcommand(
            ["--label", "qa", "status", "--json"]), "qa")

    def test_ensure_pyyaml_never_execs_a_foreign_entry_point(self):
        # Regression: under unittest discovery the historical unconditional
        # re-exec replayed `sb discover -s tests` via os.execv, silently
        # replacing the whole test-runner process (no summary, exit 2).
        import sandbox.core._config as config

        execed = []

        def _trap(*argv, **kwargs):
            execed.append(argv)
            raise AssertionError("os.execv must not run for foreign callers")

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            venv = Path(directory) / ".cli-venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").write_text("#!/bin/sh\n")
            with mock.patch.dict(sys.modules, {"yaml": None}), \
                    mock.patch.object(config.sys, "prefix", "/nonexistent-prefix"), \
                    mock.patch.object(config.sys, "argv",
                                      ["/usr/bin/python", "discover", "-s", "tests"]), \
                    mock.patch.object(config.os, "execv", side_effect=_trap), \
                    mock.patch.object(config, "CLI_VENV", venv), \
                    redirect_stderr(err), \
                    self.assertRaises(SystemExit) as raised:
                config.ensure_pyyaml()

        self.assertEqual(execed, [])
        self.assertIn("PyYAML", err.getvalue())
        self.assertEqual(raised.exception.code, 1)

    def test_ensure_pyyaml_still_replays_a_genuine_cli_invocation(self):
        import sandbox.core._config as config

        replayed = []

        def _fake_execv(path, argv):
            replayed.append((path, argv))
            raise SystemExit(51)

        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "sb"
            entry.write_text("#!/bin/sh\n")
            with mock.patch.dict(sys.modules, {"yaml": None}), \
                    mock.patch.object(config.sys, "prefix", "/nonexistent-prefix"), \
                    mock.patch.object(config.sys, "argv",
                                      [str(entry), "status", "--json"]), \
                    mock.patch.object(config.os, "execv", side_effect=_fake_execv), \
                    mock.patch.object(config, "ENTRY", entry), \
                    mock.patch.object(config, "CLI_VENV",
                                      Path(directory) / ".cli-venv"), \
                    mock.patch.object(config, "die",
                                      lambda *a, **k: (_ for _ in ()).throw(
                                          AssertionError("must exec, not die"))):
                with self.assertRaises(SystemExit) as raised:
                    config.ensure_pyyaml()

        self.assertEqual(raised.exception.code, 51)
        path, argv = replayed[0]
        self.assertTrue(path.endswith("python"))
        self.assertEqual(argv[1:], [str(entry), "status", "--json"])

    def test_restore_help_exposes_explicit_noninteractive_confirmation(self):
        r = run_sb("restore", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--yes", r.stdout)

    def test_doctor_help_exposes_machine_readable_report(self):
        r = run_sb("doctor", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--json", r.stdout)
        self.assertIn("--instance", r.stdout)
        self.assertIn("--label", r.stdout)
        self.assertIn("sb instances", r.stdout)
        self.assertIn("--project-dir DIR --json", r.stdout)
        self.assertNotIn("--remote REMOTE", r.stdout)

    def test_deploy_help_exposes_immutable_source_ref(self):
        r = run_sb("deploy", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--source-ref", r.stdout)
        self.assertIn("--deploy-timeout", r.stdout)

    def test_wp_payload_stdout_is_clean_and_diagnostics_use_stderr(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False,
        )
        out, err = StringIO(), StringIO()
        result = SimpleNamespace(returncode=0, stdout="https://example.test\n",
                                 stderr="docker compose: selected wp service\n")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                redirect_stdout(out), redirect_stderr(err):
            command.cmd_wp({}, args)
        wpcli.assert_called_once_with(["option", "get", "siteurl"],
                                      instance="fixture", check=False, capture=True,
                                      timeout=60)
        self.assertEqual(out.getvalue(), "https://example.test\n")
        self.assertIn("docker compose", err.getvalue())

    def test_wp_strips_documented_separator_before_wp_cli(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["--", "plugin", "list"],
            run_async=False,
        )
        result = SimpleNamespace(returncode=0, stdout="plugin\n", stderr="")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            command.cmd_wp({}, args)
        self.assertEqual(wpcli.call_args.args[0], ["plugin", "list"])

    def test_wp_post_list_rejects_unsupported_search_before_execution(self):
        import sandbox.commands.wp as command

        for search in (["--search", "Target"], ["--search=Target"]):
            with self.subTest(search=search):
                args = SimpleNamespace(
                    resolved_instance="fixture",
                    passthrough=["post", "list", *search, "--format=ids"],
                    run_async=False,
                )
                err = StringIO()
                with mock.patch.object(command, "preflight_instance_capability",
                                       return_value=None), \
                        mock.patch.object(command, "wpcli") as wpcli, \
                        redirect_stderr(err):
                    with self.assertRaises(SystemExit) as raised:
                        command.cmd_wp({}, args)
                self.assertEqual(raised.exception.code, 1)
                self.assertIn("does not support --search", err.getvalue())
                self.assertIn("unfiltered list", err.getvalue())
                wpcli.assert_not_called()

    def test_wp_post_list_rejects_search_without_a_value(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture",
            passthrough=["post", "list", "--search", "--format=ids"],
            run_async=False,
        )
        err = StringIO()
        with mock.patch.object(command, "preflight_instance_capability",
                               return_value=None), \
                mock.patch.object(command, "wpcli") as wpcli, \
                redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                command.cmd_wp({}, args)
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("no command was executed", err.getvalue())
        wpcli.assert_not_called()

    def test_wp_async_post_list_rejects_search_before_launching_job(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture",
            passthrough=["--", "post", "list", "--search=Target"],
            run_async=True,
        )
        err = StringIO()
        with mock.patch.object(command, "preflight_instance_capability",
                               return_value=None), \
                mock.patch("sandbox.commands.jobs.launch_job") as launch_job, \
                redirect_stderr(err):
            with self.assertRaises(SystemExit):
                command.cmd_wp({}, args)
        self.assertIn("does not support --search", err.getvalue())
        launch_job.assert_not_called()

    def test_wp_other_commands_preserve_search_passthrough(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture",
            passthrough=["plugin", "search", "--search=Target"],
            run_async=False,
        )
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(command, "preflight_instance_capability",
                               return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            command.cmd_wp({}, args)
        self.assertEqual(wpcli.call_args.args[0],
                         ["plugin", "search", "--search=Target"])

    def test_wp_timeout_parser_rejects_async_combination_before_instance_work(self):
        r = run_sb("wp", "--async", "--timeout", "5", "--", "option", "get", "siteurl")
        self.assertEqual(r.returncode, 2)
        self.assertIn("argument --timeout: not allowed with argument --async", r.stderr)
        self.assertNotIn("no sandbox instance", r.stderr + r.stdout)

    def test_wp_accepts_explicit_local_selector(self):
        r = run_sb("wp", "--local", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("explicitly select the local WordPress runtime", r.stdout)

    def test_wp_project_dir_resolves_registered_instance_outside_project_cwd(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        observed = []
        argv = ["sb", "wp", "--project-dir", "/fixture", "--", "option", "get", "siteurl"]
        core = SimpleNamespace(
            registry_all=lambda: {},
            find_project_root=lambda path: Path(path),
            registry_list_for_root=lambda _root: [{"instance": "fixture", "label": "default"}],
        )
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(cli, "COMMANDS", {
                    "wp": lambda _cfg, args: observed.append(args),
                }), \
                mock.patch.object(cli, "load_config", return_value={}), \
                mock.patch.object(cli, "resolve_instances", return_value={}), \
                mock.patch.object(cli, "_cwd_instance", return_value=None), \
                mock.patch.object(cli, "resolve_registered_instance",
                                  return_value={"instance": "fixture", "label": "default"}), \
                mock.patch.object(cli, "_core", return_value=core), \
                mock.patch.object(cli, "preflight_instance_capability", return_value=None), \
                mock.patch.object(migrate, "maybe_auto_migrate"), \
                mock.patch.object(migrate, "finalize_auto_migration", return_value=False), \
                mock.patch.object(cli, "write_compose_files"), \
                mock.patch.object(cli, "write_env_for_compose"):
            cli.main()

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].resolved_instance, "fixture")
        self.assertEqual(observed[0].project_dir, "/fixture")
        self.assertEqual(observed[0].passthrough, ["--", "option", "get", "siteurl"])

    def test_wp_timeout_parser_enforces_one_to_3600_seconds(self):
        for value in ("0", "3601", "not-an-integer"):
            with self.subTest(value=value):
                r = run_sb("wp", "--timeout", value, "--", "option", "get", "siteurl")
                self.assertEqual(r.returncode, 2)
                self.assertIn("--timeout must be an integer from 1 to 3600 seconds", r.stderr)

    def test_exec_requires_an_instance_outside_a_project(self):
        r = run_sb("exec", "--", "echo", "hello")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no sandbox instance", r.stderr + r.stdout)

    def test_test_command_lists_explicit_modes(self):
        r = run_sb("test", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto", r.stdout)
        self.assertIn("unit", r.stdout)
        self.assertIn("integration", r.stdout)

    def test_help_lists_hermes_control_plane(self):
        r = run_sb("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hermes", r.stdout + r.stderr)

    def test_hermes_requires_explicit_remote(self):
        r = run_sb("hermes", "status")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--remote", r.stderr + r.stdout)

    def test_hermes_v2_actions_are_listed_in_help(self):
        r = run_sb("hermes", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backup", r.stdout)
        self.assertIn("update", r.stdout)
        self.assertIn("policy", r.stdout)
        self.assertIn("acceptance", r.stdout)
        self.assertIn("--confirm", r.stdout)
        self.assertIn("--force-replace", r.stdout)
        self.assertIn("worktree", r.stdout)

    def test_scheduler_mutations_fail_closed_before_remote_lookup(self):
        remote = "missing-scheduler-remote"
        for command, expected in (
            (("cron", "reconcile", "--force-replace"), "unknown_remote"),
            (("cron", "verify", "deadbeef1234"), "confirmation_required"),
            (("repo", "sync", "--repo", "sandbox"), "confirmation_required"),
        ):
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(json.loads(r.stdout)["error"]["code"], expected)

    def test_hermes_dashboard_options_are_listed_in_help(self):
        r = run_sb("hermes", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--port", r.stdout)
        self.assertIn("--fqdn", r.stdout)
        self.assertIn("--plan", r.stdout)
        self.assertNotIn("--insecure", r.stdout)
        self.assertIn("--basic-auth-secret", r.stdout)

    def test_dashboard_public_subcommands_are_parser_safe(self):
        remote = "missing-public-dashboard-remote"
        for command, expected in (
            (("dashboard", "exposure-status"), "unknown_remote"),
            (("dashboard", "expose", "--fqdn", "other.asb.bd", "--plan"), "unknown_remote"),
            (("dashboard", "basic-auth", "set"), "unknown_remote"),
        ):
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(json.loads(r.stdout)["error"]["code"], expected)

    def test_dashboard_insecure_option_is_not_accepted_by_the_parser(self):
        r = run_sb("hermes", "dashboard", "status", "--remote", "test", "--insecure")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unrecognized arguments: --insecure", r.stderr)

    def test_dashboard_refuses_before_v2_without_remote_mutation(self):
        r = run_sb("hermes", "dashboard", "install", "--remote", "missing-dashboard-remote", "--json")
        self.assertNotEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["error"]["code"], "unknown_remote")

    def test_hermes_protected_v2_actions_refuse_before_remote_lookup(self):
        remote = "missing-remote-for-confirmation-test"
        cases = [
            ("backup", "restore", "--backup-id", "20260711T000000Z-deadbeef"),
            ("update", "apply", "--version", "v2026.7.7.2"),
        ]
        for action, subaction, option, value in cases:
            with self.subTest(action=action):
                r = run_sb("hermes", action, subaction, "--remote", remote, option, value, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "confirmation_required")

    def test_hermes_v2_read_commands_keep_the_json_envelope(self):
        remote = "missing-remote-for-v2-read-contract"
        cases = [
            ("update", "plan", "--version", "v2026.7.7.2"),
            ("backup", "list"),
            ("cleanup",),
            ("health",),
            ("acceptance", "v2"),
        ]
        for case in cases:
            with self.subTest(case=case):
                r = run_sb("hermes", *case, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertEqual(payload["error"]["code"], "unknown_remote")

    def test_hermes_gateway_and_async_parser_failures_are_json_safe_before_remote_access(self):
        remote = "missing-remote-for-parser-contract"
        cases = [
            ("gateway",),
            ("job", "status"),
            ("run", "--async"),
        ]
        for case in cases:
            with self.subTest(case=case):
                r = run_sb("hermes", *case, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertIn(payload["error"]["code"], {"missing_gateway_action", "missing_job_id", "missing_run_input"})

    def test_hermes_repository_subcommands_have_stable_json_failures(self):
        remote = "missing-remote-for-repository-contract"
        cases = [
            (("repo", "auth", "gitlab"), "unsupported_provider"),
            (("repo", "clone"), "missing_repo_url"),
            (("repo", "list"), "unknown_remote"),
        ]
        for command, expected_code in cases:
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertEqual(payload["error"]["code"], expected_code)

    def test_hermes_repository_auth_rejects_broad_oauth_and_advertises_token_stdin(self):
        remote = "missing-remote-for-least-privilege-auth"
        r = run_sb("hermes", "repo", "auth", "github", "--remote", remote, "--json")
        self.assertNotEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["error"]["code"], "fine_grained_token_required")
        help_output = run_sb("hermes", "--help")
        self.assertEqual(help_output.returncode, 0, help_output.stderr)
        self.assertIn("--token-stdin", help_output.stdout)

    def test_hermes_v1_command_contracts_are_json_safe(self):
        remote = "missing-remote-for-v1-contract"
        cases = [
            (("install", "--version", "main"), "invalid_release"),
            (("setup",), "unknown_remote"),
            (("doctor",), "unknown_remote"),
            (("status",), "unknown_remote"),
            (("chat",), "missing_repo"),
            (("run",), "missing_run_input"),
        ]
        for command, expected_code in cases:
            with self.subTest(command=command):
                r = run_sb("hermes", *command, "--remote", remote, "--json")
                self.assertNotEqual(r.returncode, 0)
                payload = json.loads(r.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["remote"], remote)
                self.assertEqual(payload["error"]["code"], expected_code)

    def test_no_main_in_help_command_list(self):
        # The phantom `main` instance is gone; it must not appear as guidance.
        r = run_sb("instances")
        self.assertNotIn(" main ", (r.stdout + r.stderr))


class TestRemoteAdmissionCLI(unittest.TestCase):
    @staticmethod
    def _admission_error():
        from sandbox.resources.network_capacity import evaluate_network_capacity
        from sandbox.transports.remote_jobs import RemoteJobAdmissionError

        decision = evaluate_network_capacity({"status": "partial"}, remote_name="vps")
        secret = "cli-admission-fixture-private-value"
        decision["evidence"]["ssh_output"] = secret
        decision["recovery"]["next_command"] = secret
        return RemoteJobAdmissionError(decision), secret

    def test_json_admission_output_is_one_bounded_parseable_payload(self):
        import sandbox.cli as cli

        admission, secret = self._admission_error()
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                cli._dispatch_remote_admission_error(
                    admission, SimpleNamespace(json=True),
                )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(errors.getvalue(), "")
        self.assertEqual(output.getvalue().count("\n"), 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["code"], "docker_network_capacity_unavailable")
        self.assertFalse(payload["retryable"])
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_human_admission_output_is_fixed_and_secret_free(self):
        import sandbox.cli as cli

        admission, secret = self._admission_error()
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                cli._dispatch_remote_admission_error(
                    admission, SimpleNamespace(json=False),
                )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(output.getvalue(), "")
        line = errors.getvalue()
        self.assertIn("remote job submission blocked by Docker network capacity admission", line)
        self.assertIn("docker_network_capacity_unavailable", line)
        self.assertIn("./sb remote docker-pool vps --json", line)
        self.assertNotIn(secret, line)
        self.assertNotIn("Traceback", line)

    def test_json_transport_error_is_one_bounded_receipt_unknown_envelope(self):
        import sandbox.cli as cli
        from sandbox.transports.remote_jobs import RemoteJobTransportError

        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                cli._dispatch_remote_transport_error(
                    RemoteJobTransportError("remote job acceptance failed: no payload"),
                    SimpleNamespace(json=True, remote="vps", cmd="exec"),
                )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(errors.getvalue(), "")
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["code"], "remote_job_transport_error")
        self.assertEqual(payload["status"], "unknown")
        self.assertEqual(payload["acceptance"], "unknown")
        self.assertEqual(payload["target"], {"kind": "remote", "remote": "vps"})
        self.assertNotIn("Traceback", output.getvalue())

    def test_human_transport_error_is_bounded_and_does_not_claim_a_job(self):
        import sandbox.cli as cli
        from sandbox.transports.remote_jobs import RemoteJobTransportError

        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                cli._dispatch_remote_transport_error(
                    RemoteJobTransportError("remote output read failed"),
                    SimpleNamespace(json=False, remote="vps", cmd="job-output"),
                )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("remote output read failed", errors.getvalue())
        self.assertIn("No remote job receipt was established", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_json_flag_before_passthrough_delimiter_does_not_consume_child_json(self):
        import sandbox.cli as cli
        import sandbox.commands.migrate as migrate

        admission, _secret = self._admission_error()
        captured = []

        def handler(_cfg, args):
            captured.append((args.json, args.passthrough))
            raise admission

        def run(argv):
            captured.clear()
            output, errors = StringIO(), StringIO()
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(cli, "COMMANDS", {"e2e": handler}), \
                    mock.patch.object(cli, "load_config", return_value={}), \
                    mock.patch.object(cli, "resolve_instances", return_value={}), \
                    mock.patch.object(cli, "_cwd_instance", return_value=None), \
                    mock.patch.object(migrate, "maybe_auto_migrate"), \
                    mock.patch.object(migrate, "finalize_auto_migration", return_value=False), \
                    mock.patch.object(cli, "write_compose_files"), \
                    mock.patch.object(cli, "write_env_for_compose"), \
                    redirect_stdout(output), redirect_stderr(errors):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
            return raised.exception.code, captured[:], output.getvalue(), errors.getvalue()

        code, seen, output, errors = run(["sb", "e2e", "--json", "--", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(seen, [(True, ["--", "--json"])])
        self.assertEqual(errors, "")
        self.assertFalse(json.loads(output)["ok"])

        code, seen, output, errors = run(["sb", "e2e", "--", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(seen, [(False, ["--", "--json"])])
        self.assertEqual(output, "")
        self.assertIn("remote job submission blocked by Docker network capacity admission", errors)

    def test_cwd_project_instance_survives_env_removal_via_persisted_selector(self):
        """A separately launched CLI still finds the project-owned instance."""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="sb-cli-home-") as td:
            base = Path(td)
            home = base / "home"
            selected = base / "selected"
            project = home / "project"
            (project / ".git").mkdir(parents=True)
            (project / "sandbox.config.json").write_text("{}\n")
            hint = home / ".config" / "sandbox" / "home"
            hint.parent.mkdir(parents=True)
            hint.write_text(str(selected) + "\n")

            env = os.environ.copy()
            env["HOME"] = str(home)
            env.pop("SANDBOX_HOME", None)
            env.pop("SANDBOX_RUNTIME", None)
            env["PYTHONPATH"] = str(root)
            register = (
                "import sandbox_core; sandbox_core.registry_put(%r, instance=%r)"
                % (str(project), "cli-instance")
            )
            created = subprocess.run(
                [sys.executable, "-c", register], cwd=str(root), env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            probe = "from sandbox.cli import _cwd_instance; print(_cwd_instance())"
            resolved = subprocess.run(
                [sys.executable, "-c", probe], cwd=str(project), env=env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(resolved.stdout.strip(), "cli-instance")

    def test_cli_relative_selector_falls_back_to_cwd_independent_default(self):
        """CLI path resolution never interprets a relative bootstrap hint."""
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="sb-cli-relative-") as td:
            base = Path(td)
            home = base / "home"
            cwd_a = base / "cwd-a"
            cwd_b = base / "cwd-b"
            cwd_a.mkdir(parents=True)
            cwd_b.mkdir(parents=True)
            hint = home / ".config" / "sandbox" / "home"
            hint.parent.mkdir(parents=True)
            hint.write_text("relative-state\n")
            env = os.environ.copy()
            env["HOME"] = str(home)
            env.pop("SANDBOX_HOME", None)
            env.pop("SANDBOX_RUNTIME", None)
            env["PYTHONPATH"] = str(root)
            probe = (
                "import sandbox.cli; from sandbox.core._paths import _sandbox_base; "
                "print(_sandbox_base())"
            )
            for cwd in (cwd_a, cwd_b):
                with self.subTest(cwd=cwd.name):
                    resolved = subprocess.run(
                        [sys.executable, "-c", probe], cwd=str(cwd), env=env,
                        capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(resolved.returncode, 0, resolved.stderr)
                    self.assertEqual(resolved.stdout.strip(), str((home / "sandbox").resolve()))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestApplyProjectInference(unittest.TestCase):
    """`apply` reconciles a PROJECT. Without --project-dir it used to fall
    through to the whole-sandbox setup alias, so `apply --instance X` quietly
    re-applied everything instead of reconciling X."""

    def _cli(self):
        from sandbox import cli
        return cli

    def test_named_instance_resolves_its_registered_root(self):
        from unittest import mock

        cli = self._cli()
        core = mock.Mock()
        core.registry_find_instance.return_value = {"root": "/projects/demo"}
        with mock.patch.object(cli, "_core", return_value=core), \
             mock.patch.object(cli.Path, "is_dir", return_value=True):
            root, source = cli._implied_project_dir("demo", None)
        self.assertEqual(root, "/projects/demo")
        self.assertIn("demo", source)

    def test_unknown_instance_keeps_the_whole_sandbox_behaviour(self):
        from unittest import mock

        cli = self._cli()
        core = mock.Mock()
        core.registry_find_instance.return_value = None
        with mock.patch.object(cli, "_core", return_value=core):
            self.assertEqual(cli._implied_project_dir("missing", None), (None, None))

    def test_registered_cwd_project_is_used_when_no_instance_is_named(self):
        from unittest import mock

        cli = self._cli()
        core = mock.Mock()
        core.find_project_root.return_value = "/projects/demo"
        core.registry_get.return_value = {"instance": "demo"}
        with mock.patch.object(cli, "_core", return_value=core):
            root, source = cli._implied_project_dir(None, None)
        self.assertEqual(root, "/projects/demo")
        self.assertEqual(source, "current working directory")

    def test_unregistered_cwd_falls_back_to_the_setup_alias(self):
        from unittest import mock

        cli = self._cli()
        core = mock.Mock()
        core.find_project_root.return_value = "/somewhere"
        core.registry_get.return_value = None
        with mock.patch.object(cli, "_core", return_value=core):
            self.assertEqual(cli._implied_project_dir(None, None), (None, None))

    def test_a_cwd_outside_any_project_falls_back(self):
        from unittest import mock

        cli = self._cli()
        core = mock.Mock()
        core.find_project_root.side_effect = ValueError("not a project")
        with mock.patch.object(cli, "_core", return_value=core):
            self.assertEqual(cli._implied_project_dir(None, None), (None, None))


class TestSkillProjectDirContract(unittest.TestCase):
    """`skill` accepts --project-dir after the subcommand (feedback 05936f99)."""

    def test_parser_accepts_project_dir_after_the_skill_name(self):
        r = run_sb("skill", "show", "sandbox-cli", "--project-dir", str(ROOT),
                   cwd="/tmp")
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_documented_invocation_shows_a_skill_from_any_cwd(self):
        r = run_sb("skill", "show", "sandbox-cli", "--project-dir", str(ROOT),
                   cwd="/tmp")
        self.assertIn("Operate Sandbox", r.stdout)
