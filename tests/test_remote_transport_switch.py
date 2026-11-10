import json
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

    def test_answering_no_selects_https(self):
        with patch.object(remote_cmd.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="n"):
            self.assertEqual(remote_cmd._choose_control_transport(types.SimpleNamespace(control=None, yes=False), False), "https")


HTTPS = {"ssh": "user@203.0.113.10", "provisioned": True, "control_transport": "https",
         "control_url": "https://control.example.test", "control_host": "control.example.test",
         "mcp_port": 9174, "bearer_token": "kept-token-sentinel"}


class RetireHttpsUnitTests(unittest.TestCase):
    def retire(self, *statuses, ssh_rc=0):
        commands = []

        def ssh_run(_entry, command, timeout=30, **_kwargs):
            commands.append(command)
            return types.SimpleNamespace(returncode=ssh_rc, stdout="", stderr="")

        probes = []

        def status(entry):
            probes.append(entry)
            return statuses[len(probes) - 1]

        self.commands, self.probes = commands, probes
        with patch.object(remote_cmd.sr, "remote_mcp_service_status", side_effect=status), \
                patch.object(remote_cmd.sr, "ssh_run", side_effect=ssh_run), \
                patch.object(remote_cmd.sr, "resolve_tailscale_ip", return_value="100.64.0.9"):
            remote_cmd._retire_https_control_unit(dict(HTTPS))

    def test_an_owned_unit_is_stopped_and_set_aside_not_deleted(self):
        self.retire({"ownership": "proven"})
        command = self.commands[0]
        self.assertIn("systemctl --user stop sandbox-mcp-remote.service", command)
        self.assertIn("mv -f $HOME/.config/systemd/user/sandbox-mcp-remote.service "
                      "$HOME/.sandbox/removed-units/sandbox-mcp-remote.service.", command)
        self.assertIn("umask 077", command)
        self.assertNotIn("rm ", command)
        self.assertEqual(self.probes[0]["control_transport"], "https")

    def test_a_unit_already_set_aside_needs_nothing(self):
        self.retire({"ownership": "missing"})
        self.assertEqual(self.commands, [])

    def test_a_rerun_after_the_tailscale_unit_started_needs_nothing(self):
        self.retire({"ownership": "ambiguous"}, {"ownership": "proven"})
        self.assertEqual(self.commands, [])
        self.assertEqual(self.probes[1]["control_url"], "http://100.64.0.9:9174")

    def test_a_unit_that_is_not_provably_ours_is_left_alone(self):
        with self.assertRaisesRegex(RuntimeError, "not_owned"):
            self.retire({"ownership": "ambiguous"}, {"ownership": "ambiguous"})
        self.assertEqual(self.commands, [])

    def test_an_incomplete_probe_changes_nothing(self):
        with self.assertRaises(RuntimeError):
            self.retire({"ownership": "unknown", "probe_state": "partial",
                         "probe_error": "remote_service_probe_unavailable"})
        self.assertEqual(self.commands, [])

    def test_a_failed_stop_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "not_retired"):
            self.retire({"ownership": "proven"}, ssh_rc=1)


class ProvisionSwitchPlanTests(unittest.TestCase):
    def plan(self, entry, control="tailscale"):
        import contextlib, io, json
        args = types.SimpleNamespace(name="box", control=control, confirm=False, force=False,
                                     front_door=None, json=True, yes=False, control_host="control.example.test")
        out = io.StringIO()
        with patch.object(remote_cmd.sr, "get_remote", return_value=dict(entry)), \
                patch.object(remote_cmd, "_latest_provision_log", return_value=None), \
                patch.object(remote_cmd.sr, "ssh_run", side_effect=AssertionError("no remote calls in a plan")), \
                contextlib.redirect_stdout(out):
            remote_cmd._cmd_provision(args, True)
        return json.loads(out.getvalue().strip().splitlines()[-1])

    def test_the_plan_describes_the_switch_and_keeps_the_token(self):
        result = self.plan(HTTPS)
        switch = result["transport_switch"]
        self.assertEqual((switch["from"], switch["to"], switch["keeps_token"]), ("https", "tailscale", True))
        self.assertIn("./sb remote rebind-host box --confirm", switch["follow_up"])
        self.assertNotIn("kept-token-sentinel", json.dumps(result))

    def test_no_switch_when_staying_on_https_or_not_yet_provisioned(self):
        self.assertNotIn("transport_switch", self.plan(HTTPS, control="https"))
        self.assertNotIn("transport_switch", self.plan({**HTTPS, "provisioned": False}))

