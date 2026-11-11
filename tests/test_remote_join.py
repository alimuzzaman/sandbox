import contextlib
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

import sandbox.core._remote as sr
from sandbox.commands import remote as remote_command
from tests.host_memory_fixtures import status_state

TAILNET = "100.64.0.9"
INVENTORY = {"ok": True, "inventory_schema": 1, "transport": "control"}
OWNER = {"ssh": "user@203.0.113.10", "control_transport": "tailscale", "provisioned": True,
         "control_url": f"http://{TAILNET}:9174", "tailscale_host": TAILNET, "mcp_port": 9174,
         "bearer_token": "owner-token-sentinel"}


def run(handler, args, patches):
    out = io.StringIO()
    with contextlib.ExitStack() as stack:
        for target, value in patches.items():
            stack.enter_context(mock.patch.object(*target, value) if isinstance(target, tuple)
                                else mock.patch(target, value))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        try:
            handler(args, True)
            code = 0
        except SystemExit as exc:
            code = exc.code
    text = out.getvalue().strip()
    return code, (json.loads(text.splitlines()[-1]) if text else None)


class AuthHeadersTest(unittest.TestCase):
    def test_a_token_is_sent_as_the_bearer(self):
        self.assertEqual(sr.control_auth_headers(OWNER), {"Authorization": "Bearer owner-token-sentinel"})

    def test_a_joined_tailnet_registration_sends_no_credential(self):
        joined = {**OWNER, "control_access": "tailnet"}
        del joined["bearer_token"]
        self.assertEqual(sr.control_auth_headers(joined), {})
        self.assertTrue(sr.control_ready(joined))

    def test_tailnet_access_needs_a_tailscale_control_connection(self):
        entry = {"control_url": "https://example.test", "control_transport": "https",
                 "control_access": "tailnet"}
        self.assertIsNone(sr.control_auth_headers(entry))
        self.assertFalse(sr.control_ready(entry))

    def test_a_refusal_is_reported_as_its_http_status(self):
        import urllib.error
        refused = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
        with mock.patch.object(sr, "_control_urlopen", side_effect=refused), \
                self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            sr._remote_control_request(OWNER, "/inventory")


class JoinTest(unittest.TestCase):
    def join(self, target=f"user@{TAILNET}", *, existing=None, control=None, ssh=None, resolve=None):
        args = SimpleNamespace(name="box", ssh_url=target)
        self.put = mock.Mock(side_effect=lambda name, **fields: fields)
        self.control = control or mock.Mock(return_value=dict(INVENTORY))
        self.ssh = ssh or mock.Mock(return_value=SimpleNamespace(returncode=0, stderr=""))
        return run(remote_command._cmd_join, args, {
            (sr, "get_remote"): mock.Mock(return_value=existing),
            (sr, "put_remote"): self.put,
            (sr, "_remote_control_request"): self.control,
            (sr, "remote_host_memory_request"): mock.Mock(return_value={
                "resource_schema": 1, "host_memory_schema": 1, "transport": "control",
                "service": {"ownership_marker": "a" * 24, "runtime_revision": "b" * 24},
                "result": status_state(target_identity="c" * 24)}),
            (remote_command.subprocess, "run"): self.ssh,
            (remote_command.socket, "getaddrinfo"): resolve or mock.Mock(
                return_value=[(2, 1, 6, "", (TAILNET, 0))]),
        })

    def test_it_registers_the_server_with_no_token(self):
        code, result = self.join()
        self.assertEqual((code, result["status"], result["ssh_login"]), (0, "joined", "ok"))
        fields = self.put.call_args.kwargs
        self.assertEqual(fields["control_url"], f"http://{TAILNET}:9174")
        self.assertEqual(fields["tailscale_host"], TAILNET)
        self.assertEqual((fields["control_access"], fields["joined"], fields["provisioned"]),
                         ("tailnet", True, True))
        self.assertNotIn("bearer_token", fields)
        self.assertEqual(fields["ssh"], f"user@{TAILNET}")

    def test_a_magicdns_name_is_resolved_to_its_tailnet_address(self):
        code, _ = self.join("user@box.example.ts.net")
        self.assertEqual(code, 0)
        self.assertEqual(self.put.call_args.kwargs["tailscale_host"], TAILNET)

    def test_an_address_outside_the_tailnet_is_refused(self):
        for target, resolve in (("user@203.0.113.10", None),
                                ("user@public.example.test",
                                 mock.Mock(return_value=[(2, 1, 6, "", ("203.0.113.10", 0))])),
                                ("user@missing.example.test", mock.Mock(side_effect=OSError("nx")))):
            with self.subTest(target=target):
                code, _ = self.join(target, resolve=resolve)
                self.assertNotEqual(code, 0)
                self.put.assert_not_called()

    def test_a_user_is_required(self):
        self.assertNotEqual(self.join(TAILNET)[0], 0)
        self.put.assert_not_called()

    def test_nothing_is_registered_when_the_service_does_not_admit_this_machine(self):
        code, _ = self.join(control=mock.Mock(side_effect=RuntimeError("remote control returned HTTP 401")))
        self.assertNotEqual(code, 0)
        self.put.assert_not_called()
        self.ssh.assert_not_called()

    def test_an_existing_registration_is_never_overwritten(self):
        code, _ = self.join(existing=dict(OWNER))
        self.assertNotEqual(code, 0)
        self.put.assert_not_called()
        self.control.assert_not_called()

    def test_the_first_ssh_login_records_the_host_key_without_prompting(self):
        self.join()
        argv = self.ssh.call_args.args[0]
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("StrictHostKeyChecking=accept-new", argv)
        self.assertEqual(argv[-2:], [f"user@{TAILNET}", "true"])

    def test_a_refused_ssh_login_still_joins_and_says_what_is_missing(self):
        refused = mock.Mock(return_value=SimpleNamespace(
            returncode=255, stderr="tailscale: tailnet policy does not permit you to SSH\n"))
        code, result = self.join(ssh=refused)
        self.assertEqual((code, result["status"], result["ssh_login"]), (0, "joined", "denied"))
        self.put.assert_called_once()

    def test_an_unclear_ssh_result_is_unknown(self):
        for ssh in (mock.Mock(return_value=SimpleNamespace(returncode=255, stderr="Connection timed out")),
                    mock.Mock(side_effect=OSError("no ssh"))):
            with self.subTest(ssh=ssh):
                self.assertEqual(self.join(ssh=ssh)[1]["ssh_login"], "unknown")


