"""Feedback 3234ad2b: `host login-url` for a target a joined server records.

The link is minted on the server and reaches only the caller's terminal: never
argv, a durable job, a state file or the server-side commands.
"""
from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import sandbox.core._hosting as hosting
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import server_cli_dispatch, server_login_url as slu
from sandbox.hosting.target_resolution import ResolvedTarget

MARKER, IDENTITY = "a" * 24, "b" * 24
ENTRY = {"provisioned": True, "joined": True, "control_transport": "tailscale",
         "tailscale_host": "100.64.0.7", "ssh": "owner@100.64.0.7",
         "mcp_service": {"ownership_marker": MARKER, "runtime_revision": "c" * 24},
         "control_identity": {"schema_version": 1, "target_identity": IDENTITY}}
URL = "https://wp-cfe-expiry-1111.xspeedcdn.online/wp-login.php?sandbox_autologin=1#tok-en_123"
VALIDATED = {
    "project": "xspeed-hub-cfe-expiry-1111", "environment": "sandbox",
    "routes": [{"hostname": "wp-cfe-expiry-1111.xspeedcdn.online", "primary": True}],
    "compose": {"service": "web", "files": ["compose.yml"]},
    "autologin": {"user": "xsadmin", "service": "wordpress", "ttl_seconds": 900,
                  "request_path": "/wp-login.php",
                  "container_path": "/var/www/html/wp-content/mu-plugins/99-sandbox-host-autologin.php"},
}


def _args(**values) -> SimpleNamespace:
    base = dict(action="login-url", remote="xspeed-dev", name="cfe-expiry-1111", target=None,
                environment=None, ttl_seconds=None, confirm=True, json=False, here=False,
                service=None, email=None, save_email=False, list_links=False, revoke=None,
                uses=None, project_dir=None)
    base.update(values)
    return SimpleNamespace(**base)


def _reply(**values) -> str:
    payload = {"ok": True, "url": URL, "expires_at": 1_900_000_000, "one_time": True, "user": "xsadmin",
               "contract": slu.CONTRACT, "target": "xspeed-hub-cfe-expiry-1111/sandbox",
               "name": "cfe-expiry-1111", "target_identity": IDENTITY, "ownership_marker": MARKER}
    payload.update(values)
    return json.dumps(payload) + "\n"


def _done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["ssh"], code, stdout=stdout, stderr=stderr)


