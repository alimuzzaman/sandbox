"""Feedback 3234ad2b: `host login-url` for a target a joined server records.

The token is made on the controller and never leaves it: the server gets only
its SHA-256 and answers with the link minus its ``#token`` fragment. The link
never reaches argv, a durable job, a state file or the server-side commands.
"""
from __future__ import annotations

import hashlib
import io
import json
import shlex
import subprocess
import sys
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import sandbox.core._hosting as hosting
from sandbox import cli
from sandbox.application.server_cli import ServerCLIUnavailable
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import server_cli_dispatch, stack_registry, server_login_url as slu
from sandbox.hosting.target_resolution import ResolvedTarget

MARKER, IDENTITY = "a" * 24, "b" * 24
HASH = "f" * 64
ENTRY = {"provisioned": True, "joined": True, "control_transport": "tailscale",
         "tailscale_host": "100.64.0.7", "ssh": "owner@100.64.0.7",
         "mcp_service": {"ownership_marker": MARKER, "runtime_revision": "c" * 24},
         "control_identity": {"schema_version": 1, "target_identity": IDENTITY}}
BASE = "https://wp-cfe-expiry-1111.xspeedcdn.online/wp-login.php?sandbox_autologin=1"
VALIDATED = {
    "project": "xspeed-hub-cfe-expiry-1111", "environment": "sandbox",
    "routes": [{"hostname": "wp-cfe-expiry-1111.xspeedcdn.online", "primary": True}],
    "compose": {"service": "web", "files": ["compose.yml"]},
    "autologin": {"user": "xsadmin", "service": "wordpress", "ttl_seconds": 900,
                  "request_path": "/wp-login.php",
                  "container_path": "/var/www/html/wp-content/mu-plugins/99-sandbox-host-autologin.php"},
}
# What `job-capabilities --json` prints on a 25 runtime (5c536d5): the same
# identity, no login-url contract.
CAPS_25 = {"schema_version": 1, "ok": True, "target_identity": IDENTITY, "ownership_marker": MARKER,
           "runtime_revision": "c" * 24, "executable": "/home/owner/sandbox/sb-src/sb", "uid": 1000,
           "target_metadata": {"tailscale_host": "100.64.0.7"}, "inspection_capabilities": [],
           "source_repair_capabilities": [], "server_exec_capabilities": ["job.server-exec.v1"],
           "server_exec_capture_capabilities": [], "server_cli_apply_capabilities": [],
           "capabilities": ["job.exec", "job.execution-policy.v1", "job.acceptance-receipt.v1",
                            "job.server-cli.v1", "job.source-content.v1"]}
CAPS = {**CAPS_25, "host_login_url_capabilities": [slu.CONTRACT]}


def _args(**values) -> SimpleNamespace:
    base = dict(action="login-url", remote="xspeed-dev", name="cfe-expiry-1111", target=None,
                environment=None, ttl_seconds=None, confirm=True, json=False, here=False,
                service=None, email=None, save_email=False, list_links=False, revoke=None,
                uses=None, project_dir=None, token_sha256=None)
    base.update(values)
    return SimpleNamespace(**base)


def _done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["ssh"], code, stdout=stdout, stderr=stderr)


def _sent_hash(command: str) -> str:
    argv = shlex.split(command.split(" ", 1)[1])
    return argv[argv.index("--token-sha256") + 1]


def _mint_reply(command: str, **values) -> str:
    payload = {"ok": True, "login_base": BASE, "expires_at": 1_900_000_000, "one_time": True,
               "user": "xsadmin", "contract": slu.CONTRACT, "target": "xspeed-hub-cfe-expiry-1111/sandbox",
               "name": "cfe-expiry-1111", "token_sha256": _sent_hash(command),
               "target_identity": IDENTITY, "ownership_marker": MARKER}
    payload.update(values)
    return json.dumps(payload) + "\n"


