import types
import unittest
from unittest.mock import patch

from sandbox.commands import remote as remote_cmd


class ControlTransportDefaultTests(unittest.TestCase):
    def test_json_defaults_to_tailscale(self):
        self.assertEqual(remote_cmd._choose_control_transport(types.SimpleNamespace(control=None), True), "tailscale")

    def test_explicit_https_is_honored(self):
        self.assertEqual(remote_cmd._choose_control_transport(types.SimpleNamespace(control="https"), True), "https")

    def test_prompt_defaults_to_tailscale(self):
        with patch.object(remote_cmd.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="") as prompt:
            self.assertEqual(remote_cmd._choose_control_transport(types.SimpleNamespace(control=None, yes=False), False), "tailscale")
        self.assertIn("[Y/n]", prompt.call_args.args[0])