class ControllerTests(unittest.TestCase):
    def _cmd_host(self, args, run):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(hosting_cmd.remote, "get_remote", return_value=ENTRY), \
                patch.object(hosting_cmd.remote, "ssh_run", side_effect=run), \
                patch.object(slu.time, "sleep"), \
                patch("sandbox.hosting.target_resolution.resolve") as controller_resolve, \
                patch.object(hosting_cmd, "_issue_host_autologin") as controller_mint, \
                patch.dict("os.environ", {}, clear=False) as environ, \
                redirect_stdout(out), redirect_stderr(err):
            environ.pop(slu.JOB_ENV, None)
            code = 0
            try:
                hosting_cmd.cmd_host({}, args)
            except SystemExit as exc:
                code = exc.code
        controller_resolve.assert_not_called()
        controller_mint.assert_not_called()
        return code, out.getvalue(), err.getvalue()

    def test_named_stack_link_is_minted_on_the_server_and_printed_only_here(self):
        calls = []

        def run(entry, command, timeout):
            calls.append(command)
            return _done(_reply())
        code, out, err = self._cmd_host(_args(), run)
        self.assertEqual((code, out, err), (0, URL + "\n", ""))
        self.assertEqual(len(calls), 1, "one control call; no separate home lookup")
        self.assertEqual(calls[0], '"${SANDBOX_HOME:-$HOME/sandbox}/sb-src/sb" host login-url --here '
                         "--remote xspeed-dev --name cfe-expiry-1111 --confirm --json")

    def test_target_selector_and_ttl_reach_the_server_and_json_returns_the_link(self):
        calls = []

        def run(entry, command, timeout):
            calls.append(command)
            return _done(_reply())
        code, out, _ = self._cmd_host(
            _args(name=None, target="xspeed-hub-cfe-expiry-1111/sandbox", ttl_seconds=900, json=True), run)
        self.assertEqual(code, 0)
        self.assertIn("--target xspeed-hub-cfe-expiry-1111/sandbox --ttl-seconds 900", calls[0])
        payload = json.loads(out)
        self.assertEqual((payload["url"], payload["user"]), (URL, "xsadmin"))
        self.assertNotIn("target_identity", payload)

    def test_connection_that_never_opened_is_retried_then_the_link_shown(self):
        replies = [_done(stderr="ssh: connect to host 100.64.0.7 port 22: Operation timed out\n", code=255),
                   _done(_reply())]
        code, out, _ = self._cmd_host(_args(), lambda entry, command, timeout: replies.pop(0))
        self.assertEqual((code, out), (0, URL + "\n"))

    def test_timeout_after_connecting_is_never_retried(self):
        calls = []

        def run(entry, command, timeout):
            calls.append(command)
            raise subprocess.TimeoutExpired("ssh", timeout)
        code, out, err = self._cmd_host(_args(), run)
        self.assertEqual((code, out, len(calls)), (1, "", 1))
        self.assertIn(slu.UNAVAILABLE, err)

    def test_three_failed_connections_refuse_without_issuing(self):
        calls = []

        def run(entry, command, timeout):
            calls.append(command)
            return _done(stderr="ssh: connect to host 100.64.0.7 port 22: Connection timed out\n", code=255)
        code, out, err = self._cmd_host(_args(), run)
        self.assertEqual((code, out, len(calls)), (1, "", slu.CONNECT_ATTEMPTS))
        self.assertIn("nothing was issued", err)

    def test_durable_job_and_missing_confirm_refuse_before_any_connection(self):
        for args, environ, code in ((_args(), {slu.JOB_ENV: "f" * 32}, slu.JOB_REFUSED),
                                    (_args(confirm=False), {}, "confirmation_required"),
                                    (_args(service="mail"), {}, slu.UNSUPPORTED)):
            with self.subTest(code=code), patch.dict("os.environ", {}) as env, \
                    patch.object(hosting_cmd.remote, "get_remote", return_value=ENTRY), \
                    patch.object(hosting_cmd.remote, "ssh_run") as ssh, \
                    redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                env.pop(slu.JOB_ENV, None)
                env.update(environ)
                with self.assertRaises(SystemExit):
                    hosting_cmd.cmd_host({}, args)
                ssh.assert_not_called()
                self.assertEqual(out.getvalue(), "")
                self.assertIn(code, err.getvalue())

    def test_reply_from_another_owner_is_not_shown(self):
        code, out, err = self._cmd_host(
            _args(), lambda entry, command, timeout: _done(_reply(ownership_marker="d" * 24)))
        self.assertEqual((code, out), (1, ""))
        self.assertNotIn("tok-en_123", err)

    def test_server_refusal_keeps_its_code(self):
        refusal = json.dumps({"ok": False, "code": "host_target_unknown", "error": "no stack named 'x'"})
        code, out, err = self._cmd_host(_args(), lambda entry, command, timeout: _done(refusal, code=1))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("host_target_unknown", err)

    def test_runtime_without_the_issuer_says_to_migrate(self):
        code, _, err = self._cmd_host(_args(), lambda entry, command, timeout: _done(
            stderr="sb: error: unrecognized arguments: --here\n", code=2))
        self.assertEqual(code, 1)
        self.assertIn("sb remote service migrate xspeed-dev --confirm", err)

    def test_an_ordinary_remote_keeps_the_controller_path(self):
        self.assertFalse(slu.selected(_args(), {"provisioned": True}))
        self.assertFalse(slu.selected(_args(name=None, project_dir="."), ENTRY))
        self.assertTrue(slu.selected(_args(), ENTRY))