def _server(calls, *, caps=CAPS, mint=None):
    """A fake owner: the capability probe, then the mint."""
    def run(entry, command, timeout):
        calls.append(command)
        if command == slu.PROBE_COMMAND:
            return _done(json.dumps(caps) + "\n")
        return mint(command) if mint else _done(_mint_reply(command))
    return run


class ControllerTests(unittest.TestCase):
    def _cmd_host(self, args, run, entry=ENTRY):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(hosting_cmd.remote, "get_remote", return_value=entry), \
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

    def test_token_stays_here_and_only_its_hash_reaches_the_server(self):
        calls = []
        code, out, err = self._cmd_host(_args(), _server(calls))
        self.assertEqual((code, err), (0, ""))
        url = out.strip()
        self.assertTrue(url.startswith(BASE + "#"))
        token = url.split("#", 1)[1]
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(len(calls), 2, "probe, then one mint; no separate home lookup")
        self.assertEqual(calls[0], '"${SANDBOX_HOME:-$HOME/sandbox}/sb-src/sb" job-capabilities --json')
        sent = _sent_hash(calls[1])
        self.assertEqual(sent, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(calls[1], '"${SANDBOX_HOME:-$HOME/sandbox}/sb-src/sb" host login-url --here '
                         f"--remote xspeed-dev --name cfe-expiry-1111 --token-sha256 {sent} --confirm --json")
        for command in calls:
            self.assertNotIn(token, command)

    def test_target_selector_and_ttl_reach_the_server_and_json_returns_the_link(self):
        calls = []
        code, out, _ = self._cmd_host(
            _args(name=None, target="xspeed-hub-cfe-expiry-1111/sandbox", ttl_seconds=900, json=True),
            _server(calls))
        self.assertEqual(code, 0)
        self.assertIn("--target xspeed-hub-cfe-expiry-1111/sandbox --ttl-seconds 900 --token-sha256", calls[1])
        payload = json.loads(out)
        self.assertTrue(payload["url"].startswith(BASE + "#"))
        self.assertEqual(payload["user"], "xsadmin")
        for key in ("target_identity", "token_sha256", "login_base"):
            self.assertNotIn(key, payload)

    def test_connection_that_never_opened_is_retried_then_the_link_shown(self):
        calls = []
        healthy = _server(calls)
        failures = [_done(stderr="ssh: connect to host 100.64.0.7 port 22: Operation timed out\n", code=255)]

        def run(entry, command, timeout):
            if failures:
                calls.append(command)
                return failures.pop(0)
            return healthy(entry, command, timeout)
        code, out, _ = self._cmd_host(_args(), run)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith(BASE + "#"))
        self.assertEqual(calls[:2], [slu.PROBE_COMMAND, slu.PROBE_COMMAND])

    def test_mint_timeout_is_retried_with_the_same_hash(self):
        calls = []
        attempts = []

        def mint(command):
            attempts.append(command)
            if len(attempts) == 1:
                raise subprocess.TimeoutExpired("ssh", 240)
            return _done(_mint_reply(command))
        code, out, _ = self._cmd_host(_args(), _server(calls, mint=mint))
        self.assertEqual(code, 0)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1], "a retry re-sends the same hash, so it cannot mint twice")
        self.assertEqual(hashlib.sha256(out.strip().split("#", 1)[1].encode()).hexdigest(),
                         _sent_hash(attempts[0]))

    def test_repeated_timeouts_refuse_without_a_link(self):
        calls = []

        def mint(command):
            raise subprocess.TimeoutExpired("ssh", 240)
        code, out, err = self._cmd_host(_args(), _server(calls, mint=mint))
        self.assertEqual((code, out, len(calls)), (1, "", 1 + slu.CONNECT_ATTEMPTS))
        self.assertIn(slu.UNAVAILABLE, err)
        self.assertIn("no link was shown", err)

    def test_three_failed_connections_refuse_without_issuing(self):
        calls = []

        def run(entry, command, timeout):
            calls.append(command)
            return _done(stderr="ssh: connect to host 100.64.0.7 port 22: Connection timed out\n", code=255)
        code, out, err = self._cmd_host(_args(), run)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(calls, [slu.PROBE_COMMAND] * slu.CONNECT_ATTEMPTS, "no mint was ever sent")
        self.assertIn("nothing was issued", err)

    def test_exit_255_after_the_session_opened_is_not_retried(self):
        for result in (_done(stderr="Connection to 100.64.0.7 closed by remote host.\n", code=255),
                       _done(stderr="client_loop: send disconnect: Broken pipe\n", code=255),
                       _done(stdout="partial\n",
                             stderr="ssh: connect to host 100.64.0.7 port 22: Operation timed out\n", code=255)):
            with self.subTest(stderr=result.stderr):
                calls = []
                code, out, err = self._cmd_host(_args(), _server(calls, mint=lambda command: result))
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(len(calls), 2, "probe and one mint attempt only")
                self.assertIn(slu.UNAVAILABLE, err)

    def test_durable_job_and_bad_requests_refuse_before_any_connection(self):
        for args, environ, code in ((_args(), {slu.JOB_ENV: "f" * 32}, slu.JOB_REFUSED),
                                    (_args(confirm=False), {}, "confirmation_required"),
                                    (_args(service="mail"), {}, slu.UNSUPPORTED),
                                    (_args(name=None, target="p/e", project_dir="/checkout"), {},
                                     "host_target_invalid"),
                                    (_args(token_sha256=HASH), {}, "host_target_invalid")):
            with self.subTest(code=code, args=args), patch.dict("os.environ", {}) as env, \
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

    def test_registration_without_identity_or_marker_refuses_before_connecting(self):
        no_identity = {key: value for key, value in ENTRY.items() if key != "control_identity"}
        no_marker = {**ENTRY, "mcp_service": {"runtime_revision": "c" * 24}}
        for entry in (no_identity, no_marker, {**ENTRY, "control_identity": {"schema_version": 1}}):
            with self.subTest(entry=sorted(entry)):
                calls = []
                code, out, err = self._cmd_host(_args(), _server(calls), entry=entry)
                self.assertEqual((code, out, calls), (1, "", []))
                self.assertIn("re-join or refresh", err)

    def test_reply_that_does_not_match_this_request_is_not_shown(self):
        for overrides in ({"ownership_marker": "d" * 24}, {"target_identity": "e" * 24},
                          {"token_sha256": HASH}, {"contract": "host.login-url-here.v0"},
                          {"login_base": BASE + "#planted"}, {"login_base": "http://evil.example/"}):
            with self.subTest(overrides=overrides):
                code, out, _ = self._cmd_host(
                    _args(), _server([], mint=lambda command: _done(_mint_reply(command, **overrides))))
                self.assertEqual((code, out), (1, ""))

    def test_probe_from_another_owner_refuses_before_minting(self):
        calls = []
        code, out, err = self._cmd_host(_args(), _server(calls, caps={**CAPS, "ownership_marker": "d" * 24}))
        self.assertEqual((code, out, calls), (1, "", [slu.PROBE_COMMAND]))
        self.assertIn("registered owner", err)

    def test_server_refusal_keeps_its_code(self):
        refusal = json.dumps({"ok": False, "code": "host_target_unknown", "error": "no stack named 'x'"})
        code, out, err = self._cmd_host(_args(), _server([], mint=lambda command: _done(refusal, code=1)))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("host_target_unknown", err)

    def test_protocol_25_runtime_is_refused_with_migrate_advice_before_any_mint(self):
        calls = []
        code, out, err = self._cmd_host(_args(), _server(calls, caps=CAPS_25))
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(calls, [slu.PROBE_COMMAND], "the 25 runtime never sees a login-url command")
        self.assertIn(slu.UNAVAILABLE, err)
        self.assertIn("sb remote service migrate xspeed-dev --confirm", err)

    def test_an_ordinary_remote_keeps_the_controller_path(self):
        self.assertFalse(slu.selected(_args(), {"provisioned": True}))
        self.assertFalse(slu.selected(_args(name=None, project_dir="."), ENTRY))
        self.assertTrue(slu.selected(_args(), ENTRY))


