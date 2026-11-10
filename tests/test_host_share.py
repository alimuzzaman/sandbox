"""Spec 081: the `share:` manifest block, gate routing, and `sb host share`.

The runtime half (scope, sessions, the stack page) is in test_share_gate_runtime.py.
"""
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp" / "wp-server"))

import sandbox.core._hosting as hosting  # noqa: E402
from sandbox.commands import host_share  # noqa: E402
from sandbox.commands import hosting as hosting_cmd  # noqa: E402
from sandbox.hosting import login_service as login  # noqa: E402
from sandbox.hosting import share  # noqa: E402
from sandbox.hosting.front_door import nginx  # noqa: E402
from sandbox.hosting.share import stack_page  # noqa: E402
from tests.subprocess_support import run_test_process  # noqa: E402

IMAGE = "python:3.13-alpine@sha256:" + "a" * 64
PRIMARY = "mockups.dev.example.test"
GATE = "s7k2.dev.example.test"
CANARY = "C4nAryT0kenC4nAryT0kenC4nAryT0kenC4nAryT0k"
PORT = 18050


def _future(days=10):
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _share(**fields):
    return {"hostname": GATE, "private": True, "agent_mint": True, "image": IMAGE,
            "enabled_until": _future(), "allowed_host_suffixes": ["dev.example.test"], **fields}


def _environment(share_block=None, **extra):
    environment = {
        "compose": {"files": ["compose.yml"], "service": "web", "container_port": 8080},
        "healthcheck": {"path": "/", "statuses": [200]},
        "deploy": {"allowed_branches": ["main"], "require_clean": True},
        "host": {"primary": PRIMARY, "aliases": [{"hostname": GATE, "mode": "serve"},
                                                 {"hostname": "old.dev.example.test", "mode": "redirect",
                                                  "target": f"https://{PRIMARY}"}]},
        "cloudflare": {"proxied": False, "tls": "acme"},
        **extra,
    }
    if share_block is not None:
        environment["share"] = share_block
    return environment


def _validate(environment):
    document = {"version": 1, "project": "mockups", "environments": {"dev": environment}}
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "sandbox.hosting.yml").write_text(json.dumps(document))
        Path(directory, "compose.yml").write_text("services: {}\n")
        return hosting.validate_manifest(directory)


