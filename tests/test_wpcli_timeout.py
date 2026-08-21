"""Timeout and routing contracts for the Docker WP-CLI helper."""
from __future__ import annotations

import unittest
from unittest import mock
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from types import SimpleNamespace
import subprocess

from sandbox.core import _docker


class _Result:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class TestWpCliTimeout(unittest.TestCase):
    def setUp(self):
        self.previous = dict(_docker._WP_CLI_BUILTIN)
        _docker._WP_CLI_BUILTIN.clear()
        # ``wpcli`` checks the managed-native gateway before selecting the
        # Compose route.  This test is about timeout/routing mechanics, so keep
        # that unrelated composition boundary inert under the unittest argv.
        self.gate = mock.patch.object(_docker, "_managed_execution_gate", return_value=None)
        self.gate.start()
        # Docker routing tests do not exercise Herd/config discovery.  The
        # latter may bootstrap the CLI venv (and re-exec) under a bare system
        # Python, turning unittest's module selector into Sandbox CLI argv.
        self.herd = mock.patch.object(_docker, "_is_herd_instance", return_value=False)
        self.herd.start()

    def tearDown(self):
        self.herd.stop()
        self.gate.stop()
        _docker._WP_CLI_BUILTIN.clear()
        _docker._WP_CLI_BUILTIN.update(self.previous)

    def test_builtin_preflight_forwards_timeout(self):
        with mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            self.assertTrue(_docker._wp_has_builtin_cli("fixture", timeout=2.5))
        self.assertEqual(compose.call_args.kwargs["timeout"], 2.5)

    def test_wpcli_forwards_timeout_to_builtin_preflight_and_exec(self):
        with mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            _docker.wpcli(["core", "is-installed"], instance="fixture",
                          check=False, capture=True, timeout=7)
        self.assertEqual(compose.call_count, 2)
        self.assertEqual(compose.call_args_list[0].kwargs["timeout"], 7)
        self.assertEqual(compose.call_args_list[1].kwargs["timeout"], 7)

    def test_wpcli_forwards_timeout_to_managed_execution_gate(self):
        gate_result = _Result()
        with mock.patch.object(_docker, "_managed_execution_gate",
                               return_value=gate_result) as gate:
            result = _docker.wpcli(["core", "is-installed"], instance="fixture",
                                   check=False, capture=True, timeout=17)
        self.assertIs(result, gate_result)
        self.assertEqual(gate.call_args.kwargs["timeout"], 17)

    def test_db_commands_skip_builtin_preflight(self):
        with mock.patch.object(_docker, "_wp_has_builtin_cli") as preflight, \
                mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            _docker.wpcli(["db", "query", "SELECT 1", "--skip-column-names"],
                          instance="fixture", check=False, capture=True, timeout=11)
        preflight.assert_not_called()
        compose.assert_called_once()
        self.assertEqual(compose.call_args.args[:3], ("run", "--rm", "wpcli"))
        self.assertEqual(compose.call_args.kwargs["timeout"], 11)

    def test_cmd_wp_uses_default_timeout_for_direct_namespace(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False,
        )
        result = SimpleNamespace(returncode=0, stdout="https://example.test\n", stderr="")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result) as wpcli, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            command.cmd_wp({}, args)
        self.assertEqual(wpcli.call_args.kwargs["timeout"], 60)

    def test_cmd_wp_timeout_preserves_partial_streams_and_is_uncertain(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False, timeout=3,
        )
        out, err = StringIO(), StringIO()
        expired = subprocess.TimeoutExpired(
            ["docker", "compose"], 3, output=b"partial stdout\n", stderr=b"partial stderr\n"
        )
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", side_effect=expired) as wpcli, \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                command.cmd_wp({}, args)
        self.assertEqual(raised.exception.code, 124)
        self.assertEqual(out.getvalue(), "partial stdout\n")
        self.assertIn("partial stderr\n", err.getvalue())
        self.assertIn("wp command timed out after 3 seconds; completion is unknown—inspect state before retrying, or use --async for long work", err.getvalue())
        wpcli.assert_called_once_with(["option", "get", "siteurl"],
                                      instance="fixture", check=False, capture=True,
                                      timeout=3)

    def test_cmd_wp_child_exit_124_is_not_treated_as_timeout(self):
        import sandbox.commands.wp as command

        args = SimpleNamespace(
            resolved_instance="fixture", passthrough=["option", "get", "siteurl"],
            run_async=False, timeout=3,
        )
        out, err = StringIO(), StringIO()
        result = SimpleNamespace(returncode=124, stdout="child output\n", stderr="child error\n")
        with mock.patch.object(command, "preflight_instance_capability", return_value=None), \
                mock.patch.object(command, "wpcli", return_value=result), \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                command.cmd_wp({}, args)
        self.assertEqual(raised.exception.code, 124)
        self.assertIn("child output\n", out.getvalue())
        self.assertIn("wp command failed with exit code 124", err.getvalue())
        self.assertNotIn("completion is unknown", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
