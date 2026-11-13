"""The host owner dispatcher runs after parsing and before laptop setup."""
from dataclasses import replace
import sys
import unittest
from unittest.mock import Mock, patch

from sandbox import cli
from sandbox.commands.manifest import load_builtin_commands


class HostOwnerPreconfigurationTests(unittest.TestCase):
    def setUp(self):
        load_builtin_commands()
        self.original_get = cli.COMMAND_SPECS.get
        self.argv = ["sb", "host", "stack", "up", "--remote", "fixture",
                     "--template", "sandbox.hosting.yml", "--name", "demo", "--plan", "--json"]

    def invoke(self, callback, argv=None):
        spec = replace(self.original_get("host"), preconfiguration_dispatch=callback)
        def selected(name):
            return spec if name == "host" else self.original_get(name)
        with patch.object(sys, "argv", self.argv if argv is None else argv), \
             patch.object(cli.COMMAND_SPECS, "get", side_effect=selected), \
             patch.object(cli, "load_config", side_effect=AssertionError("laptop config lookup")), \
             patch("sandbox.controller_update.hook.maybe_auto_update",
                   side_effect=AssertionError("controller update before dispatch")):
            return cli.main()

    def test_handled_owner_request_precedes_laptop_configuration_and_update(self):
        observed = []
        def handle(args):
            observed.append(args)
            return True
        self.invoke(handle)
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].remote, "fixture")
        self.assertEqual(observed[0]._server_cli_argv, tuple(self.argv[1:]))

    def test_lost_selected_owner_authority_cannot_fall_back_to_laptop_setup(self):
        callback = Mock(side_effect=RuntimeError("original owner unavailable"))
        with self.assertRaisesRegex(RuntimeError, "original owner unavailable"):
            self.invoke(callback)
        callback.assert_called_once()

    def test_invalid_host_positionals_refuse_before_owner_dispatch(self):
        callback = Mock(return_value=True)
        with self.assertRaises(SystemExit):
            self.invoke(callback, ["sb", "host", "status", "unexpected.example.invalid"])
        callback.assert_not_called()