class ProvisionGuardTest(unittest.TestCase):
    def test_provision_refuses_a_joined_server_without_force(self):
        joined = {**OWNER, "joined": True}
        args = SimpleNamespace(name="box", confirm=True, force=False)
        code, _ = run(remote_command._cmd_provision, args, {(sr, "get_remote"): mock.Mock(return_value=joined),
                                                              (remote_command, "_choose_control_transport"):
                                                                  mock.Mock(side_effect=AssertionError("reached"))})
        self.assertNotEqual(code, 0)
        self.assertNotEqual(code, None)


class TailnetAccessCommandTest(unittest.TestCase):
    def command(self, operation, *, setting=False, confirm=False, entry=None, probe="refused",
                write_rc=0):
        self.state = {"setting": setting}
        commands = []

        def ssh_run(_entry, command, timeout=30, **_kwargs):
            commands.append(command)
            if command.startswith("test ! -e "):
                if self.state["setting"] is None:
                    return SimpleNamespace(returncode=1, stdout="", stderr="")
                if self.state["setting"] == "absent":
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return SimpleNamespace(returncode=0, stdout=json.dumps({"tailnet_members": self.state["setting"]}),
                                       stderr="")
            if write_rc == 0:
                self.state["setting"] = '"tailnet_members": true' in command
            return SimpleNamespace(returncode=write_rc, stdout="", stderr="")

        self.commands = commands
        args = SimpleNamespace(name=operation, ssh_url="box", confirm=confirm)
        control = mock.Mock(side_effect=RuntimeError("remote control returned HTTP 401")
                            if probe == "refused" else None, return_value=dict(INVENTORY))
        self.control = control
        return run(remote_command._cmd_tailnet_access, args, {
            (sr, "get_remote"): mock.Mock(return_value=entry if entry is not None else dict(OWNER)),
            (sr, "ssh_run"): ssh_run,
            (sr, "resolve_sandbox_home"): mock.Mock(return_value="/home/user/sandbox"),
            (sr, "_remote_control_request"): control,
        })

    def writes(self):
        return [item for item in self.commands if not item.startswith("test ! -e ")]

    def test_no_file_means_on(self):
        code, result = self.command("status", setting="absent", probe="admitted")
        self.assertEqual((code, result["setting"], result["members"]), (0, True, "admitted"))
        code, result = self.command("enable", setting="absent", confirm=True, probe="admitted")
        self.assertEqual((code, result["status"]), (0, "already_enabled"))
        self.assertEqual(self.writes(), [])

    def test_status_reads_the_setting_and_what_the_service_does(self):
        code, result = self.command("status", setting=True, probe="admitted")
        self.assertEqual((code, result["setting"], result["members"]), (0, True, "admitted"))
        self.assertEqual(self.writes(), [])

    def test_the_probe_sends_no_token(self):
        self.command("status")
        probed = self.control.call_args.args[0]
        self.assertNotIn("bearer_token", probed)
        self.assertEqual(sr.control_auth_headers(probed), {})

    def test_enable_without_confirm_only_plans(self):
        code, result = self.command("enable")
        self.assertEqual((code, result["status"]), (0, "planned"))
        self.assertEqual(self.writes(), [])

    def test_enable_writes_a_private_file_atomically(self):
        code, result = self.command("enable", confirm=True, probe="admitted")
        self.assertEqual((code, result["status"], result["setting"], result["members"]),
                         (0, "enabled", True, "admitted"))
        write = self.writes()[0]
        self.assertIn("umask 077", write)
        self.assertIn("mv -f", write)
        self.assertIn("/home/user/sandbox/runtime/control-access.json", write)

    def test_an_older_service_that_still_refuses_is_reported(self):
        code, result = self.command("enable", confirm=True, probe="refused")
        self.assertEqual((code, result["members"]), (0, "refused"))
        self.assertIn("migrate", result["next_step"])

    def test_disable_turns_it_off(self):
        code, result = self.command("disable", setting=True, confirm=True)
        self.assertEqual((code, result["status"], result["setting"]), (0, "disabled", False))

    def test_already_in_place_writes_nothing(self):
        code, result = self.command("enable", setting=True, confirm=True, probe="admitted")
        self.assertEqual((code, result["status"]), (0, "already_enabled"))
        self.assertEqual(self.writes(), [])

    def test_a_failed_write_is_an_error(self):
        code, _ = self.command("enable", confirm=True, write_rc=1)
        self.assertNotEqual(code, 0)

    def test_only_the_owning_controller_may_change_it(self):
        joined = {**OWNER, "control_access": "tailnet", "joined": True}
        del joined["bearer_token"]
        code, _ = self.command("enable", confirm=True, entry=joined)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.writes(), [])

    def test_a_public_https_remote_is_refused(self):
        code, _ = self.command("enable", confirm=True, entry={**OWNER, "control_transport": "https"})
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