def _registry(*records) -> dict:
    return {"stacks": {stack_registry.record_key(r["remote"], r["slug"]): r for r in records}}


STACK = {"remote": "xspeed-dev", "slug": "cfe-expiry-1111", "project": "xspeed-hub-cfe-expiry-1111",
         "environment": "sandbox"}


class ServerTests(unittest.TestCase):
    owner = SimpleNamespace(sandbox_home="/home/owner/sandbox", target_identity=IDENTITY,
                            ownership_marker=MARKER)
    resolved = ResolvedTarget(remote="xspeed-dev", project="xspeed-hub-cfe-expiry-1111",
                              environment="sandbox", kind="stack",
                              project_dir="/home/owner/sandbox/runtime/stacks/cfe-expiry-1111",
                              slug="cfe-expiry-1111")

    def _patches(self, stack, *, validated=VALIDATED, environ=None, own_remote=None,
                 registry=None, resolved=None, saved=None):
        state = {"hosts": {"xspeed-dev/xspeed-hub-cfe-expiry-1111/sandbox": {"commit": "e" * 40}}}

        def lock(validated_, remote_name, callback):
            self.assertEqual(remote_name, "xspeed-dev")
            return callback(state, lambda value: (saved if saved is not None else []).append(json.dumps(value)))
        resolve = stack.enter_context(patch("sandbox.hosting.target_resolution.resolve",
                                            return_value=resolved or self.resolved))
        validate = stack.enter_context(patch.object(hosting, "validate_manifest", return_value=dict(validated)))
        stack.enter_context(patch.object(hosting_cmd, "_with_host_writer_lock", side_effect=lock))
        stack.enter_context(patch("sandbox.core._remote.get_remote", return_value=own_remote))
        stack.enter_context(patch.object(stack_registry, "read",
                                         return_value=_registry(STACK) if registry is None else registry))
        env = stack.enter_context(patch.dict("os.environ", {}))
        env.pop(slu.JOB_ENV, None)
        env.update(environ or {})
        return resolve, validate

    def _issue(self, args, **options):
        commands, saved = [], []
        with ExitStack() as stack:
            resolve, validate = self._patches(stack, saved=saved, **options)
            result = slu.issue_here(args, owner=self.owner,
                                    runner=lambda command, timeout: commands.append(command) or "")
        return result, commands, saved, resolve

    def test_server_installs_the_controllers_hash_and_returns_the_link_without_its_token(self):
        result, commands, saved, resolve = self._issue(_args(here=True, ttl_seconds=900, token_sha256=HASH))
        self.assertEqual(resolve.call_args.args, ("xspeed-dev",))
        self.assertEqual(resolve.call_args.kwargs["name"], "cfe-expiry-1111")
        self.assertNotIn("url", result)
        self.assertEqual(result["login_base"], BASE)
        self.assertEqual((result["user"], result["contract"], result["target_identity"],
                          result["ownership_marker"], result["token_sha256"]),
                         ("xsadmin", slu.CONTRACT, IDENTITY, MARKER, HASH))
        self.assertEqual(len(commands), 3)
        self.assertTrue(all("/home/owner/sandbox/deploy-src/hosts/xspeed-hub-cfe-expiry-1111" in command
                            for command in commands))
        self.assertIn(HASH, commands[2], "the registration carries the controller's hash")
        self.assertEqual(json.loads(saved[-1])["hosts"]["xspeed-dev/xspeed-hub-cfe-expiry-1111/sandbox"]
                         ["autologin"]["user"], "xsadmin")

    def test_recorded_stack_target_is_accepted(self):
        result, _, _, resolve = self._issue(
            _args(here=True, name=None, target="xspeed-hub-cfe-expiry-1111/sandbox", token_sha256=HASH))
        self.assertEqual(resolve.call_args.kwargs["target"], "xspeed-hub-cfe-expiry-1111/sandbox")
        self.assertEqual(result["login_base"], BASE)

    def test_hash_is_required_and_must_be_sha256_hex(self):
        for value in (None, "F" * 64, "f" * 63, "tok-en"):
            with self.subTest(value=value), self.assertRaises(hosting.HostingRefusal) as caught:
                self._issue(_args(here=True, token_sha256=value))
            self.assertEqual(caught.exception.code, "login_url_invalid")

    def test_target_that_is_not_a_recorded_stack_is_refused_before_resolution(self):
        commands = []
        with ExitStack() as stack:
            resolve, validate = self._patches(stack, registry=_registry())
            with self.assertRaises(hosting.HostingRefusal) as caught:
                slu.issue_here(_args(here=True, name=None, target="plain-project/production", token_sha256=HASH),
                               owner=self.owner, runner=lambda command, timeout: commands.append(command))
        self.assertEqual(caught.exception.code, slu.NOT_STACK)
        resolve.assert_not_called()
        validate.assert_not_called()
        self.assertEqual(commands, [])

    def test_plain_resolution_is_refused_even_if_resolve_returns_one(self):
        plain = ResolvedTarget(remote="xspeed-dev", project="xspeed-hub-cfe-expiry-1111",
                               environment="sandbox", kind="plain", project_dir="/home/owner")
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True, token_sha256=HASH), resolved=plain)
        self.assertEqual(caught.exception.code, slu.NOT_STACK)

    def test_target_with_project_dir_is_refused(self):
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True, name=None, target="xspeed-hub-cfe-expiry-1111/sandbox",
                              project_dir="/home/owner/checkout", token_sha256=HASH))
        self.assertEqual(caught.exception.code, "host_target_invalid")
        self.assertIn("--project-dir", caught.exception.detail if hasattr(caught.exception, "detail")
                      else str(caught.exception))

    def test_remote_name_this_host_controls_is_refused(self):
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True, token_sha256=HASH), own_remote={"provisioned": True})
        self.assertEqual(caught.exception.code, slu.REMOTE_MISMATCH)

    def test_caller_that_is_not_the_installed_owner_is_refused(self):
        with ExitStack() as stack:
            resolve, _ = self._patches(stack)
            stack.enter_context(patch("sandbox.application.server_cli.read_current_server_owner",
                                      side_effect=ServerCLIUnavailable("not the owner")))
            with self.assertRaises(hosting.HostingRefusal) as caught:
                slu.issue_here(_args(here=True, token_sha256=HASH), runner=lambda *a: "")
        self.assertEqual(caught.exception.code, slu.UNAVAILABLE)
        resolve.assert_not_called()

    def test_server_refuses_inside_a_durable_job(self):
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True, token_sha256=HASH), environ={slu.JOB_ENV: "f" * 32})
        self.assertEqual(caught.exception.code, slu.JOB_REFUSED)

    def test_target_without_wordpress_autologin_is_refused(self):
        validated = {key: value for key, value in VALIDATED.items() if key != "autologin"}
        with self.assertRaises(hosting.HostingRefusal) as caught:
            self._issue(_args(here=True, token_sha256=HASH), validated=validated)
        self.assertEqual(caught.exception.code, slu.NOT_DECLARED)

    def _run_here(self, args, **options):
        out = io.StringIO()
        with ExitStack() as stack:
            self._patches(stack, **options)
            stack.enter_context(patch("sandbox.application.server_cli.read_current_server_owner",
                                      return_value=self.owner))
            stack.enter_context(redirect_stdout(out))
            code = 0
            try:
                slu.run_here(args)
            except SystemExit as exc:
                code = exc.code
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "always exactly one JSON line")
        return code, json.loads(lines[0]), out.getvalue()

    def test_failure_after_the_hash_is_installed_says_so_without_a_secret(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            if len(calls) == 3:
                raise RuntimeError(f"php failed near {HASH}")
            return ""
        with patch.object(slu, "_local_runner", side_effect=runner):
            code, payload, raw = self._run_here(_args(here=True, token_sha256=HASH))
        self.assertEqual((code, payload["ok"], payload["code"]), (1, False, slu.AFTER_INSTALL))
        self.assertIn("may be installed", payload["error"])
        self.assertNotIn(HASH, raw)

    def test_unexpected_error_is_one_json_line_naming_only_its_type(self):
        with patch.object(slu, "issue_here", side_effect=KeyError("secret-detail-xyz")):
            code, payload, raw = self._run_here(_args(here=True, token_sha256=HASH))
        self.assertEqual((code, payload["code"]), (1, slu.FAILED))
        self.assertIn("KeyError", payload["error"])
        self.assertNotIn("secret-detail-xyz", raw)

    def test_success_reply_is_one_json_line_with_no_token(self):
        with patch.object(slu, "_local_runner", return_value=""):
            code, payload, _ = self._run_here(_args(here=True, token_sha256=HASH))
        self.assertEqual((code, payload["ok"], payload["login_base"]), (0, True, BASE))
        self.assertNotIn("url", payload)
        self.assertNotIn("#", json.dumps(payload))

    def test_here_runs_before_configuration_and_prints_one_json_line(self):
        out = io.StringIO()
        with patch.object(slu, "issue_here", return_value={"login_base": BASE}), redirect_stdout(out):
            self.assertTrue(server_cli_dispatch.dispatch_host_if_selected(_args(here=True)))
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "login_base": BASE})

    def test_here_through_the_real_command_entry_point(self):
        seen = []

        def issue(args, **_):
            seen.append(args)
            return {"login_base": BASE, "token_sha256": args.token_sha256}
        argv = ["sb", "host", "login-url", "--here", "--remote", "xspeed-dev", "--name", "cfe-expiry-1111",
                "--ttl-seconds", "900", "--token-sha256", HASH, "--confirm", "--json"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(slu, "issue_here", side_effect=issue), \
                patch.object(cli, "load_config", side_effect=AssertionError("config before dispatch")), \
                patch("sandbox.controller_update.hook.maybe_auto_update",
                      side_effect=AssertionError("controller update before dispatch")), \
                redirect_stdout(out):
            cli.main()
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0].remote, seen[0].name, seen[0].ttl_seconds, seen[0].token_sha256,
                          seen[0].here, seen[0].confirm), ("xspeed-dev", "cfe-expiry-1111", 900, HASH, True, True))
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {"ok": True, "login_base": BASE, "token_sha256": HASH})

    def test_here_refusal_is_one_json_line_without_a_link(self):
        out = io.StringIO()
        with patch.object(slu, "issue_here", side_effect=hosting.HostingRefusal("host_target_busy", "busy")), \
                redirect_stdout(out), self.assertRaises(SystemExit):
            slu.run_here(_args(here=True))
        self.assertEqual(json.loads(out.getvalue())["code"], "host_target_busy")