class ManifestTests(unittest.TestCase):
    def refused(self, environment, needle):
        with self.assertRaises(hosting.HostingError) as raised:
            _validate(environment)
        self.assertIn(needle, str(raised.exception))

    def test_short_form_puts_the_gate_in_front_of_the_routed_service(self):
        validated = _validate(_environment(_share()))
        declaration = validated["login_service"]
        self.assertEqual(declaration["share"], {"hostname": GATE, "private": True, "agent_mint": True,
                                                "stack_sign_in": []})
        entry = declaration["entries"]["share"]
        self.assertEqual((entry["mode"], entry["hostname"], entry["upstream"]),
                         ("site_gate", GATE, "http://web:8080"))
        self.assertEqual(declaration["session_ttl_seconds"], 43200)
        self.assertEqual(login.gate_hostnames(declaration), {GATE})
        self.assertIn("sandbox-login", validated["compose"]["background_services"])
        self.assertEqual(login.runtime_config(declaration)["allowed_host_suffixes"], ["dev.example.test"])

    def test_defaults_are_not_private_and_not_agent_minted(self):
        block = _share()
        block.pop("private")
        block.pop("agent_mint")
        declaration = _validate(_environment(block))["login_service"]
        self.assertFalse(login.is_private(declaration))
        self.assertFalse(declaration["share"]["agent_mint"])

    def test_short_form_refusals(self):
        missing = _share()
        missing.pop("enabled_until")
        self.refused(_environment(missing), "enabled_until is required")
        self.refused(_environment(_share(hostname=PRIMARY)), "served alias")
        self.refused(_environment(_share(hostname="other.dev.example.test")), "served alias")
        self.refused(_environment(_share(colour="blue")), "unknown fields: colour")
        self.refused(_environment(_share(private="yes")), "share.private must be true or false")
        self.refused(_environment(_share(stack_sign_in=["qa"])), "stack_sign_in names 'qa'")
        self.refused(_environment(_share(allowed_host_suffixes=["other.test"])), "does not cover")
        self.refused(_environment("yes"), "share must be a mapping")

    def test_with_a_login_service_the_share_block_carries_only_its_own_fields(self):
        login_block = {"login_service": {"image": IMAGE, "enabled_until": _future(), "session_ttl_seconds": 3600,
                                         "allowed_host_suffixes": ["dev.example.test"]},
                       "entries": {"mockup": {"mode": "site_gate", "hostname": GATE, "upstream": "http://site:80"}}}
        environment = _environment({"hostname": GATE}, autologin=login_block)
        environment["compose"]["background_services"] = ["site"]
        self.refused(environment, "a site_gate hostname must front no other entry")
        environment["host"]["aliases"].append({"hostname": "m.dev.example.test", "mode": "serve"})
        login_block["entries"]["mockup"]["hostname"] = "m.dev.example.test"
        declaration = _validate(environment)["login_service"]
        self.assertEqual(sorted(declaration["entries"]), ["mockup", "share"])
        self.assertEqual(declaration["session_ttl_seconds"], 3600)
        environment["share"] = {"hostname": GATE, "image": IMAGE}
        self.refused(environment, "autologin.login_service already declares them")
        environment["share"] = {"hostname": GATE}
        login_block["entries"]["share"] = login_block["entries"].pop("mockup")
        self.refused(environment, "entries.share is reserved")

    def test_private_with_basic_auth_is_refused(self):
        self.refused(_environment(_share(), basic_auth={"username": "u", "password_secret": "P"}),
                     "share.private")

    def test_wordpress_autologin_and_a_share_block_coexist(self):
        validated = _validate(_environment(_share(private=False), autologin={
            "user": "admin", "container_path": "/var/www/html/wp-content/mu-plugins/99-auto.php"}))
        self.assertEqual(validated["autologin"]["user"], "admin")
        self.assertIn("share", validated["login_service"]["entries"])

    def test_indexing_defaults_to_noindex_with_robots_deny(self):
        validated = _validate(_environment())
        self.assertEqual((validated["indexing"], validated["robots"]), ("noindex", "deny"))
        public = _validate(_environment(indexing="public"))
        self.assertEqual((public["indexing"], public["robots"]), ("public", "allow"))
        self.assertEqual(_validate(_environment(indexing="public", robots="deny"))["robots"], "deny")
        self.refused(_environment(robots="allow"), "needs `indexing: public`")
        self.refused(_environment(indexing="yes"), "indexing must be noindex or public")


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.private = _validate(_environment(_share()))
        self.public = _validate(_environment(_share(private=False)))

    def test_the_gate_publishes_its_own_loopback_port_and_the_app_keeps_its(self):
        override = hosting.compose_override(self.private, PORT)
        self.assertIn(f'"127.0.0.1:{PORT}:8080"', override)
        self.assertIn(f'ports: ["127.0.0.1:{PORT + 3000}:8080"]', override)
        self.assertEqual(login.gate_loopback_port(PORT), PORT + share.GATE_PORT_OFFSET)

    def test_caddy_private_answers_the_primary_with_404(self):
        rendered = hosting.caddyfile(self.private, PORT)
        primary = _caddy_block(rendered, PRIMARY)
        self.assertIn("respond 404", primary)
        self.assertNotIn(f"reverse_proxy 127.0.0.1:{PORT}", rendered)
        self.assertIn(f"reverse_proxy 127.0.0.1:{PORT + 3000}", _caddy_block(rendered, GATE))

    def test_caddy_public_keeps_the_app_route(self):
        rendered = hosting.caddyfile(self.public, PORT)
        self.assertIn(f"reverse_proxy 127.0.0.1:{PORT}", _caddy_block(rendered, PRIMARY))
        self.assertIn(f"reverse_proxy 127.0.0.1:{PORT + 3000}", _caddy_block(rendered, GATE))
        self.assertNotIn(GATE, _caddy_block(rendered, PRIMARY).split("{", 1)[0])

    def test_nginx_private_and_public(self):
        conf = nginx.render_host_conf(self.private, PORT, name="mockups", cert_path="/c", key_path="/k")
        servers = _nginx_servers(conf)
        primary = next(block for block in servers if f"server_name {PRIMARY};" in block)
        gate = next(block for block in servers if f"server_name {GATE};" in block)
        self.assertIn("return 404;", primary)
        self.assertNotIn("proxy_pass", primary)
        self.assertIn(f"proxy_pass http://127.0.0.1:{PORT + 3000};", gate)
        conf = nginx.render_host_conf(self.public, PORT, name="mockups", cert_path="/c", key_path="/k")
        self.assertIn(f"proxy_pass http://127.0.0.1:{PORT};", conf)
        self.assertIsNotNone(nginx.route_marker(conf))

    def test_075_entry_hostnames_are_routed_to_the_gate_too(self):
        login_block = {"login_service": {"image": IMAGE, "enabled_until": _future(), "session_ttl_seconds": 3600,
                                         "allowed_host_suffixes": ["dev.example.test"]},
                       "entries": {"mockup": {"mode": "site_gate", "hostname": GATE, "upstream": "http://site:80"}}}
        environment = _environment(autologin=login_block)
        environment["compose"]["background_services"] = ["site"]
        validated = _validate(environment)
        self.assertIn(f"reverse_proxy 127.0.0.1:{PORT + 3000}", _caddy_block(hosting.caddyfile(validated, PORT), GATE))
        self.assertIn(f"reverse_proxy 127.0.0.1:{PORT}", _caddy_block(hosting.caddyfile(validated, PORT), PRIMARY))


