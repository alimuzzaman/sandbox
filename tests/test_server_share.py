"""Feedback f0176eae: `host share` for a stack a joined server records.

A follow (or any server-side `stack up`) records the stack only on the server,
so the controller resolves nothing. The controller now sends one `--here`
call that the real CLI parses and the real server side answers; the token
stays on the controller and only its SHA-256 crosses the channel.
"""
from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sandbox.core._hosting as hosting
from sandbox import cli
from sandbox.commands import host_share
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting import server_login_url as slu
from sandbox.hosting import server_share, share, stack_registry
from sandbox.hosting.target_resolution import ResolvedTarget
from tests.test_host_share import _environment, _share, _validate

MARKER, IDENTITY = "a" * 24, "b" * 24
REMOTE = "xspeed-dev"
ENTRY = {"provisioned": True, "joined": True, "control_transport": "tailscale",
         "tailscale_host": "100.64.0.7", "ssh": "owner@100.64.0.7",
         "mcp_service": {"ownership_marker": MARKER, "runtime_revision": "c" * 24},
         "control_identity": {"schema_version": 1, "target_identity": IDENTITY}}
CAPS = {"schema_version": 1, "ok": True, "target_identity": IDENTITY, "ownership_marker": MARKER,
        "host_login_url_capabilities": [slu.CONTRACT], "host_share_capabilities": [server_share.CONTRACT]}
OWNER = SimpleNamespace(sandbox_home="/home/owner/sandbox", target_identity=IDENTITY, ownership_marker=MARKER)
SLUG = "wp-xspilot"


def _done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["ssh"], code, stdout=stdout, stderr=stderr)


class JoinedShareTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.audit = Path(directory.name, "audit.jsonl")
        self.validated = _validate(_environment(_share()))
        # Built before the fakes: _validate goes through the patched validate_manifest.
        self.terminal_only = _validate(_environment(_share(agent_mint=False)))
        self.resolved = ResolvedTarget(remote=REMOTE, project=self.validated["project"],
                                       environment=self.validated["environment"], kind="stack",
                                       project_dir="/home/owner/sandbox/runtime/stacks/" + SLUG, slug=SLUG)
        self.commands, self.gate, self.records = [], [], {}
        self.caps, self.timeout_create = CAPS, False
        self.terminal = None
        stack = ExitStack()
        self.addCleanup(stack.close)
        self._fakes(stack)

    # The server: the real CLI parses the command and the real --here side answers.
    def _server(self, command: str):
        argv = shlex.split(command.split(" ", 1)[1])
        out, code = io.StringIO(), 0
        with patch.object(sys, "argv", ["sb", *argv]), \
                patch.object(cli, "load_config", side_effect=AssertionError("config before dispatch")), \
                patch("sandbox.controller_update.hook.maybe_auto_update",
                      side_effect=AssertionError("controller update before dispatch")), \
                redirect_stdout(out):
            try:
                cli.main()
            except SystemExit as exc:
                code = exc.code or 0
        return _done(out.getvalue(), code=code)

    def _ssh(self, entry, command, timeout=30, **_kwargs):
        self.commands.append(command)
        if command == slu.PROBE_COMMAND:
            return _done(json.dumps(self.caps) + "\n")
        if self.timeout_create and " share create " in command:
            raise subprocess.TimeoutExpired(command, timeout)
        return self._server(command)

    def _site_here(self, validated, home, verb, name, *extra, input_data=None):
        self.gate.append((verb, name, extra))
        self.assertEqual(home, OWNER.sandbox_home)
        if verb == "mint":
            record = json.loads(input_data)["link"]
            self.records[record["id"]] = record
            return {"ok": True, "id": record["id"]}
        if verb == "list":
            return {"ok": True, "links": [{"id": key} for key in self.records], "sessions": []}
        return {"ok": True, "revoked_links": 1, "revoked_sessions": 0}

    def _fakes(self, stack):
        stack.enter_context(patch("sandbox.core._remote.get_remote",
                                  side_effect=lambda name, **kw: None if kw.get("adopt_joined") is False
                                  else (ENTRY if name == REMOTE else None)))
        stack.enter_context(patch("sandbox.core._remote.ssh_run", side_effect=self._ssh))
        stack.enter_context(patch.object(slu.time, "sleep"))
        stack.enter_context(patch.object(host_share, "resolve_target",
                                         side_effect=AssertionError("controller resolved the target")))
        stack.enter_context(patch("sandbox.application.server_cli.read_current_server_owner",
                                  return_value=OWNER))
        stack.enter_context(patch.object(stack_registry, "read", side_effect=lambda: {"stacks": {
            stack_registry.record_key(REMOTE, SLUG): {
                "remote": REMOTE, "slug": SLUG, "project": self.validated["project"],
                "environment": self.validated["environment"]}}}))
        stack.enter_context(patch("sandbox.hosting.target_resolution.resolve",
                                  side_effect=lambda *_a, **_k: self.resolved))
        stack.enter_context(patch.object(hosting, "validate_manifest",
                                         side_effect=lambda *_a, **_k: self.validated))
        stack.enter_context(patch.object(hosting, "load_host_state", side_effect=lambda: {"hosts": {
            hosting.state_key(REMOTE, self.validated): {"commit": "e" * 40}}}))
        stack.enter_context(patch.object(hosting_cmd, "_with_host_writer_lock",
                                         side_effect=lambda _v, _r, callback: callback({}, lambda _s: None)))
        stack.enter_context(patch.object(server_share, "_site_here", side_effect=self._site_here))
        stack.enter_context(patch.object(share, "audit_path", return_value=self.audit))
        stack.enter_context(patch.object(hosting_cmd, "_operator_terminal", side_effect=lambda: self.terminal))
        environ = stack.enter_context(patch.dict(os.environ, {}))
        for name in (slu.JOB_ENV, host_share.CALLER_ENV):
            environ.pop(name, None)

    def _no_token_sent(self, token):
        for command in self.commands:
            self.assertNotIn(token, command)
        self.assertNotIn(token, self.audit.read_text() if self.audit.exists() else "")

    def test_create_for_a_server_recorded_stack_mints_there_and_keeps_the_token_here(self):
        result = host_share.create_share(REMOTE, name=SLUG, ttl="3d", uses=2, path="/designs/")
        self.assertTrue(result["ok"], result)
        token = result["link"].split("#", 1)[1]
        self.assertTrue(result["link"].startswith(f"https://{self.validated['login_service']['entries']['share']['hostname']}/s/#"))
        self.assertEqual(result["link_id"], share.short_link_id(token))
        record = self.records[result["link_id"]]
        self.assertEqual(record["token_sha256"], share.token_hash(token))
        self.assertEqual((record["scope"], record["uses"], record["kind"]), ("/designs/", 2, "site"))
        self.assertEqual(record["expires_at"] - record["issued_at"], 3 * 86400)
        self.assertEqual(result["target"], f"{self.validated['project']}/{self.validated['environment']}")
        self.assertEqual(len(self.commands), 2, "one probe, one create")
        self.assertIn(share.token_hash(token), self.commands[1])
        self.assertIn("--json-link", self.commands[1], "no terminal here: the server must check agent_mint")
        self._no_token_sent(token)
        audit = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual([(row["op"], row["outcome"]) for row in audit], [("create", "ok"), ("create", "ok")])

    def test_target_selector_resolves_on_the_server_too(self):
        label = f"{self.validated['project']}/{self.validated['environment']}"
        result = host_share.create_share(REMOTE, target=label)
        self.assertTrue(result["ok"])
        self.assertIn("--target " + shlex.quote(label), self.commands[1])

    def test_list_and_revoke_reach_the_servers_gate(self):
        created = host_share.create_share(REMOTE, name=SLUG)
        listed = host_share.list_shares(REMOTE, name=SLUG)
        self.assertEqual([link["id"] for link in listed["links"]], [created["link_id"]])
        revoked = host_share.revoke_share(REMOTE, created["link_id"], name=SLUG)
        self.assertEqual((revoked["ok"], revoked["revoked_links"]), (True, 1))
        self.assertEqual([call[0] for call in self.gate], ["mint", "list", "revoke"])
        self.assertEqual(self.gate[-1][2], (created["link_id"],))

    def test_without_agent_mint_and_no_terminal_nothing_is_stored(self):
        self.validated = self.terminal_only
        with self.assertRaises(share.ShareRefusal) as raised:
            host_share.create_share(REMOTE, name=SLUG)
        self.assertEqual(raised.exception.code, "agent_mint_not_enabled")
        self.assertEqual(self.gate, [])

    def test_without_agent_mint_the_link_goes_to_the_terminal_only(self):
        self.validated = self.terminal_only
        self.terminal = io.StringIO()
        self.terminal.close = lambda: None
        result = host_share.create_share(REMOTE, name=SLUG)
        self.assertEqual(result["link_shown_on"], "terminal")
        self.assertNotIn("link", result)
        token = self.terminal.getvalue().strip().split("#", 1)[1]
        self.assertEqual(share.short_link_id(token), result["link_id"])
        self.assertNotIn("--json-link", self.commands[1])
        self._no_token_sent(token)

    def test_an_older_runtime_is_refused_with_migrate_advice_before_anything_runs(self):
        self.caps = {key: value for key, value in CAPS.items() if key != "host_share_capabilities"}
        with self.assertRaises(share.ShareRefusal) as raised:
            host_share.list_shares(REMOTE, name=SLUG)
        self.assertEqual(raised.exception.code, slu.UNAVAILABLE)
        self.assertIn(f"sb remote service migrate {REMOTE} --confirm", raised.exception.message)
        self.assertEqual(self.commands, [slu.PROBE_COMMAND])

    def test_a_create_that_timed_out_is_not_sent_again(self):
        self.timeout_create = True
        with self.assertRaises(share.ShareRefusal) as raised:
            host_share.create_share(REMOTE, name=SLUG)
        self.assertEqual(raised.exception.code, slu.UNAVAILABLE)
        self.assertEqual(sum(" share create " in command for command in self.commands), 1)

    def test_project_dir_and_a_durable_job_are_refused_before_connecting(self):
        with self.assertRaises(share.ShareRefusal):
            host_share.create_share(REMOTE, name=SLUG, project_dir="/tmp/x")
        with patch.dict(os.environ, {slu.JOB_ENV: "job-1"}), self.assertRaises(share.ShareRefusal) as raised:
            host_share.list_shares(REMOTE, name=SLUG)
        self.assertEqual(raised.exception.code, "job_output_refused")
        self.assertEqual(self.commands, [])

    def test_the_server_side_refuses_a_create_without_a_hash(self):
        reply = self._server(server_share.command("create", REMOTE, name=SLUG))
        self.assertEqual(reply.returncode, 1)
        self.assertEqual(json.loads(reply.stdout)["code"], "share_options_conflict")
        self.assertEqual(self.gate, [])

    def test_an_ordinary_remote_and_a_stack_page_keep_the_controller_path(self):
        self.assertIsNone(server_share.joined_entry("other", target=None, name=SLUG))
        self.assertIsNone(server_share.joined_entry(REMOTE, target=None, name=None, stack=SLUG))
        self.assertIs(server_share.joined_entry(REMOTE, target=None, name=SLUG), ENTRY)


if __name__ == "__main__":
    unittest.main()
