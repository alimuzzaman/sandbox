import json
import types
import unittest
import unittest.mock
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
    def retire(self, *statuses, ssh_rc=0, backups=""):
        commands = []

        def ssh_run(_entry, command, timeout=30, **_kwargs):
            commands.append(command)
            if command.startswith("ls "):
                return types.SimpleNamespace(returncode=0, stdout=backups, stderr="")
            return types.SimpleNamespace(returncode=ssh_rc, stdout="", stderr="")

        probes = []

        def status(entry):
            probes.append(entry)
            return statuses[len(probes) - 1]

        self.commands, self.probes = commands, probes
        with patch.object(remote_cmd.sr, "remote_mcp_service_status", side_effect=status), \
                patch.object(remote_cmd.sr, "ssh_run", side_effect=ssh_run), \
                patch.object(remote_cmd.sr, "resolve_tailscale_ip", return_value="100.64.0.9"):
            self.result = remote_cmd._retire_https_control_unit(dict(HTTPS))

    def test_an_owned_unit_is_stopped_and_set_aside_not_deleted(self):
        self.retire({"ownership": "proven"})
        command = self.commands[0]
        self.assertIn("systemctl --user stop sandbox-mcp-remote.service", command)
        self.assertIn("mv -f $HOME/.config/systemd/user/sandbox-mcp-remote.service "
                      "$HOME/.sandbox/removed-units/sandbox-mcp-remote.service.", command)
        self.assertIn("umask 077", command)
        self.assertNotIn("rm ", command)
        self.assertIn("systemctl --user disable sandbox-mcp-remote.service && ", command)
        self.assertNotIn("|| true", command)
        self.assertEqual(self.probes[0]["control_transport"], "https")
        self.assertRegex(self.result, r"removed-units/sandbox-mcp-remote\.service\.\d{8}T\d{6}Z$")

    def test_a_unit_sandbox_already_set_aside_needs_nothing(self):
        backup = "/home/user/.sandbox/removed-units/sandbox-mcp-remote.service.20261110T041500Z"
        self.retire({"ownership": "missing"}, backups=backup + "\n")
        self.assertEqual(self.result, backup)
        self.assertFalse(any("systemctl" in item for item in self.commands))

    def test_a_missing_unit_without_a_sandbox_backup_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unit_missing"):
            self.retire({"ownership": "missing"}, backups="")

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



class ConfirmedSwitchTests(unittest.TestCase):
    def provision(self, *, tailscale_ip="100.64.0.9", entry=HTTPS, start_error=None):
        import contextlib, io
        args = types.SimpleNamespace(name="box", control="tailscale", confirm=True, force=False,
                                     front_door=None, json=True, yes=False, upload_timeout=None)
        self.retire = unittest.mock.Mock(return_value="$HOME/.sandbox/removed-units/sandbox-mcp-remote.service.20261110T041500Z")
        self.restore = unittest.mock.Mock(return_value=True)
        self.start = unittest.mock.Mock(side_effect=start_error, return_value={"service": "sandbox-mcp-remote.service"})
        self.put = unittest.mock.Mock(side_effect=lambda name, **fields: {**entry, **fields})
        self.mint = unittest.mock.Mock(return_value="new-token-sentinel")
        resolve = unittest.mock.Mock(side_effect=RuntimeError("no Tailscale IP")
                                     if tailscale_ip is None else None, return_value=tailscale_ip)
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for target, value in (
                    ("get_remote", unittest.mock.Mock(return_value=dict(entry))),
                    ("put_remote", self.put), ("mint_bearer_token", self.mint),
                    ("resolve_tailscale_ip", resolve), ("start_remote_mcp_server", self.start)):
                stack.enter_context(patch.object(remote_cmd.sr, target, value))
            for target, value in (
                    ("_checked_front_door", unittest.mock.Mock(return_value=None)),
                    ("_front_door_provision_preflight", unittest.mock.Mock()),
                    ("_runtime_source_upload_timeout_arg", unittest.mock.Mock(return_value=300)),
                    ("_local_git_revision", unittest.mock.Mock(return_value="a" * 40)),
                    ("_assert_clean_source_revision", unittest.mock.Mock()),
                    ("_read_exact_source_file", unittest.mock.Mock(return_value=b"true")),
                    ("_upload_runtime_source", unittest.mock.Mock(return_value={})),
                    ("_new_provision_log", unittest.mock.Mock(return_value=None)),
                    ("_record_provision_event", unittest.mock.Mock()),
                    ("_provision_log_summary", unittest.mock.Mock(return_value=None)),
                    ("_network_range_hint", unittest.mock.Mock(return_value={})),
                    ("_invalidate_readiness", unittest.mock.Mock()),
                    ("_retire_https_control_unit", self.retire),
                    ("_restore_https_control_unit", self.restore)):
                stack.enter_context(patch.object(remote_cmd, target, value))
            stack.enter_context(patch.object(remote_cmd.subprocess, "run",
                                             return_value=types.SimpleNamespace(returncode=0, stdout="", stderr="")))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            try:
                remote_cmd._cmd_provision(args, True)
                return 0, out.getvalue()
            except SystemExit as exc:
                return exc.code, out.getvalue()

    def test_https_control_is_not_stopped_until_tailscale_is_joined(self):
        code, _ = self.provision(tailscale_ip=None)
        self.assertNotEqual(code, 0)
        self.retire.assert_not_called()
        self.start.assert_not_called()

    def test_the_switch_keeps_the_recorded_token(self):
        code, output = self.provision()
        self.assertEqual(code, 0)
        self.retire.assert_called_once()
        self.assertEqual(self.start.call_args.args[3], "kept-token-sentinel")
        self.mint.assert_not_called()
        self.assertEqual(self.put.call_args.kwargs["control_transport"], "tailscale")
        self.assertNotIn("kept-token-sentinel", output)

    def test_a_failed_start_puts_the_https_service_back(self):
        code, _ = self.provision(start_error=RuntimeError("unit failed"))
        self.assertNotEqual(code, 0)
        self.restore.assert_called_once()
        self.assertEqual(self.restore.call_args.args[1], self.retire.return_value)
        self.put.assert_not_called()

    def test_a_fresh_provision_still_mints_a_token(self):
        code, _ = self.provision(entry={"ssh": "user@203.0.113.10", "provisioned": False})
        self.assertEqual(code, 0)
        self.retire.assert_not_called()
        self.mint.assert_called_once()


class RestoreUnitTests(unittest.TestCase):
    def test_only_a_sandbox_backup_path_is_ever_moved(self):
        calls = []
        ssh = lambda _e, command, timeout=30, **_k: calls.append(command) or types.SimpleNamespace(returncode=0)
        with patch.object(remote_cmd.sr, "ssh_run", side_effect=ssh):
            good = "$HOME/.sandbox/removed-units/sandbox-mcp-remote.service.20261110T041500Z"
            self.assertTrue(remote_cmd._restore_https_control_unit(dict(HTTPS), good))
            for bad in ("/etc/passwd", good + "; rm -rf ~", "", None):
                self.assertFalse(remote_cmd._restore_https_control_unit(dict(HTTPS), bad))
        self.assertEqual(len(calls), 1)
        self.assertIn("systemctl --user enable --now sandbox-mcp-remote.service", calls[0])