class ServerTests(unittest.TestCase):
    owner = SimpleNamespace(sandbox_home="/home/owner/sandbox", target_identity=IDENTITY,
                            ownership_marker=MARKER)
    resolved = ResolvedTarget(remote="xspeed-dev", project="xspeed-hub-cfe-expiry-1111",
                              environment="sandbox", kind="stack",
                              project_dir="/home/owner/sandbox/runtime/stacks/cfe-expiry-1111",
                              slug="cfe-expiry-1111")

    def _issue(self, args, *, validated=VALIDATED, environ=None):
        commands, saved = [], []
        state = {"hosts": {"xspeed-dev/xspeed-hub-cfe-expiry-1111/sandbox": {"commit": "e" * 40}}}

        def lock(validated_, remote_name, callback):
            self.assertEqual(remote_name, "xspeed-dev")
            return callback(state, lambda value: saved.append(json.dumps(value)))
        with patch("sandbox.hosting.target_resolution.resolve", return_value=self.resolved) as resolve, \
                patch.object(hosting, "validate_manifest", return_value=dict(validated)), \
                patch.object(hosting_cmd, "_with_host_writer_lock", side_effect=lock), \
                patch.dict("os.environ", {}) as env:
            env.pop(slu.JOB_ENV, None)
            env.update(environ or {})
            result = slu.issue_here(args, owner=self.owner,
                                    runner=lambda command, timeout: commands.append(command) or "")
        return result, commands, saved, resolve

    def test_server_mints_records_and_returns_the_link_with_its_owner_identity(self):
        result, commands, saved, resolve = self._issue(_args(here=True, ttl_seconds=900))
        resolve.assert_called_once_with("xspeed-dev", target=None, name="cfe-expiry-1111", environment=None)
        token = result["url"].split("#", 1)[1]
        self.assertTrue(result["url"].startswith(
            "https://wp-cfe-expiry-1111.xspeedcdn.online/wp-login.php?sandbox_autologin=1#"))
        self.assertEqual((result["user"], result["contract"], result["target_identity"]),
                         ("xsadmin", slu.CONTRACT, IDENTITY))
        self.assertEqual(len(commands), 3)
        self.assertTrue(all("/home/owner/sandbox/deploy-src/hosts/xspeed-hub-cfe-expiry-1111" in command
                            for command in commands))
        for written in (*commands, *saved):
            self.assertNotIn(token, written)
        self.assertEqual(json.loads(saved[-1])["hosts"]["xspeed-dev/xspeed-hub-cfe-expiry-1111/sandbox"]
                         ["autologin"]["user"], "xsadmin")

    def test_server_refuses_inside_a_durable_job(self):
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True), environ={slu.JOB_ENV: "f" * 32})
        self.assertEqual(caught.exception.code, slu.JOB_REFUSED)

    def test_target_without_wordpress_autologin_is_refused(self):
        validated = {key: value for key, value in VALIDATED.items() if key != "autologin"}
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True), validated=validated)
        self.assertEqual(caught.exception.code, slu.NOT_DECLARED)

    def test_here_runs_before_configuration_and_prints_one_json_line(self):
        out = io.StringIO()
        with patch.object(slu, "issue_here", return_value={"url": URL}), redirect_stdout(out):
            self.assertTrue(server_cli_dispatch.dispatch_host_if_selected(_args(here=True)))
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "url": URL})

    def test_here_refusal_is_one_json_line_without_a_link(self):
        out = io.StringIO()
        with patch.object(slu, "issue_here", side_effect=hosting.HostingRefusal("host_target_busy", "busy")), \
                redirect_stdout(out), self.assertRaises(SystemExit):
            slu.run_here(_args(here=True))
        self.assertEqual(json.loads(out.getvalue())["code"], "host_target_busy")


if __name__ == "__main__":
    unittest.main()