class IssuerTests(unittest.TestCase):
    def test_controller_path_still_makes_its_own_token(self):
        state = {"hosts": {"xspeed-dev/xspeed-hub-cfe-expiry-1111/sandbox": {}}}
        commands = []
        result = hosting_cmd._issue_host_autologin(
            VALIDATED, None, "xspeed-dev", state, None, save_state=lambda value: None,
            run=lambda command, timeout: commands.append(command) or "", home="/h")
        token = result["url"].split("#", 1)[1]
        self.assertTrue(result["url"].startswith(BASE + "#"))
        self.assertNotIn("login_base", result)
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), commands[2])
        for command in commands:
            self.assertNotIn(token, command)

    def test_manifest_without_a_primary_route_installs_nothing(self):
        validated = {**VALIDATED, "routes": [{"hostname": "x.example", "primary": False}]}
        state = {"hosts": {"xspeed-dev/xspeed-hub-cfe-expiry-1111/sandbox": {}}}
        commands = []
        with self.assertRaises(StopIteration):
            hosting_cmd._issue_host_autologin(
                validated, None, "xspeed-dev", state, None, save_state=lambda value: None,
                run=lambda command, timeout: commands.append(command) or "", home="/h", token_hash=HASH)
        self.assertEqual(commands, [])


if __name__ == "__main__":
    unittest.main()
