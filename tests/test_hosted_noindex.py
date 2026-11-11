"""Spec 081 FR-023: no hosted route can answer without `X-Robots-Tag`.

Every renderer branch is rendered on both front doors and parsed. In Caddy each site
block must carry the deferred header at site level, which covers every handler inside
it. In nginx `add_header` is not inherited into a level that declares its own, so
every server, every location and every if-block with an `add_header` must carry it,
and every proxied location must hide the application's own value.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sandbox.core._hosting as hosting  # noqa: E402
from sandbox.core import _remote  # noqa: E402
from sandbox.hosting.front_door import nginx  # noqa: E402
from sandbox.hosting.tailnet_names import models as tailnet_models, render as tailnet_render  # noqa: E402
from tests.test_host_share import GATE, PRIMARY, _environment, _share  # noqa: E402

TAG = "noindex, nofollow, noarchive"
CADDY_HEADER = f'    header {{\n        defer\n        X-Robots-Tag "{TAG}"\n    }}\n'
PORT = 18060
BASIC = {"username": "reviewer", "password_secret": "REVIEW_PASSWORD"}
PROXIED = {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict"}


def _validate(environment):
    document = {"version": 1, "project": "mockups", "environments": {"dev": environment}}
    environment.setdefault("secrets", {"required": {"REVIEW_PASSWORD": "SOURCE_REVIEW_PASSWORD"}})
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "sandbox.hosting.yml").write_text(json.dumps(document))
        Path(directory, "compose.yml").write_text("services: {}\n")
        return hosting.validate_manifest(directory)


def _variants():
    """Every renderer branch: plain, robots, basic auth with each bypass, gates, redirects."""
    gate_075 = {"login_service": {"image": "python:3.13-alpine@sha256:" + "a" * 64,
                                  "enabled_until": _share()["enabled_until"], "session_ttl_seconds": 3600,
                                  "allowed_host_suffixes": ["dev.example.test"]},
                "entries": {"mockup": {"mode": "site_gate", "hostname": GATE, "upstream": "http://site:80"}}}
    with_site = _environment(autologin=gate_075)
    with_site["compose"]["background_services"] = ["site"]
    return {
        "plain": _environment(),
        "robots_deny_explicit": _environment(robots="deny"),
        "basic_auth": _environment(basic_auth=dict(BASIC)),
        "basic_auth_bypass_paths": _environment(basic_auth={**BASIC, "bypass_paths": ["/health"]}),
        "basic_auth_bypass_routes": _environment(basic_auth={**BASIC, "bypass_routes": [
            {"path_template": "/api/{id}", "methods": ["GET", "POST"]}, {"path": "/hook", "methods": ["POST"]}]}),
        "basic_auth_bypass_ips": _environment(cloudflare=dict(PROXIED),
                                              basic_auth={**BASIC, "bypass_ips": ["103.95.98.15"]}),
        "share_private": _environment(_share()),
        "share_public": _environment(_share(private=False)),
        "site_gate_075": with_site,
    }


def _caddy_sites(rendered):
    """Top-level site blocks, by brace depth."""
    blocks, depth, current = [], 0, []
    for line in rendered.splitlines(keepends=True):
        current.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0 and "".join(current).strip():
            blocks.append("".join(current))
            current = []
    return [block for block in blocks if "{" in block]


def _nginx_blocks(conf, opener):
    """Each block that starts with ``opener`` (e.g. ``server {``), with its body."""
    found = []
    lines = conf.splitlines()
    for index, line in enumerate(lines):
        if not re.match(rf"^\s*{opener}", line):
            continue
        depth, body = 0, []
        for item in lines[index:]:
            body.append(item)
            depth += item.count("{") - item.count("}")
            if depth == 0:
                break
        found.append("\n".join(body))
    return found


class CaddyCoverageTests(unittest.TestCase):
    def test_every_site_block_of_every_variant_carries_the_header(self):
        for name, environment in _variants().items():
            validated = _validate(environment)
            for rendered in (hosting.caddyfile(validated, PORT, redact_basic_auth=True),
                             hosting.caddyfile(validated, PORT, "/c.pem", "/k.pem",
                                               "$2a$14$" + "x" * 53 if validated.get("basic_auth") else None)):
                sites = _caddy_sites(rendered)
                self.assertGreaterEqual(len(sites), 2, name)  # served plus the redirect alias
                for site in sites:
                    with self.subTest(variant=name, site=site.split("{", 1)[0].strip()):
                        self.assertIn(CADDY_HEADER, site)
                        # Site level, not nested in one handler.
                        self.assertTrue(any(line == "    header {" for line in site.splitlines()))

    def test_public_indexing_drops_the_header(self):
        validated = _validate(_environment(indexing="public"))
        self.assertNotIn("X-Robots-Tag", hosting.caddyfile(validated, PORT))
        self.assertNotIn("robots.txt", hosting.caddyfile(validated, PORT))

    def test_robots_txt_is_denied_by_default(self):
        validated = _validate(_environment())
        self.assertIn("handle /robots.txt", hosting.caddyfile(validated, PORT))

    def test_preview_routes_carry_the_header(self):
        command = _remote._caddy_proxy_command("p.dev.example.test", PORT, "sandbox-preview")
        self.assertIn(f'X-Robots-Tag "{TAG}"', command)
        self.assertNotIn("X-Robots-Tag", _remote._caddy_proxy_command(
            "p.dev.example.test", PORT, "sandbox-preview", robots="allow"))


class NginxCoverageTests(unittest.TestCase):
    @staticmethod
    def uncovered(conf):
        """Every server, location or if-block of ``conf`` that can answer without the header."""
        header = f'add_header X-Robots-Tag "{TAG}" always;'
        missing = []
        for server in _nginx_blocks(conf, r"server \{"):
            name = re.search(r"server_name ([^;]+);", server).group(1)
            if f"\n    {header}" not in server:
                missing.append((name, "server"))
            for location in _nginx_blocks(server, r"location "):
                if header not in location:
                    missing.append((name, location.splitlines()[0].strip()))
                if "proxy_pass" in location and "proxy_hide_header X-Robots-Tag;" not in location:
                    missing.append((name, location.splitlines()[0].strip() + " (no proxy_hide_header)"))
            for block in _nginx_blocks(server, r"if \("):
                if "add_header" in block and header not in block:
                    missing.append((name, block.splitlines()[0].strip()))
        return missing

    def assert_covered(self, conf, label):
        self.assertTrue(_nginx_blocks(conf, r"server \{"), label)
        self.assertEqual(self.uncovered(conf), [], label)

    def test_every_server_location_and_if_block_carries_the_header(self):
        for name, environment in _variants().items():
            validated = _validate(environment)
            for conf in (nginx.render_host_conf(validated, PORT, name="mockups", cert_path="/c", key_path="/k",
                                                basic_auth_password="pw" if validated.get("basic_auth") else None),
                         nginx.render_host_conf(validated, PORT, name="mockups", cert_path=None, key_path=None,
                                                redact_basic_auth=True)):
                self.assert_covered(conf, name)

    def test_control_routes_carry_it_too(self):
        conf = nginx.render_proxy_route("control.dev.example.test", PORT, name="control",
                                        cert_path="/c", key_path="/k")
        self.assert_covered(conf, "control")

    def test_public_indexing_drops_it(self):
        validated = _validate(_environment(indexing="public"))
        conf = nginx.render_host_conf(validated, PORT, name="mockups", cert_path="/c", key_path="/k")
        self.assertNotIn("X-Robots-Tag", conf)

    def test_a_missing_header_is_caught(self):
        validated = _validate(_environment())
        conf = nginx.render_host_conf(validated, PORT, name="mockups", cert_path="/c", key_path="/k")
        broken = conf.replace(f'        add_header X-Robots-Tag "{TAG}" always;\n        proxy_pass',
                              "        proxy_pass", 1)
        self.assertNotEqual(broken, conf)
        self.assertEqual(self.uncovered(conf), [])
        self.assertEqual(len(self.uncovered(broken)), 1)


class TailnetNameCoverageTests(unittest.TestCase):
    """Spec 078 tailnet-only blocks: the header at server level, inherited everywhere."""

    def block(self, http2):
        return tailnet_render.render_block(
            tailnet_models.TailnetName("mail.box.example.test", "example.test"),
            tailnet_models.Upstream("127.0.0.1", 8025), "100.64.0.7", http2_directive=http2)

    def test_both_servers_carry_it_and_no_location_overrides_it(self):
        header = f'add_header X-Robots-Tag "{TAG}" always;'
        for http2 in (True, False):
            servers = _nginx_blocks(self.block(http2), r"server \{")
            self.assertEqual(len(servers), 2)
            for server in servers:
                with self.subTest(http2=http2, server=server.splitlines()[1].strip()):
                    self.assertIn(f"\n    {header}", server)
                    # nginx drops inherited add_header in a level that declares its own,
                    # so no location or if-block here may declare any.
                    for location in _nginx_blocks(server, r"location "):
                        self.assertNotIn("add_header", location)
                        if "proxy_pass" in location:
                            self.assertIn("proxy_hide_header X-Robots-Tag;", location)
                    for block in _nginx_blocks(server, r"if \("):
                        self.assertNotIn("add_header", block)
                    self.assertNotIn("add_header", server.replace(f"\n    {header}", "", 1))


if __name__ == "__main__":
    unittest.main()
