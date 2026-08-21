"""Timeout and routing contracts for the Docker WP-CLI helper."""
from __future__ import annotations

import unittest
from unittest import mock

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

    def tearDown(self):
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

    def test_db_commands_skip_builtin_preflight(self):
        with mock.patch.object(_docker, "_wp_has_builtin_cli") as preflight, \
                mock.patch.object(_docker, "compose", return_value=_Result()) as compose:
            _docker.wpcli(["db", "query", "SELECT 1", "--skip-column-names"],
                          instance="fixture", check=False, capture=True, timeout=11)
        preflight.assert_not_called()
        compose.assert_called_once()
        self.assertEqual(compose.call_args.args[:3], ("run", "--rm", "wpcli"))
        self.assertEqual(compose.call_args.kwargs["timeout"], 11)


if __name__ == "__main__":
    unittest.main(verbosity=2)
