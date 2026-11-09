import contextlib
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from sandbox.commands import remote as remote_command

ENTRY = {"ssh": "user@203.0.113.10", "provisioned": True}


class RemoteHost:
    def __init__(self, enabled=False, prefs_rc=0, set_rc=0, stuck=False):
        self.enabled, self.prefs_rc, self.set_rc, self.stuck = enabled, prefs_rc, set_rc, stuck
        self.commands = []

    def __call__(self, _entry, command, timeout=30, **_kwargs):
        self.commands.append(command)
        if command == "tailscale debug prefs":
            return SimpleNamespace(returncode=self.prefs_rc, stdout=json.dumps({"RunSSH": self.enabled}), stderr="")
        if command == "tailscale ip -4":
            return SimpleNamespace(returncode=0, stdout="100.64.0.9\n", stderr="")
        if command.startswith("sudo -n tailscale set --ssh="):
            if self.set_rc == 0 and not self.stuck:
                self.enabled = command.endswith("=true")
            return SimpleNamespace(returncode=self.set_rc, stdout="", stderr="")
        raise AssertionError(command)


class TailscaleSshTest(unittest.TestCase):
    def run_command(self, operation, host, *, confirm=False, login=None, entry=ENTRY):
        args = SimpleNamespace(name=operation, ssh_url="box", confirm=confirm, json=True)
        out = io.StringIO()
        login = login or mock.Mock(return_value=SimpleNamespace(returncode=0))
        with mock.patch.object(remote_command.sr, "get_remote", return_value=entry), \
                mock.patch.object(remote_command.sr, "ssh_run", side_effect=host), \
                mock.patch.object(remote_command.sr, "put_remote") as put, \
                mock.patch.object(remote_command.subprocess, "run", login), \
                contextlib.redirect_stdout(out):
            try:
                remote_command._cmd_tailscale_ssh(args, True)
                code = 0
            except SystemExit as exc:
                code = exc.code
        put.assert_not_called()
        self.login = login
        return code, (json.loads(out.getvalue()) if out.getvalue().strip() else None)

    def changes(self, host):
        return [item for item in host.commands if item.startswith("sudo")]

    def test_status_reads_without_changing(self):
        host = RemoteHost(enabled=True)
        code, result = self.run_command("status", host)
        self.assertEqual((code, result["status"], result["enabled"]), (0, "observed", True))
        self.assertEqual(self.changes(host), [])

    def test_enable_without_confirm_only_plans(self):
        host = RemoteHost()
        code, result = self.run_command("enable", host)
        self.assertEqual((code, result["status"]), (0, "planned"))
        self.assertEqual(self.changes(host), [])
        self.login.assert_not_called()

    def test_enable_turns_it_on_and_proves_a_non_interactive_login(self):
        host = RemoteHost()
        code, result = self.run_command("enable", host, confirm=True)
        self.assertEqual((code, result["status"], result["login"]), (0, "enabled", "ok"))
        self.assertEqual(self.changes(host), ["sudo -n tailscale set --ssh=true"])
        argv = self.login.call_args.args[0]
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-2:], ["user@100.64.0.9", "true"])

    def test_a_denied_login_reports_the_rule_to_add(self):
        host = RemoteHost()
        code, result = self.run_command(
            "enable", host, confirm=True, login=mock.Mock(return_value=SimpleNamespace(returncode=255)))
        self.assertEqual((code, result["status"], result["login"]), (0, "enabled", "denied"))
        self.assertEqual(result["required_rule"]["action"], "accept")
        self.assertEqual(result["required_rule"]["users"], ["user"])

    def test_a_denied_login_is_explained_in_plain_output(self):
        args = SimpleNamespace(name="enable", ssh_url="box", confirm=True)
        out = io.StringIO()
        with mock.patch.object(remote_command.sr, "get_remote", return_value=ENTRY), \
                mock.patch.object(remote_command.sr, "ssh_run", side_effect=RemoteHost()), \
                mock.patch.object(remote_command.subprocess, "run",
                                  mock.Mock(return_value=SimpleNamespace(returncode=255))), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            remote_command._cmd_tailscale_ssh(args, False)
        self.assertIn("could not log in", out.getvalue())

    def test_a_login_that_waits_for_a_browser_counts_as_denied(self):
        host = RemoteHost()
        _, result = self.run_command(
            "enable", host, confirm=True,
            login=mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 30)))
        self.assertEqual(result["login"], "denied")

    def test_disable_turns_it_off_without_a_login_check(self):
        host = RemoteHost(enabled=True)
        code, result = self.run_command("disable", host, confirm=True)
        self.assertEqual((code, result["status"]), (0, "disabled"))
        self.assertEqual(self.changes(host), ["sudo -n tailscale set --ssh=false"])
        self.login.assert_not_called()

    def test_disabling_when_off_changes_nothing(self):
        host = RemoteHost()
        code, result = self.run_command("disable", host, confirm=True)
        self.assertEqual((code, result["status"]), (0, "already_disabled"))
        self.assertEqual(self.changes(host), [])

    def test_a_change_that_did_not_take_is_an_error(self):
        code, _ = self.run_command("enable", RemoteHost(stuck=True), confirm=True)
        self.assertNotEqual(code, 0)

    def test_a_refused_sudo_is_an_error(self):
        code, _ = self.run_command("enable", RemoteHost(set_rc=1), confirm=True)
        self.assertNotEqual(code, 0)

    def test_a_host_without_tailscale_is_an_error(self):
        host = RemoteHost(prefs_rc=127)
        code, _ = self.run_command("enable", host, confirm=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.changes(host), [])

    def test_unknown_operation_and_remote_are_refused(self):
        self.assertNotEqual(self.run_command("restart", RemoteHost())[0], 0)
        self.assertNotEqual(self.run_command("status", RemoteHost(), entry=None)[0], 0)


if __name__ == "__main__":
    unittest.main()
