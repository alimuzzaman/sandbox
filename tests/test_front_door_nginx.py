"""Remote front-door contract and the nginx adapter (PRD 056)."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox.commands.hosting as hosting_cmd  # noqa: E402
import sandbox.commands.remote as remote_cmd  # noqa: E402
import sandbox.core._hosting as hosting  # noqa: E402
import sandbox.core._remote as remote  # noqa: E402
from sandbox.hosting import front_door  # noqa: E402
from sandbox.hosting.front_door import nginx  # noqa: E402

PASSWORD = "s3cr+t/pass=word"


def _validated(**overrides):
    validated = {
        "project": "demo", "environment": "production",
        "routes": [
            {"hostname": "example.com", "mode": "serve", "primary": True},
            {"hostname": "*.example.com", "mode": "serve", "primary": False},
            {"hostname": "www.example.org", "mode": "redirect",
             "target": "https://example.com", "primary": False},
        ],
        "robots": "deny",
        "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict"},
        "basic_auth": {
            "username": "op", "password_secret": "DEMO_PASSWORD",
            "bypass_ips": ["203.0.113.42"],
            "bypass_paths": ["/healthz"],
            "bypass_routes": [
                {"path": "/api/oauth2/token", "methods": ["POST"]},
                {"path_template": "/api/v1/orgs/{orgId}/projects", "methods": ["GET", "POST"]},
            ],
        },
    }
    validated.update(overrides)
    return validated


def _render(validated=None, **kwargs):
    options = {"name": "host-demo-production", "cert_path": "/c.pem", "key_path": "/k.pem",
               "basic_auth_password": PASSWORD}
    options.update(kwargs)
    return nginx.render_host_conf(validated or _validated(), 18001, **options)


class FrontDoorManifestTests(unittest.TestCase):
    def test_caddy_is_the_default_and_unrecorded_means_caddy(self):
        self.assertEqual(front_door.DEFAULT_FRONT_DOOR, "caddy")
        self.assertEqual(front_door.front_door_mode({}), "caddy")
        self.assertEqual(front_door.front_door_mode(None), "caddy")
        self.assertEqual(front_door.front_door_mode({"front_door": "nginx"}), "nginx")

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(front_door.FrontDoorError) as ctx:
            front_door.front_door_mode({"front_door": "apache"})
        self.assertEqual(ctx.exception.code, "front_door_unknown")

    def test_caddy_keeps_every_surface(self):
        for capability in front_door.CAPABILITIES:
            self.assertEqual(front_door.require_capability({}, capability), "caddy")

    def test_nginx_refuses_unbuilt_surfaces_before_side_effects(self):
        entry = {"front_door": "nginx"}
        self.assertEqual(front_door.require_capability(entry, "host_routes"), "nginx")
        self.assertEqual(front_door.require_capability(entry, "control_route"), "nginx")
        for capability in ("instance_routes", "preview_routes"):
            with self.assertRaises(front_door.FrontDoorError) as ctx:
                front_door.require_capability(entry, capability)
            self.assertEqual(ctx.exception.code, "front_door_capability_unavailable")

    def test_instance_route_helpers_refuse_on_nginx_without_ssh(self):
        entry = {"front_door": "nginx", "ssh": "u@h"}
        with patch.object(remote, "ssh_run") as run:
            with self.assertRaises(front_door.FrontDoorError):
                remote.configure_instance_https_route(entry, "a.example.com", 18000)
            with self.assertRaises(front_door.FrontDoorError):
                remote.remove_instance_https_route(entry, "a.example.com")
            with self.assertRaises(front_door.FrontDoorError):
                remote.instance_route_hosts(entry, 18000)
        run.assert_not_called()


class NginxRenderTests(unittest.TestCase):
    def test_never_declares_default_server_and_guards_undeclared_hosts(self):
        conf = _render()
        self.assertNotIn("default_server", conf)
        guard = conf.count("return 444;")
        self.assertEqual(guard, 3)  # served, redirect, port-80 servers
        self.assertIn('"~^example\\.com$" 1;', conf)
        self.assertIn('"~^.+\\.example\\.com$" 1;', conf)

    def test_basic_auth_lives_in_the_root_only_map_not_a_worker_readable_file(self):
        conf = _render()
        self.assertNotIn("auth_basic_user_file", conf)
        self.assertIn("map $http_authorization", conf)
        self.assertIn("return 401;", conf)
        self.assertIn("WWW-Authenticate 'Basic realm=\"restricted\"' always", conf)

    def test_bypass_rules_match_the_caddy_contract(self):
        conf = _render()
        self.assertIn('"~^GET:/healthz$" 1;', conf)
        self.assertIn('"~^POST:/api/oauth2/token$" 1;', conf)
        self.assertIn('"~^(GET|POST):/api/v1/orgs/[^/]+/projects$" 1;', conf)
        self.assertIn('"~^1:203\\.0\\.113\\.42$" 1;', conf)
        # Proxy-source check uses the TCP peer, not the incumbent's real_ip view.
        self.assertIn("geo $realip_remote_addr", conf)
        for cidr in hosting._CLOUDFLARE_PROXY_CIDRS:
            self.assertIn(f"    {cidr} 1;", conf)

    def test_without_realip_module_uses_remote_addr(self):
        conf = _render(realip=False)
        self.assertIn("geo $remote_addr", conf)
        self.assertNotIn("$realip_remote_addr", conf)

    def test_authorization_is_stripped_only_on_gated_requests(self):
        conf = _render()
        self.assertIn("proxy_set_header Authorization $sbx_", conf)
        self.assertRegex(conf, r"map \$sbx_\w+_public \$sbx_\w+_authorization \{\n"
                               r"    default \"\";\n    1 \$http_authorization;")
        control = nginx.render_proxy_route("control.example.net", 9174, name="mcp-control.example.net",
                                           cert_path="/c", key_path="/k")
        self.assertNotIn("proxy_set_header Authorization", control)

    def test_cf_connecting_ip_reaches_the_app_only_from_cloudflare_peers(self):
        conf = _render(validated=_validated(basic_auth=None))
        self.assertIn("proxy_set_header CF-Connecting-IP $sbx_", conf)
        self.assertRegex(conf, r"map \$sbx_\w+_cf_peer \$sbx_\w+_cf_connecting_ip \{\n"
                               r"    default \"\";\n    1 \$http_cf_connecting_ip;")

    def test_streaming_uploads_timeouts_and_upgrade(self):
        conf = _render()
        for directive in ("client_max_body_size 0;", "proxy_buffering off;",
                          "proxy_request_buffering off;", "proxy_read_timeout 3600s;",
                          "proxy_http_version 1.1;", "proxy_set_header Upgrade $http_upgrade;",
                          "proxy_set_header X-Forwarded-Proto https;",
                          "proxy_pass http://127.0.0.1:18001;"):
            self.assertIn(directive, conf)

    def test_robots_redirects_and_http_to_https(self):
        conf = _render()
        self.assertIn('return 200 "User-agent: *\\nDisallow: /\\n";', conf)
        self.assertIn("return 308 https://example.com$request_uri;", conf)
        self.assertIn("return 308 https://$host$request_uri;", conf)
        self.assertIn(f"root {nginx.ACME_ROOT};", conf)
        allow = _render(validated=_validated(robots="allow"))
        self.assertNotIn("/robots.txt", allow)

    def test_marker_is_deterministic_and_secret_free(self):
        first = _render()
        second = _render(basic_auth_password="a-different-secret")
        self.assertEqual(nginx.route_marker(first), nginx.route_marker(second))
        self.assertIsNotNone(nginx.route_marker(first))
        redacted = _render(basic_auth_password=None, redact_basic_auth=True)
        self.assertNotIn(PASSWORD, redacted)
        self.assertEqual(nginx.route_marker(redacted), nginx.route_marker(first))

    def test_missing_password_is_refused_unless_redacted(self):
        with self.assertRaises(ValueError):
            _render(basic_auth_password=None)

    def test_bootstrap_without_certificate_serves_only_port_80(self):
        conf = _render(cert_path=None, key_path=None)
        self.assertNotIn("listen 443", conf)
        self.assertNotIn(PASSWORD, conf)
        self.assertIn("listen 80;", conf)
        self.assertIn("/.well-known/acme-challenge/", conf)

    def test_listen_variants(self):
        self.assertIn("listen [::]:443 ssl;", _render(ipv6=True))
        self.assertNotIn("[::]", _render())
        legacy = _render(http2_directive=False, ipv6=True)
        self.assertIn("listen 443 ssl http2;", legacy)
        self.assertIn("listen [::]:443 ssl http2;", legacy)
        self.assertNotIn("http2 on;", legacy)

    def test_variable_prefixes_are_per_route(self):
        a = _render(name="host-a-production")
        b = _render(name="host-b-production")
        self.assertNotEqual(nginx._var("host-a-production"), nginx._var("host-b-production"))
        self.assertIn(nginx._var("host-a-production"), a)
        self.assertNotIn(nginx._var("host-a-production"), b)

    def test_unsafe_tokens_are_rejected(self):
        with self.assertRaises(ValueError):
            nginx._q('a"b')
        with self.assertRaises(ValueError):
            nginx.route_file("../etc")


NGINX_DUMP = """# configuration file /etc/nginx/nginx.conf:
http {
    include /etc/nginx/sites-enabled/*;
    include /etc/nginx/conf.d/*.conf;
}
# configuration file /etc/nginx/sites-enabled/panel-site:
server {
    listen 80;
    server_name panel.example.com www.panel.example.com; # comment server_name ignored.example
}
server {
    server_name *.wild.example.net
        .dot.example.org;
}
server { server_name ~^shop\\d+\\.example\\.io$; }
# configuration file /etc/nginx/conf.d/default.conf:
server {
    listen 80;
    server_name  localhost;
}
# configuration file /etc/nginx/conf.d/sandbox-host-demo-production.conf:
server { server_name example.com; }
"""


class NginxConflictTests(unittest.TestCase):
    def setUp(self):
        self.pairs = nginx.parse_server_names(NGINX_DUMP)

    def test_parse_server_names_by_file(self):
        names = {name for _, name in self.pairs}
        self.assertIn("panel.example.com", names)
        self.assertIn("*.wild.example.net", names)
        self.assertIn(".dot.example.org", names)
        self.assertNotIn("ignored.example", names)
        self.assertIn(("/etc/nginx/conf.d/default.conf", "localhost"), self.pairs)

    def _conflicts(self, declared, own=None):
        return nginx.find_conflicts(declared, self.pairs, own)

    def test_exact_match_conflicts_with_panel_site(self):
        rows = self._conflicts(["panel.example.com"])
        self.assertEqual(rows[0]["owner"], "panel")
        self.assertEqual(rows[0]["file"], "/etc/nginx/sites-enabled/panel-site")

    def test_declared_wildcard_covering_a_panel_host_conflicts(self):
        self.assertTrue(self._conflicts(["*.example.com"]))
        self.assertFalse(self._conflicts(["*.unrelated.com"]))

    def test_panel_wildcards_and_regexes_cover_declared_hosts(self):
        self.assertTrue(self._conflicts(["a.wild.example.net"]))
        self.assertTrue(self._conflicts(["dot.example.org"]))
        self.assertTrue(self._conflicts(["x.dot.example.org"]))
        self.assertTrue(self._conflicts(["shop12.example.io"]))
        self.assertFalse(self._conflicts(["shop.example.io"]))

    def test_own_route_is_not_a_conflict_but_another_sandbox_route_is(self):
        own = "/etc/nginx/conf.d/sandbox-host-demo-production.conf"
        self.assertEqual(self._conflicts(["example.com"], own), [])
        rows = self._conflicts(["example.com"])
        self.assertEqual(rows[0]["owner"], "sandbox")

    def test_preflight_refuses_conflicts_caddy_and_missing_include(self):
        base = {"nginx_installed": True, "active": True, "includes_conf_d": True,
                "config_valid": True, "sandbox_caddy": {}, "listeners": [], "conflicts": []}
        with patch.object(nginx, "probe", return_value=base):
            self.assertIs(nginx.preflight({}, declared=["a.com"], own_file=None), base)
        cases = {
            "front_door_hostname_conflict": {"conflicts": [{"hostname": "a.com", "server_name": "a.com",
                                                            "file": "/x", "owner": "panel"}]},
            "front_door_caddy_present": {"listeners": [{"port": "443", "processes": ["caddy"]}]},
            "front_door_include_missing": {"includes_conf_d": False},
            "front_door_nginx_invalid": {"config_valid": False},
            "front_door_nginx_inactive": {"active": False},
            "front_door_nginx_missing": {"nginx_installed": False},
        }
        for code, change in cases.items():
            with self.subTest(code=code), patch.object(nginx, "probe", return_value={**base, **change}):
                with self.assertRaises(front_door.FrontDoorError) as ctx:
                    nginx.preflight({}, declared=["a.com"], own_file=None)
                self.assertEqual(ctx.exception.code, code)

    def test_foreign_listeners_exclude_sandbox_caddy(self):
        facts = {"listeners": [{"port": "80", "processes": ["nginx"]},
                               {"port": "443", "processes": ["caddy"]}]}
        self.assertEqual([row["processes"] for row in nginx.foreign_listeners(facts)], [["nginx"]])


class NginxTransactionTests(unittest.TestCase):
    def test_transaction_script_is_valid_shell(self):
        result = subprocess.run(["sh", "-n"], input=nginx.transaction_script(),
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_transaction_is_serialized_validated_and_never_touches_nginx_conf(self):
        command = nginx.transaction_command("/etc/nginx/conf.d/sandbox-x.conf", "/s", "d" * 64,
                                            operation="apply", remove=False,
                                            probe_host="a.com", marker="m")
        self.assertIn(f"flock -w {nginx.LOCK_WAIT_SECONDS} {nginx.LOCK_PATH}", command)
        script = nginx.transaction_script()
        self.assertIn("nginx -t", script)
        self.assertIn("systemctl reload nginx", script)
        self.assertIn("restore_local", script)
        self.assertIn("install -o root -g root -m 0600", script)
        self.assertNotIn("/etc/nginx/nginx.conf", command)

    def test_route_content_travels_on_stdin_not_argv(self):
        conf = _render()
        calls = []

        def fake(entry, command, timeout=30, input_data=None):
            calls.append((command, input_data))
            return subprocess.CompletedProcess([], 0, "[Sandbox] nginx phase=complete state=changed\n", "")

        with patch.object(remote, "ssh_run", side_effect=fake):
            result = nginx.apply_route({"ssh": "u@h"}, "host-demo-production", conf)
        self.assertEqual(result["state"], "changed")
        self.assertTrue(all(PASSWORD not in command for command, _ in calls))
        self.assertTrue(all("czNjcit0" not in command for command, _ in calls))
        self.assertEqual(calls[0][1], conf)

    def test_failed_transaction_reports_phase(self):
        with patch.object(remote, "ssh_run", side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 70, "", "nginx: [emerg] bad")]):
            with self.assertRaises(front_door.FrontDoorError) as ctx:
                nginx.apply_route({"ssh": "u@h"}, "host-demo-production", _render())
        self.assertEqual(ctx.exception.code, "front_door_validate_failed")

    def test_probe_program_compiles(self):
        program = nginx._PROBE_PROGRAM % {
            "conf_dir": nginx.CONF_DIR, "prefix": nginx.FILE_PREFIX,
            "shared": nginx._SHARED_SOURCE, "marker_path": nginx.MARKER_PATH,
            "overdue": nginx.RENEWAL_OVERDUE_DAYS}
        compile(program, "<probe>", "exec")

    def test_certbot_command_installs_hook_and_uses_webroot(self):
        command = nginx.certbot_command("host-demo-production", ["example.com", "www.example.com"])
        self.assertIn(f"--webroot -w {nginx.ACME_ROOT}", command)
        self.assertIn("--cert-name sandbox-host-demo-production", command)
        self.assertIn(nginx.DEPLOY_HOOK, command)
        self.assertIn("nginx -t && systemctl reload nginx", command)
        with self.assertRaises(front_door.FrontDoorError):
            nginx.ensure_public_certificate({}, "x", ["*.example.com"])

    def test_status_flags_unloaded_routes_conflicts_and_renewal(self):
        report = nginx.summarize({"active": True, "config_valid": True, "routes": [
            {"file": "/etc/nginx/conf.d/sandbox-a.conf", "readable": True, "mode": "0o600",
             "loaded": False, "conflicts": [{"hostname": "a.com", "server_name": "a.com",
                                             "file": "/p", "owner": "panel"}],
             "certificates": [{"path": "/etc/letsencrypt/live/x/fullchain.pem", "present": True,
                               "not_after": "Oct 20 00:00:00 2026 GMT", "expired": False,
                               "renewal_overdue": True, "issuer": "public"}]}]})
        self.assertEqual(report["unloaded_routes"], ["/etc/nginx/conf.d/sandbox-a.conf"])
        self.assertEqual(report["conflicts"][0]["route_file"], "/etc/nginx/conf.d/sandbox-a.conf")
        self.assertIn("renewal has not succeeded", report["certificate_problems"][0])


class CaddyUnchangedTests(unittest.TestCase):
    """The Caddy adapter is a pass-through over the historical helpers."""

    def test_unrecorded_remote_selects_caddy_adapter(self):
        self.assertIsInstance(hosting_cmd._host_edge({}), hosting_cmd._CaddyHostEdge)
        self.assertIsInstance(hosting_cmd._host_edge({"front_door": "nginx"}),
                              hosting_cmd._NginxHostEdge)

    def test_caddy_render_equals_direct_caddyfile(self):
        validated = _validated()
        edge = hosting_cmd._CaddyHostEdge({"ssh": "u@h"})
        with patch.object(hosting_cmd, "_remote_basic_auth_hash", return_value="$2a$14$hash") as hashed:
            rendered = edge.render(validated, 18001, "/c", "/k", {"DEMO_PASSWORD": PASSWORD})
        hashed.assert_called_once_with({"ssh": "u@h"}, PASSWORD)
        self.assertEqual(rendered, hosting.caddyfile(validated, 18001, "/c", "/k", "$2a$14$hash"))

    def test_caddy_adapter_delegates_with_identical_arguments(self):
        entry = {"ssh": "u@h"}
        edge = hosting_cmd._CaddyHostEdge(entry)
        with patch.object(hosting_cmd, "_read_remote_optional", return_value="prev") as read, \
                patch.object(hosting_cmd, "_configure_host_caddy", return_value={"state": "changed"}) as conf, \
                patch.object(hosting_cmd, "_restore_host_caddy", return_value={}) as restore, \
                patch.object(hosting_cmd, "_origin_certificate", return_value=("c", "k", {})) as origin:
            self.assertEqual(edge.read_previous("sandbox-host-a-b"), "prev")
            edge.configure("sandbox-host-a-b", "content", "prev", log_path="/log")
            edge.restore("sandbox-host-a-b", "prev", log_path="/log")
            self.assertEqual(edge.certificate(False, {}, {}, {}, None, "/h", "n"), (None, None, None))
            edge.certificate(True, {"v": 1}, {"r": 1}, {"s": 1}, "client", "/h", "n")
        read.assert_called_once_with(entry, "/etc/caddy/conf.d/sandbox-host-a-b.caddy")
        conf.assert_called_once_with(entry, "sandbox-host-a-b", "content", "prev", log_path="/log")
        restore.assert_called_once_with(entry, "sandbox-host-a-b", "prev", log_path="/log")
        origin.assert_called_once_with(entry, {"v": 1}, {"r": 1}, {"s": 1}, "client", "/h")
        self.assertEqual(edge.after_dns({}, 1, {}, "c", {"id": 1}, "n"), {"id": 1})

    def test_caddy_control_route_command_is_unchanged(self):
        entry = {"ssh": "u@h"}
        with patch.object(remote, "ssh_run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            remote.configure_https_proxy(entry, "control.example.com", 9174)
        run.assert_called_once_with(
            entry, remote._caddy_proxy_command("control.example.com", 9174, "sandbox-mcp"), timeout=180)

    def test_caddy_preflight_and_plan_make_no_remote_call(self):
        plan = {"runtime": {"caddyfile": "x"}}
        with patch.object(remote, "ssh_run") as run:
            hosting_cmd._host_edge_preflight(_validated(), {})
            hosting_cmd._add_front_door_plan(plan, _validated(), {})
        run.assert_not_called()
        self.assertEqual(plan, {"runtime": {"caddyfile": "x"}})

    def test_caddy_doctor_adds_no_rows(self):
        self.assertEqual(remote.front_door_doctor_checks({}), [])


class ProvisionPreflightTests(unittest.TestCase):
    def test_foreign_listener_blocks_caddy_install_and_names_it(self):
        facts = {"listeners": [{"port": "80", "processes": ["nginx"]}]}
        with patch.object(nginx, "probe", return_value=facts), \
                patch.object(remote_cmd, "die", side_effect=SystemExit) as die:
            with self.assertRaises(SystemExit):
                remote_cmd._front_door_provision_preflight("vps", {"ssh": "u@h"}, "https", "c.example.com")
        message = die.call_args.args[0]
        self.assertIn("front_door_foreign_listener", message)
        self.assertIn("nginx", message)
        self.assertIn("--front-door nginx", message)

    def test_sandbox_caddy_is_never_foreign(self):
        facts = {"listeners": [{"port": "443", "processes": ["caddy"]}]}
        with patch.object(nginx, "probe", return_value=facts), \
                patch.object(remote_cmd, "die", side_effect=SystemExit):
            remote_cmd._front_door_provision_preflight("vps", {"ssh": "u@h"}, "https", "c.example.com")

    def test_tailscale_caddy_provision_does_not_probe(self):
        with patch.object(nginx, "probe") as probe:
            remote_cmd._front_door_provision_preflight("vps", {"ssh": "u@h"}, "tailscale", None)
        probe.assert_not_called()

    def test_switching_a_hosting_remote_is_refused(self):
        entry = {"ssh": "u@h", "provisioned": False}
        with patch.object(remote_cmd, "hosting_state_keys", return_value=["vps/site/production"]), \
                patch.object(remote_cmd, "die", side_effect=SystemExit) as die:
            with self.assertRaises(SystemExit):
                remote_cmd._checked_front_door("vps", entry, "nginx")
        self.assertIn("front_door_switch_refused", die.call_args.args[0])

    def test_choosing_nginx_where_sandbox_caddy_runs_is_refused(self):
        entry = {"ssh": "u@h"}
        facts = {"sandbox_caddy": {"active": True}, "listeners": []}
        with patch.object(remote_cmd, "hosting_state_keys", return_value=[]), \
                patch.object(nginx, "probe", return_value=facts), \
                patch.object(remote_cmd, "die", side_effect=SystemExit) as die:
            with self.assertRaises(SystemExit):
                remote_cmd._checked_front_door("vps", entry, "nginx")
        self.assertIn("front_door_caddy_present", die.call_args.args[0])

    def test_same_mode_or_default_records_nothing(self):
        self.assertIsNone(remote_cmd._checked_front_door("vps", {"front_door": "nginx"}, "nginx"))
        self.assertIsNone(remote_cmd._checked_front_door("vps", None, "caddy"))
        self.assertIsNone(remote_cmd._checked_front_door("vps", {}, None))


if __name__ == "__main__":
    unittest.main()
