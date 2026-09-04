import unittest
import json
from unittest.mock import MagicMock, patch
import argparse

# This import should fail in the RED phase because sandbox.commands.server does not exist yet.
from sandbox.commands.server import ServerCommand, setup_parser, handle, execute_server_config

class TestServerConfigCLI(unittest.TestCase):
    """T019: Command-owned grammar, legacy compatibility, JSON schema, and exit status."""

    def setUp(self):
        self.parser = argparse.ArgumentParser()
        setup_parser(self.parser)

    def test_server_command_registered(self):
        """Test that server command is registered as a feature-owned CommandSpec (not legacy)."""
        self.assertTrue(hasattr(ServerCommand, 'spec'))
        self.assertEqual(ServerCommand.spec.name, "server")

    def test_apply_grammar(self):
        """Test that server config apply --name NAME --file PATH is parsed correctly."""
        args = self.parser.parse_args(["config", "apply", "--name", "test-frag", "--file", "/tmp/valid.conf"])
        self.assertEqual(args.subcommand, "config")
        self.assertEqual(args.config_action, "apply")
        self.assertEqual(args.name, "test-frag")
        self.assertEqual(args.file, "/tmp/valid.conf")

    def test_list_grammar(self):
        """Test that server config list is parsed correctly."""
        args = self.parser.parse_args(["config", "list"])
        self.assertEqual(args.config_action, "list")

    def test_show_grammar(self):
        """Test that server config show --name NAME is parsed correctly."""
        args = self.parser.parse_args(["config", "show", "--name", "test-frag"])
        self.assertEqual(args.config_action, "show")
        self.assertEqual(args.name, "test-frag")

    def test_revert_grammar(self):
        """Test that server config revert --name NAME is parsed correctly."""
        args = self.parser.parse_args(["config", "revert", "--name", "test-frag"])
        self.assertEqual(args.config_action, "revert")
        self.assertEqual(args.name, "test-frag")

    def test_json_flag(self):
        """Test that --json flag is accepted on all config operations."""
        for action in ["apply", "list", "show", "revert"]:
            args_list = ["config", action, "--json"]
            if action in ["apply", "show", "revert"]:
                args_list.extend(["--name", "test-frag"])
            if action == "apply":
                args_list.extend(["--file", "/tmp/test.conf"])
            args = self.parser.parse_args(args_list)
            self.assertTrue(args.json)

    def test_stdin_file_mutually_exclusive(self):
        """Test that --stdin is mutually exclusive with --file."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["config", "apply", "--name", "test-frag", "--file", "/tmp/test.conf", "--stdin"])

    def test_authority_default(self):
        """Test that --authority defaults to wordpress-cache-v1."""
        args = self.parser.parse_args(["config", "apply", "--name", "test-frag", "--file", "/tmp/test.conf"])
        self.assertEqual(args.authority, "wordpress-cache-v1")

    def test_show_content_json_incompatible(self):
        """Test that show --content --json is invalid/incompatible."""
        with self.assertRaises(SystemExit):
            self.parser.parse_args(["config", "show", "--name", "test-frag", "--content", "--json"])

    def test_legacy_switch_nginx(self):
        """Test that sb server nginx is still recognized (legacy switch form)."""
        args = self.parser.parse_args(["nginx"])
        self.assertEqual(args.server_type, "nginx")

    def test_legacy_switch_litespeed_instance(self):
        """Test that sb server <instance> litespeed is still recognized (two-token legacy form)."""
        args = self.parser.parse_args(["my-instance", "litespeed"])
        self.assertEqual(args.instance, "my-instance")
        self.assertEqual(args.server_type, "litespeed")

    def test_legacy_switch_apache(self):
        """Test that sb server apache is still recognized."""
        args = self.parser.parse_args(["apache"])
        self.assertEqual(args.server_type, "apache")

    def test_legacy_switch_herd(self):
        """Test that sb server herd is still recognized."""
        args = self.parser.parse_args(["herd"])
        self.assertEqual(args.server_type, "herd")

    @patch('sandbox.commands.server.sys.stdout')
    def test_json_schema_apply(self, mock_stdout):
        """Test that successful apply returns JSON with ok, mutated, operation, outcome, instance, fragment, fragment_set, phases, transaction_id."""
        result = execute_server_config(MagicMock(config_action="apply", json=True))
        output = mock_stdout.write.call_args[0][0]
        data = json.loads(output)
        self.assertIn("ok", data)
        self.assertIn("mutated", data)
        self.assertIn("operation", data)
        self.assertIn("outcome", data)
        self.assertIn("instance", data)
        self.assertIn("fragment", data)
        self.assertIn("fragment_set", data)
        self.assertIn("phases", data)
        self.assertIn("transaction_id", data)

    @patch('sandbox.commands.server.sys.stdout')
    def test_json_schema_list(self, mock_stdout):
        """Test that list returns JSON with fragment array."""
        execute_server_config(MagicMock(config_action="list", json=True))
        output = mock_stdout.write.call_args[0][0]
        data = json.loads(output)
        self.assertIn("fragments", data)
        self.assertIsInstance(data["fragments"], list)

    @patch('sandbox.commands.server.sys.stdout')
    def test_json_schema_errors(self, mock_stdout):
        """Test that errors return ok:false, mutated:false, bounded error code."""
        execute_server_config(MagicMock(config_action="apply", json=True, fail_for_test=True))
        output = mock_stdout.write.call_args[0][0]
        data = json.loads(output)
        self.assertFalse(data.get("ok"))
        self.assertFalse(data.get("mutated"))
        self.assertIn("error_code", data)

    def test_exit_status_success(self):
        """Test exit 0 for active and no_op outcomes."""
        for outcome in ["active", "no_op"]:
            status = handle(MagicMock(outcome=outcome))
            self.assertEqual(status, 0)

    def test_exit_status_nonzero(self):
        """Test nonzero exit for refused, rolled_back, conflict, recovery_needed."""
        for outcome in ["refused", "rolled_back", "conflict", "recovery_needed"]:
            status = handle(MagicMock(outcome=outcome))
            self.assertNotEqual(status, 0)

    def test_predispatch_skip(self):
        """Test that list and metadata show use the read-only pre-dispatch path."""
        # predispatch_policy returns True to skip legacy writers
        result_list = ServerCommand.predispatch_policy(MagicMock(config_action="list"))
        self.assertTrue(result_list)

        result_show = ServerCommand.predispatch_policy(MagicMock(config_action="show", content=False))
        self.assertTrue(result_show)