def _caddy_block(rendered, hostname):
    for block in rendered.split("\n\n"):
        if block.split("{", 1)[0].strip().split(", ").count(hostname):
            return block
    raise AssertionError(f"no Caddy block for {hostname}")


def _nginx_servers(conf):
    return [part for part in conf.split("\nserver {")[1:]]


class PreflightTests(unittest.TestCase):
    def _probe(self, services, upstreams=(), gate_port=None):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, "config.json")
            config.write_text(json.dumps({"services": services}))
            fake = Path(directory, "compose.sh")
            fake.write_text(f"#!/bin/sh\nexec /bin/cat {config}\n")
            command = shlex.split(login.preflight_command(f"/bin/sh {fake}", None, list(upstreams), gate_port))
            done = run_test_process([sys.executable, *command[1:]], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_only_the_managed_gate_mapping_is_allowed(self):
        managed = {"mode": "ingress", "host_ip": "127.0.0.1", "target": 8080, "published": str(PORT + 3000)}
        self.assertEqual(self._probe({"sandbox-login": {"ports": [managed]}}, gate_port=PORT + 3000)["login_ports"], 0)
        self.assertEqual(self._probe({"sandbox-login": {"ports": [managed]}})["login_ports"], 1)
        other = {**managed, "published": "9999"}
        self.assertEqual(self._probe({"sandbox-login": {"ports": [managed, other]}},
                                     gate_port=PORT + 3000)["login_ports"], 1)
        public = {**managed, "host_ip": "0.0.0.0"}
        self.assertEqual(self._probe({"sandbox-login": {"ports": [public]}}, gate_port=PORT + 3000)["login_ports"], 1)

    def test_a_private_target_checks_its_routed_service(self):
        declaration = _validate(_environment(_share()))["login_service"]
        self.assertEqual(login.preflight_upstreams(declaration, "web"), ["web"])
        public = _validate(_environment(_share(private=False)))["login_service"]
        self.assertEqual(login.preflight_upstreams(public, "web"), [])
        exposed = {"mode": "ingress", "target": 80, "published": "8090"}
        self.assertEqual(self._probe({"web": {"ports": [exposed]}}, ["web"])["upstream_ports"], 1)
        with self.assertRaises(login.LoginServiceError) as raised:
            login.check_preflight(json.dumps({"capture_present": True, "capture_ports": 0, "login_ports": 0,
                                              "upstream_missing": 0, "upstream_ports": 1}))
        self.assertEqual(raised.exception.code, "upstream_port_published")


class _Terminal(io.StringIO):
    def close(self):
        self.final = self.getvalue()
        super().close()


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audit = Path(self.directory.name, "audit.jsonl")
        self.validated = _validate(_environment(_share()))
        self.target = host_share.ShareTarget(remote="dev", validated=self.validated, entry={"provisioned": True},
                                             label="mockups/dev", project_dir=self.directory.name)
        self.commands, self.inputs, self.replies = [], [], {}

    def _remote(self, _entry, command, timeout=180, **kwargs):
        self.commands.append(command)
        self.inputs.append(kwargs.get("input_data"))
        if " exec -T sandbox-login " not in command:
            return ""
        verb = shlex.split(command.split(" exec -T ", 1)[1])[6]
        if verb in self.replies:
            return self.replies[verb]
        if verb == "mint":
            record = json.loads(kwargs["input_data"])["link"]
            return json.dumps({"ok": True, "id": record["id"], "expires_at": record["expires_at"],
                               "uses": record["uses"]})
        if verb == "list":
            return json.dumps({"ok": True, "links": [], "sessions": []})
        return json.dumps({"ok": True, "revoked_links": 1, "revoked_sessions": 1})

    def run_create(self, *, caller="cli", terminal=None, env=None, target=None, **fields):
        self.terminal = terminal
        target = target or self.target
        with patch.object(host_share, "resolve_target", return_value=target), \
             patch.object(hosting_cmd.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(host_share.remote, "resolve_sandbox_home", return_value="/srv/sandbox"), \
             patch.object(hosting_cmd, "_compose_prefix", return_value="docker compose"), \
             patch.object(hosting_cmd, "_remote_checked", side_effect=self._remote), \
             patch.object(hosting_cmd, "_with_host_writer_lock",
                          side_effect=lambda _v, _r, callback: callback({}, lambda _s: None)), \
             patch.object(hosting_cmd, "_operator_terminal", return_value=terminal), \
             patch.object(share, "audit_path", return_value=self.audit), \
             patch.object(share.secrets, "token_urlsafe", return_value=CANARY), \
             patch.dict(os.environ, env or {}, clear=False):
            for name in ("SANDBOX_DURABLE_JOB_ID", host_share.CALLER_ENV):
                if name not in (env or {}):
                    os.environ.pop(name, None)
            return host_share.create_share("dev", caller=caller, **fields)

    def assert_no_leak(self, *channels):
        digest = hashlib.sha256(CANARY.encode()).hexdigest()
        for channel in (*channels, *self.commands, self.audit.read_text() if self.audit.exists() else ""):
            self.assertNotIn(CANARY, channel)
            self.assertNotIn(digest, channel)
        for sent in self.inputs:
            self.assertNotIn(CANARY, sent or "")

    def test_agent_mint_returns_the_link_once(self):
        result = self.run_create(path="/designs/home/", ttl="3d", uses=2)
        self.assertTrue(result["ok"])
        self.assertRegex(result["link"], rf"^https://{re.escape(GATE)}/s/l-[0-9a-f]{{32}}#{CANARY}$")
        self.assertEqual(result["link"].count(CANARY), 1)
        rest = {key: value for key, value in result.items() if key != "link"}
        self.assert_no_leak(json.dumps(rest))
        self.assertEqual((result["scope"], result["uses"], result["kind"]), ("/designs/home/", 2, "site"))
        self.assertNotIn("designs", result["link"])
        payload = json.loads(next(item for item in self.inputs if item))
        self.assertEqual(payload["link"]["token_sha256"], hashlib.sha256(CANARY.encode()).hexdigest())
        self.assertEqual(payload["link"]["expires_at"] - payload["link"]["issued_at"], 3 * 86400)
        audit = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual(len(audit), 1)
        self.assertEqual({key: audit[0][key] for key in ("op", "outcome", "target", "entry", "scope", "uses",
                                                         "link_id", "remote")},
                         {"op": "create", "outcome": "ok", "target": "mockups/dev", "entry": "share",
                          "scope": "/designs/home/", "uses": 2, "link_id": result["link_id"], "remote": "dev"})
        self.assertEqual(set(audit[0]), set(share.AUDIT_FIELDS))
        self.assertTrue(audit[0]["controller"] and audit[0]["owner"])
        self.assertTrue(any("share-links" in command and "audit.jsonl" in command for command in self.commands))

    def test_without_the_opt_in_mcp_is_refused_and_nothing_is_minted(self):
        validated = _validate(_environment(_share(agent_mint=False)))
        target = host_share.ShareTarget("dev", validated, {"provisioned": True}, "mockups/dev", self.directory.name)
        with self.assertRaises(share.ShareRefusal) as raised:
            self.run_create(caller="mcp", target=target)
        self.assertEqual(raised.exception.code, "agent_mint_not_enabled")
        self.assertFalse(any(" exec -T " in command for command in self.commands))
        with self.assertRaises(share.ShareRefusal) as raised:
            self.run_create(caller="cli", target=target, env={host_share.CALLER_ENV: "mcp"})
        self.assertEqual(raised.exception.code, "agent_mint_not_enabled")
        self.assertEqual(json.loads(self.audit.read_text().splitlines()[-1])["code"], "agent_mint_not_enabled")

    def test_without_the_opt_in_the_cli_uses_the_terminal_only(self):
        validated = _validate(_environment(_share(agent_mint=False)))
        target = host_share.ShareTarget("dev", validated, {"provisioned": True}, "mockups/dev", self.directory.name)
        with self.assertRaises(share.ShareRefusal) as raised:
            self.run_create(target=target, terminal=None)
        self.assertEqual(raised.exception.code, "terminal_required")
        terminal = _Terminal()
        result = self.run_create(target=target, terminal=terminal)
        self.assertNotIn("link", result)
        self.assertEqual(result["link_shown_on"], "terminal")
        self.assertEqual(terminal.final.count(CANARY), 1)
        self.assert_no_leak(json.dumps(result))

    def test_a_durable_job_is_refused(self):
        with self.assertRaises(share.ShareRefusal) as raised:
            self.run_create(env={"SANDBOX_DURABLE_JOB_ID": "job-1"})
        self.assertEqual(raised.exception.code, "job_output_refused")
        self.assertEqual(self.commands, [])

    def test_bad_options_are_refused_before_the_remote(self):
        for fields, code in (({"path": "/a/../b/"}, "share_scope_invalid"), ({"ttl": "31d"}, "share_ttl_invalid"),
                             ({"uses": 0}, "uses_out_of_range"), ({"uses": 21}, "uses_out_of_range"),
                             ({"entry": "nope"}, "share_entry_unknown"),
                             ({"email": "a@dev.example.test"}, "share_options_conflict")):
            with self.subTest(fields=fields):
                self.commands = []
                with self.assertRaises(share.ShareRefusal) as raised:
                    self.run_create(**fields)
                self.assertEqual(raised.exception.code, code)
                self.assertFalse(any(" exec -T " in command for command in self.commands))

    def test_a_share_needs_a_declared_gate(self):
        validated = _validate(_environment())
        target = host_share.ShareTarget("dev", validated, {"provisioned": True}, "mockups/dev", self.directory.name)
        with self.assertRaises(share.ShareRefusal) as raised:
            self.run_create(target=target)
        self.assertEqual(raised.exception.code, "share_not_declared")

    def test_the_gate_must_confirm(self):
        self.replies["mint"] = json.dumps({"ok": False, "code": "site_link_limit"})
        with self.assertRaises(share.ShareRefusal) as raised:
            self.run_create()
        self.assertEqual(raised.exception.code, "site_link_limit")

    def test_cli_json_output(self):
        args = SimpleNamespace(image_action="create", json=True, remote="dev", target=None, name=None, stack=None,
                               project_dir=None, environment=None, entry=None, share_path=None, ttl=None,
                               uses=None, email=None)
        out = io.StringIO()
        with patch.object(host_share, "create_share", return_value={"ok": True, "link": "L"}) as created, \
             contextlib.redirect_stdout(out):
            host_share.cmd_host_share(args)
        self.assertEqual(json.loads(out.getvalue()), {"ok": True, "link": "L"})
        self.assertEqual(created.call_args.kwargs["caller"], "cli")
        args.image_action = "revoke"
        args.share_id = "x; rm -rf /"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit), \
                patch.object(share, "audit_path", return_value=self.audit):
            host_share.cmd_host_share(args)
        self.assertEqual(json.loads(out.getvalue())["code"], "site_link_unknown")

    def test_mcp_tools_answer_with_refusals(self):
        import tools.share as share_tools
        with patch.object(host_share, "create_share", side_effect=share.ShareRefusal("agent_mint_not_enabled", "m")):
            self.assertEqual(share_tools.host_share_create("dev", project_dir="."),
                             {"ok": False, "code": "agent_mint_not_enabled", "message": "m"})
        with patch.object(host_share, "create_share", return_value={"ok": True}) as created:
            share_tools.host_share_create("dev", stack="hub-1")
        self.assertEqual(created.call_args.kwargs["caller"], "mcp")
        from tools import manifest
        self.assertIn("share", manifest.DEFAULT_MCP_GROUPS)
        self.assertEqual(manifest.BUILTIN_TOOL_NAMES["share"],
                         ("host_share_create", "host_share_list", "host_share_revoke"))

    def test_cli_parses_share_verbs(self):
        home = tempfile.mkdtemp(dir=self.directory.name)
        done = run_test_process([sys.executable, str(ROOT / "sb"), "host", "share", "revoke", "nope", "--remote",
                                 "dev", "--json"], capture_output=True, text=True, cwd=self.directory.name,
                                env={"SANDBOX_HOME": home, "SANDBOX_AUTO_UPDATE": "0"})
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertEqual(json.loads(done.stdout.splitlines()[-1])["code"], "site_link_unknown")
        done = run_test_process([sys.executable, str(ROOT / "sb"), "host", "plan", "create", "--remote", "dev"],
                                capture_output=True, text=True, cwd=self.directory.name,
                                env={"SANDBOX_HOME": home, "SANDBOX_AUTO_UPDATE": "0"})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("only available with `host share`", done.stderr)


class StackPageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def target(self, *, private=False, sign_in=(), autologin=True):
        environment = _environment(_share(private=private, stack_sign_in=list(sign_in)), **({"autologin": {
            "user": "admin", "container_path": "/var/www/html/wp-content/mu-plugins/99-auto.php"}} if autologin else {}))
        environment["host"]["aliases"].append({"hostname": "hub.dev.example.test", "mode": "serve"})
        validated = _validate(environment)
        record = {"slug": "hub-1", "hostnames": [PRIMARY, "hub.dev.example.test", GATE, "elsewhere.example.org"],
                  "sites": [{"hostname": "wp1.dev.example.test"}, {"hostname": "x.other.test"}]}
        return host_share.ShareTarget("dev", validated, {"provisioned": True}, "mockups/dev",
                                      self.directory.name, stack=record), validated["login_service"]

    def test_build_lists_hosts_and_the_wordpress_sign_in(self):
        found, declaration = self.target()
        with patch.object(hosting, "load_host_state", return_value={"hosts": {}}):
            page = stack_page.build(found, declaration, request_email=None)
        self.assertEqual(page["title"], "hub-1")
        self.assertEqual([(item["hostname"], item["role"], item["probe"]) for item in page["hosts"]],
                         [(PRIMARY, "mockups", "internal"), ("hub.dev.example.test", "hub", "internal"),
                          ("wp1.dev.example.test", "wp1", "public")])
        self.assertEqual(page["wordpress"]["url"], f"https://{PRIMARY}/")
        keyed = stack_page.with_key(page, "l-" + "c" * 32)
        record = stack_page.gate_record(keyed, "share", "l-" + "c" * 32)
        self.assertEqual(record["wordpress"]["kid"], "c" * 32)
        self.assertRegex(record["wordpress"]["key"], r"^[0-9a-f]{64}$")
        self.assertNotIn("user", record["wordpress"])

    def test_a_private_target_leaves_wordpress_out_and_says_why(self):
        found, declaration = self.target(private=True)
        with patch.object(hosting, "load_host_state", return_value={"hosts": {}}):
            page = stack_page.build(found, declaration, request_email=None)
        self.assertIsNone(page["wordpress"])
        self.assertIn("private", page["notes"][0])

    def test_key_plugin_holds_a_key_and_never_a_token(self):
        plugin = stack_page.render_key_plugin("c" * 32, "d" * 64, "admin", 4_000_000_000)
        self.assertIn("'cccccccccccccccccccccccccccccccc'", plugin)
        self.assertIn("add_site_option( $used", plugin)
        self.assertIn("X-Robots-Tag: noindex, nofollow, noarchive", plugin)
        self.assertIn(f"$exp > $now + {share.SIGN_IN_SECONDS}", plugin)
        for kid, key, user, until in (("short", "d" * 64, "admin", 4_000_000_000),
                                       ("c" * 32, "d" * 63, "admin", 4_000_000_000),
                                       ("c" * 32, "d" * 64, "ad min", 4_000_000_000),
                                       ("c" * 32, "d" * 64, "admin", 1)):
            with self.assertRaises(share.ShareRefusal):
                stack_page.render_key_plugin(kid, key, user, until)

    def test_key_install_goes_over_stdin_and_revoke_deletes_by_link(self):
        found, declaration = self.target()
        with patch.object(hosting, "load_host_state", return_value={"hosts": {}}):
            page = stack_page.with_key(stack_page.build(found, declaration, request_email=None), "l-" + "e" * 32)
        calls = []
        with patch.object(hosting_cmd, "_remote_checked",
                          side_effect=lambda _e, command, **kw: calls.append((command, kw)) or "1\n"), \
             patch.object(stack_page, "_prefix", return_value="docker compose"):
            stack_page.install_wordpress_key(found, page, 4_000_000_000)
            removed = stack_page.remove_wordpress_keys(found, "l-" + "e" * 32)
        command, kwargs = calls[0]
        self.assertNotIn(page["wordpress"]["key"], command)
        self.assertIn(page["wordpress"]["key"], kwargs["input_data"])
        self.assertIn(f"sandbox-share-4000000000-{'e' * 32}.php", command)
        self.assertIn(f"sandbox-share-*-{'e' * 32}.php", calls[1][0])
        self.assertEqual(removed, 1)

    def test_sign_in_entries_need_an_address_for_magic_links(self):
        environment = _environment(_share(stack_sign_in=["qa"]), autologin={
            "login_service": {"image": IMAGE, "enabled_until": _future(), "session_ttl_seconds": 3600,
                              "allowed_host_suffixes": ["dev.example.test"],
                              "capture": {"service": "mail", "adapter": "mailpit_v1", "api_url": "http://mail:8025"}},
            "entries": {"qa": {"mode": "magic_link", "requires_email": True, "hostname": "login.dev.example.test",
                               "path": "/__sandbox/login/qa", "magic_link": {
                                   "request_url": "http://web:8080/login", "request_origin": f"https://{PRIMARY}",
                                   "link_origin": f"https://{PRIMARY}", "link_path": "/verify",
                                   "link_pattern": "https://mockups[^ ]+"}}}})
        environment["share"] = {"hostname": GATE, "agent_mint": True, "stack_sign_in": ["qa"]}
        environment["compose"]["background_services"] = ["mail"]
        environment["host"]["aliases"].append({"hostname": "login.dev.example.test", "mode": "serve"})
        validated = _validate(environment)
        found = host_share.ShareTarget("dev", validated, {"provisioned": True}, "mockups/dev", self.directory.name,
                                       stack={"slug": "hub-1", "hostnames": [PRIMARY]})
        with patch.object(hosting, "load_host_state", return_value={"hosts": {}}):
            with self.assertRaises(login.LoginServiceError) as raised:
                stack_page.build(found, validated["login_service"], request_email=None)
            self.assertEqual(raised.exception.code, "email_required")
            page = stack_page.build(found, validated["login_service"], request_email="qa@dev.example.test")
        self.assertEqual(page["sign_in"], [{"entry": "qa", "email": "qa@dev.example.test"}])
        self.assertEqual(login.gate_hostnames(validated["login_service"]), {GATE, "login.dev.example.test"})


if __name__ == "__main__":
    unittest.main()
