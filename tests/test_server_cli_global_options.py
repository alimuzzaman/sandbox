"""Global CLI selectors cannot bypass retained-command restrictions."""
import unittest

from sandbox.application import server_cli


class ServerCLIGlobalOptionsTests(unittest.TestCase):
    def test_global_options_do_not_bypass_secret_command_refusal(self):
        for prefix in (("--strict-runtime",), ("--label", "fixture"), ("--instance=fixture",)):
            with self.subTest(prefix=prefix), self.assertRaises(server_cli.ServerCLIUnavailable):
                server_cli.fixed_argv((*prefix, "secrets", "run", "--source", "personal", "--", "true"))

    def test_global_options_do_not_bypass_missing_stdin_transport(self):
        for prefix in (("--strict-runtime",), ("--label=fixture",), ("--instance", "fixture")):
            with self.subTest(prefix=prefix), self.assertRaises(server_cli.ServerCLIStdinUnavailable):
                server_cli.fixed_argv((*prefix, "host", "exec", "--remote", "fixture", "--", "cat"))

    def test_allowed_command_retains_its_global_options(self):
        command = ("--strict-runtime", "--label", "fixture", "host", "stack", "list")
        self.assertEqual(server_cli.fixed_argv(command)[1:], command)

    def test_options_before_sensitive_actions_do_not_hide_the_command(self):
        for command in (("secrets", "--label", "fixture", "run"),
                        ("host", "--remote", "fixture", "exec"),
                        ("--lab", "fixture", "secrets", "run")):
            with self.subTest(command=command), self.assertRaises(server_cli.ServerCLIUnavailable):
                server_cli.fixed_argv(command)
