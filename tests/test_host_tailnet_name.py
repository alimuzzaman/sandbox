"""Tailnet-only hostnames (spec 078): models, render, broker, verify, state,
service flows over fakes, and the CLI surface.

Placeholders only: example.test, 203.0.113.x (public), 100.64.0.x (tailnet).
"""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import io
import json
import os
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sandbox.hosting import front_door  # noqa: E402
from sandbox.hosting.front_door import FrontDoorError  # noqa: E402
from sandbox.hosting.tailnet_names import models as m  # noqa: E402
from sandbox.hosting.tailnet_names import broker as broker_mod  # noqa: E402
from sandbox.hosting.tailnet_names import render, service, state, verify, x509  # noqa: E402
from sandbox.hosting.tailnet_names.broker import Broker  # noqa: E402
from sandbox.secrets import SecretBrokerError  # noqa: E402

MARKER = "tnmarker-0123456789abcdef-SECRET"
TAILNET_IP = "100.64.0.10"
PUBLIC_IP = "203.0.113.10"
FQDN = "app.example.test"
NOW = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)


def config(**overrides) -> m.TailnetNameConfig:
    block = {"zones": ["example.test"],
             "cloudflare_token": {"source": "personal", "key": "EXAMPLE_CF_API_TOKEN"},
             "acme_email": "ops@example.test"}
    block.update(overrides)
    return m.TailnetNameConfig.parse(block)


def target(public_ip: str | None = PUBLIC_IP) -> m.RemoteTarget:
    return m.RemoteTarget("r1", {"ssh": "user@" + PUBLIC_IP, "front_door": "nginx",
                                 "tailscale_host": TAILNET_IP}, TAILNET_IP, public_ip)


class ModelTests(unittest.TestCase):
    def test_config_requires_block_and_names_only(self):
        with self.assertRaises(m.TailnetNameError) as caught:
            m.TailnetNameConfig.parse(None)
        self.assertEqual(caught.exception.code, m.CONFIG_MISSING)
        for bad in (
            {"zones": []},
            {"cloudflare_token": {"source": "personal", "key": "K", "value": MARKER}},
            {"cloudflare_token": "personal/K"},
            {"acme_email": "not-an-email"},
            {"acme_server": "http://acme.example.test/dir"},
            {"lego": "lego"},
            {"zones": ["Example.TEST"]},
        ):
            with self.subTest(bad=bad), self.assertRaises(m.TailnetNameError) as caught:
                config(**bad)
            self.assertEqual(caught.exception.code, m.CONFIG_INVALID)

    def test_config_defaults_and_acme_shortcodes(self):
        parsed = config()
        self.assertEqual(parsed.zones, ("example.test",))
        self.assertEqual(parsed.acme_server, m.ACME_SERVERS["letsencrypt"])
        staged = parsed.with_acme_server("letsencrypt-staging")
        self.assertEqual(staged.acme_server, m.ACME_SERVERS["letsencrypt-staging"])
        self.assertEqual(config(acme_server="https://acme.example.test/directory").acme_server,
                         "https://acme.example.test/directory")

    def test_name_rules(self):
        zones = ("example.test", "lab.example.test")
        self.assertEqual(m.TailnetName.parse(FQDN, zones), m.TailnetName(FQDN, "example.test"))
        self.assertEqual(m.TailnetName.parse("a.lab.example.test", zones).zone, "lab.example.test")
        for bad, code in (("example.test", m.INVALID), ("*.example.test", m.INVALID),
                          ("App.example.test", m.INVALID), ("app.other.test", m.ZONE_NOT_ALLOWED),
                          ("app..example.test", m.INVALID), ("-a.example.test", m.INVALID),
                          ("a" * 140 + ".example.test", m.INVALID), (None, m.INVALID)):
            with self.subTest(bad=bad), self.assertRaises(m.TailnetNameError) as caught:
                m.TailnetName.parse(bad, zones)
            self.assertEqual(caught.exception.code, code)
        name = m.TailnetName.parse(FQDN, zones)
        self.assertEqual(name.route_name, "tailnet-" + FQDN)
        self.assertEqual(name.cert_dir, "/etc/nginx/sandbox-tailnet/" + FQDN)

    def test_upstream_rules(self):
        self.assertEqual(str(m.Upstream.parse("localhost:8025")), "127.0.0.1:8025")
        self.assertEqual(str(m.Upstream.parse("10.1.2.3:80")), "10.1.2.3:80")
        self.assertEqual(str(m.Upstream.parse("100.64.0.10:8780")), "100.64.0.10:8780")
        for bad in ("203.0.113.5:80", "app.internal:80", "127.0.0.1", "127.0.0.1:0",
                    "127.0.0.1:65536", "[::1]:80", "127.0.0.1:80; evil", "169.254.1.1:80"):
            with self.subTest(bad=bad), self.assertRaises(m.TailnetNameError) as caught:
                m.Upstream.parse(bad)
            self.assertEqual(caught.exception.code, m.UPSTREAM_INVALID)

    def test_tailnet_address(self):
        self.assertEqual(m.tailnet_address(TAILNET_IP), TAILNET_IP)
        for bad in (None, "", "203.0.113.10", "host.ts.net", "100.128.0.1"):
            with self.subTest(bad=bad), self.assertRaises(m.TailnetNameError) as caught:
                m.tailnet_address(bad)
            self.assertEqual(caught.exception.code, m.NO_TAILNET_ADDRESS)

    def test_public_address(self):
        def resolver(host, *_args):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", 443))]
        self.assertEqual(m.public_address(override=None, ssh_host=PUBLIC_IP), PUBLIC_IP)
        self.assertEqual(m.public_address(override=None, ssh_host="box.example.test",
                                          resolver=resolver), "198.51.100.7")
        self.assertIsNone(m.public_address(override=None, ssh_host=TAILNET_IP))
        self.assertIsNone(m.public_address(override=None, ssh_host=None))
        with self.assertRaises(m.TailnetNameError) as caught:
            m.public_address(override="10.0.0.1", ssh_host=None)
        self.assertEqual(caught.exception.code, m.PUBLIC_ADDRESS_INVALID)

    def test_error_is_a_hosting_refusal_with_code(self):
        from sandbox.core._hosting import HostingRefusal
        error = m.TailnetNameError(m.BUSY, "x")
        self.assertIsInstance(error, HostingRefusal)
        self.assertEqual(error.code, m.BUSY)


class CapabilityTests(unittest.TestCase):
    def test_only_nginx_front_door_serves_tailnet_names(self):
        self.assertEqual(front_door.require_capability({"front_door": "nginx"},
                                                       front_door.TAILNET_NAMES), "nginx")
        with self.assertRaises(FrontDoorError) as caught:
            front_door.require_capability({}, front_door.TAILNET_NAMES)
        self.assertEqual(caught.exception.code, "front_door_capability_unavailable")


class RenderTests(unittest.TestCase):
    def block(self, http2=True):
        return render.render_block(m.TailnetName(FQDN, "example.test"),
                                   m.Upstream("127.0.0.1", 8025), TAILNET_IP,
                                   http2_directive=http2)

    def test_listens_only_on_the_tailnet_address(self):
        text = self.block()
        listens = [line.strip() for line in text.splitlines() if line.strip().startswith("listen")]
        self.assertEqual(listens, [f"listen {TAILNET_IP}:80;", f"listen {TAILNET_IP}:443 ssl;"])
        self.assertNotIn("default_server", text)
        self.assertIn("http2 on;", text)
        self.assertIn(f"return 308 https://{FQDN}$request_uri;", text)
        self.assertIn("proxy_pass http://127.0.0.1:8025;", text)
        for directive in ("proxy_http_version 1.1;", "proxy_set_header Upgrade $http_upgrade;",
                          'proxy_set_header Connection "upgrade";', "proxy_buffering off;",
                          "proxy_read_timeout 600s;", "proxy_set_header Host $host;",
                          "proxy_set_header X-Forwarded-Proto https;"):
            self.assertIn(directive, text)
        self.assertIn(f"ssl_certificate     /etc/nginx/sandbox-tailnet/{FQDN}/fullchain.pem;", text)
        self.assertIn(f"ssl_certificate_key /etc/nginx/sandbox-tailnet/{FQDN}/privkey.pem;", text)
        self.assertEqual(text.count(f'if ($host != "{FQDN}") {{ return 444; }}'), 2)

    def test_old_nginx_uses_listen_http2(self):
        text = self.block(http2=False)
        self.assertIn(f"listen {TAILNET_IP}:443 ssl http2;", text)
        self.assertNotIn("http2 on;", text)

    def test_header_round_trip_and_server_names(self):
        from sandbox.hosting.front_door import nginx
        text = self.block()
        self.assertEqual(render.parse_header(text),
                         {"fqdn": FQDN, "tailnet_ip": TAILNET_IP,
                          "upstream": "127.0.0.1:8025", "version": 1})
        self.assertIsNone(render.parse_header("server {}"))
        names = {n for _p, n in nginx.parse_server_names("# configuration file x:\n" + text)}
        self.assertEqual(names, {FQDN})
        self.assertIsNone(nginx.route_marker(text))

    def test_refuses_unsafe_tokens(self):
        with self.assertRaises(ValueError):
            render.render_block(m.TailnetName("a;b.example.test", "example.test"),
                                m.Upstream("127.0.0.1", 1), TAILNET_IP, http2_directive=True)


class ModelHardeningTests(unittest.TestCase):
    def test_config_takes_a_lego_digest_and_refuses_unknown_keys(self):
        digest = "0f" * 32
        self.assertEqual(config(lego_sha256=digest).lego_sha256, digest)
        for bad in ({"lego_sha256": "0F" * 32}, {"lego_sha256": "abc"}, {"token": "x"},
                    {"cloudflare_api_token": MARKER}):
            with self.subTest(bad=bad), self.assertRaises(m.TailnetNameError) as caught:
                config(**bad)
            self.assertEqual(caught.exception.code, m.CONFIG_INVALID)
            self.assertNotIn(MARKER, str(caught.exception))

    def test_record_parse_is_strict(self):
        good = m.TailnetNameRecord(FQDN, "example.test", "127.0.0.1:8025", TAILNET_IP,
                                   dns_record_id="c" * 32, lease_token="d" * 32).as_dict()
        self.assertEqual(m.TailnetNameRecord.parse(good).lease_token, "d" * 32)
        for key, value in (("fqdn", "app.other.test"), ("state", "gone"),
                           ("upstream", "203.0.113.5:80"), ("tailnet_ip", PUBLIC_IP),
                           ("dns_record_id", "../x"), ("lease_token", "short"),
                           ("cert_not_after", "tomorrow"), ("steps", {"dns": 1})):
            with self.subTest(key=key), self.assertRaises(m.TailnetNameError) as caught:
                m.TailnetNameRecord.parse(dict(good, **{key: value}))
            self.assertEqual(caught.exception.code, m.STATE_CORRUPT)

    def test_inventory_row_carries_ownership_and_activation(self):
        row = m.RemoteInventoryEntry.parse({"fqdn": FQDN, "block_present": True,
                                            "block_managed": False, "dir_unsafe": True,
                                            "activation": "written"})
        self.assertEqual((row.block_managed, row.dir_unsafe, row.dir_present, row.activation),
                         (False, True, True, "written"))


# ---------------------------------------------------------------------------
# Broker: fixed use profiles and a pinned lego (F2)
# ---------------------------------------------------------------------------

class FakeSecrets:
    """Stands in for build_secret_service: records the profiles it is given."""

    def __init__(self, *, output="", exit_code=0, error=None, on_run=None):
        self.output, self.exit_code, self.error, self.on_run = output, exit_code, error, on_run
        self.built: list[dict] = []
        self.calls: list[dict] = []

    def __call__(self, extra_profiles):
        self.built.append(extra_profiles)
        owner = self

        class _Service:
            def use_profile(self, name, *, surface):
                profile = extra_profiles[name]
                seen = {"name": name, "surface": surface, "profile": profile}
                if name == "sandbox-tailnet-name-dns":
                    path = Path(profile["argv"][3])
                    seen["request"] = json.loads(path.read_text())
                    seen["request_mode"] = stat.S_IMODE(path.stat().st_mode)
                if name == "sandbox-tailnet-name-lego":
                    seen["csr"] = Path(profile["argv"][profile["argv"].index("--csr") + 1]).read_text()
                owner.calls.append(seen)
                if owner.error:
                    raise owner.error
                if owner.on_run:
                    owner.on_run(profile["argv"])
                return {"ok": True, "operation": "use",
                        "result": {"exit_code": owner.exit_code, "termination": "exited",
                                   "output": owner.output, "truncated": False}}
        return _Service()


def pinned(path="/opt/lego"):
    return lambda cfg: (path, "0f" * 32)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "tn" / "acme"

    def tearDown(self):
        self.tmp.cleanup()

    def broker(self, fake, pin=None):
        return Broker(fake, acme_root=self.root, pin=pin or pinned())

    def test_dns_runs_the_fixed_profile_on_a_private_request_file(self):
        fake = FakeSecrets(output='noise\n{"result": "created", "record_id": "%s"}\n' % ("a" * 32))
        request = {"op": "ensure", "zone": "example.test", "name": FQDN, "ip": TAILNET_IP}
        result = self.broker(fake).dns(config(), request)
        self.assertEqual(result.result, "created")
        [call] = fake.calls
        profile = call["profile"]
        self.assertEqual((call["name"], call["surface"]), (broker_mod.DNS_PROFILE, "cli"))
        self.assertEqual((profile["source"], profile["key"], profile["destination"], profile["mcp"]),
                         ("personal", "EXAMPLE_CF_API_TOKEN", m.DNS_TOKEN_DESTINATION, False))
        self.assertEqual(profile["argv"][:2], [sys.executable, "-I"])
        self.assertTrue(profile["argv"][2].endswith("tailnet_names/cloudflare_child.py"))
        self.assertEqual((call["request"], call["request_mode"]), (request, 0o600))
        self.assertFalse(Path(profile["argv"][3]).exists(), "the request file is removed")
        self.assertEqual(len(profile["argv"]), 4, "per-call data never rides in argv")

    def test_broker_refusal_maps_without_detail_leak(self):
        fake = FakeSecrets(error=SecretBrokerError("key_missing", "secret key does not exist"))
        with self.assertRaises(m.TailnetNameError) as caught:
            self.broker(fake).dns(config(), {"op": "inspect"})
        self.assertEqual(caught.exception.code, m.SECRET_UNAVAILABLE)
        self.assertIn("key_missing", str(caught.exception))

    def test_dns_child_without_result_is_a_dns_failure(self):
        fake = FakeSecrets(output="Traceback\n", exit_code=1)
        with self.assertRaises(m.TailnetNameError) as caught:
            self.broker(fake).dns(config(), {"op": "inspect"})
        self.assertEqual(caught.exception.code, m.DNS_FAILED)

    def test_issue_runs_the_pinned_lego_with_a_fixed_argv(self):
        stale = self.root / "certificates" / (FQDN + ".crt")
        stale.parent.mkdir(parents=True)
        stale.write_text("stale")

        def on_run(argv):
            self.assertFalse(stale.exists(), "old output must be cleared before lego runs")
            stale.write_text("-----BEGIN CERTIFICATE-----\nnew\n-----END CERTIFICATE-----\n")
        fake = FakeSecrets(on_run=on_run)
        csr = "-----BEGIN CERTIFICATE REQUEST-----\nx\n"
        chain = self.broker(fake).issue(config(), FQDN, csr, "/opt/lego")
        self.assertIn("new", chain)
        [call] = fake.calls
        self.assertEqual(call["name"], broker_mod.LEGO_PROFILE)
        self.assertEqual(call["profile"]["argv"], [
            "/opt/lego", "run", "--accept-tos", "--email", "ops@example.test",
            "--server", m.ACME_SERVERS["letsencrypt"], "--dns", "cloudflare",
            "--csr", str(self.root / "csr" / "request.csr"), "--path", str(self.root)])
        self.assertEqual(call["csr"], csr)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(self.broker(fake).cached_chain(FQDN), chain)

    def test_issue_refuses_when_the_pin_changed(self):
        fake = FakeSecrets()
        with self.assertRaises(m.TailnetNameError) as caught:
            self.broker(fake, pin=pinned("/opt/other")).issue(config(), FQDN, "csr", "/opt/lego")
        self.assertEqual(caught.exception.code, m.ACME_CLIENT_UNTRUSTED)
        self.assertEqual(fake.calls, [])

    def test_issue_failure_reports_a_bounded_tail(self):
        output = "\n".join(f"line {i}" for i in range(50))
        fake = FakeSecrets(output=output, exit_code=1)
        with self.assertRaises(m.TailnetNameError) as caught:
            self.broker(fake).issue(config(), FQDN, "csr", "/opt/lego")
        self.assertEqual(caught.exception.code, m.CERTIFICATE_FAILED)
        self.assertIn("line 49", str(caught.exception))
        self.assertNotIn("line 40", str(caught.exception))


class LegoPinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "bin"
        self.dir.mkdir()
        self.tool = self.dir / "lego"
        self.tool.write_text("#!/bin/sh\necho 'lego version 5.1.0 linux/amd64'\n")
        self.tool.chmod(0o755)
        self.digest = hashlib.sha256(self.tool.read_bytes()).hexdigest()
        self.probes: list[dict] = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, version="lego version 5.1.0 linux/amd64", rc=0):
        def run(argv, **kwargs):
            self.probes.append({"argv": argv, **kwargs})
            return SimpleNamespace(returncode=rc, stdout=version + "\n", stderr="")
        return run

    def pin(self, runner=None, **cfg):
        with mock.patch.object(broker_mod, "_check_ownership"):
            return broker_mod.pin_lego(config(lego=str(self.tool), **cfg),
                                       runner=runner or self.runner())

    def test_missing_lego_refuses(self):
        with self.assertRaises(m.TailnetNameError) as caught:
            broker_mod.pin_lego(config(lego="/nonexistent/lego"))
        self.assertEqual(caught.exception.code, m.ACME_CLIENT_MISSING)

    def test_an_arbitrary_executable_is_not_trusted_without_a_digest(self):
        with self.assertRaises(m.TailnetNameError) as caught:
            self.pin()
        self.assertEqual(caught.exception.code, m.ACME_CLIENT_UNTRUSTED)
        self.assertEqual(self.probes, [], "nothing untrusted is executed")

    def test_a_recorded_digest_trusts_it_and_a_mismatch_does_not(self):
        real, digest = self.pin(lego_sha256=self.digest)
        self.assertEqual((real, digest), (os.path.realpath(self.tool), self.digest))
        [probe] = self.probes
        self.assertEqual(probe["argv"], [real, "--version"])
        self.assertEqual(probe["env"], {"PATH": "/usr/bin:/bin"}, "the probe never sees the token")
        self.assertEqual(probe["stdin"], subprocess.DEVNULL)
        with self.assertRaises(m.TailnetNameError) as caught:
            self.pin(lego_sha256="0f" * 32)
        self.assertEqual(caught.exception.code, m.ACME_CLIENT_UNTRUSTED)

    def test_the_version_probe_must_report_lego_5(self):
        for runner in (self.runner("lego version 4.21.0"), self.runner("hello"),
                       self.runner(rc=1)):
            with self.subTest(), self.assertRaises(m.TailnetNameError) as caught:
                self.pin(runner=runner, lego_sha256=self.digest)
            self.assertEqual(caught.exception.code, m.ACME_CLIENT_UNTRUSTED)

    def test_a_symlink_is_resolved_before_any_check(self):
        link = Path(self.tmp.name) / "lego-link"
        link.symlink_to(self.tool)
        with mock.patch.object(broker_mod, "_check_ownership") as checked:
            broker_mod.pin_lego(config(lego=str(link), lego_sha256=self.digest),
                                runner=self.runner())
        self.assertEqual(checked.call_args[0][0], Path(os.path.realpath(self.tool)))

    def test_ownership_refuses_writable_or_foreign_paths(self):
        os.chmod(self.dir, 0o777)
        with self.assertRaises(m.TailnetNameError) as caught:
            broker_mod._check_ownership(self.tool, os.getuid())
        self.assertEqual(caught.exception.code, m.ACME_CLIENT_UNTRUSTED)
        os.chmod(self.dir, 0o755)
        if os.getuid() != 0:
            with self.assertRaises(m.TailnetNameError):
                broker_mod._check_ownership(self.tool, os.getuid() + 1)


class SecretFactoryTests(unittest.TestCase):
    def test_profiles_are_injected_before_the_service_is_built(self):
        from sandbox.commands import host_tailnet_name as cli
        descriptor = {"secrets": {"sources": {"personal": {"type": "file"}},
                                  "useProfiles": {"deploy": {"source": "personal"}}}}
        built = {}

        def build(**kwargs):
            built.update(kwargs)
            return "service"
        with mock.patch("sandbox.secrets.service.load_secret_project_descriptor",
                        return_value=descriptor), \
                mock.patch("sandbox.secrets.context.build_secret_service", side_effect=build):
            factory = cli.secret_service_factory("/tmp")
            self.assertEqual(factory({broker_mod.DNS_PROFILE: {"argv": ["x"]}}), "service")
            profiles = built["config"]["secrets"]["useProfiles"]
            self.assertEqual(set(profiles), {"deploy", broker_mod.DNS_PROFILE})
            self.assertEqual(set(descriptor["secrets"]["useProfiles"]), {"deploy"},
                             "the project's descriptor is not mutated")
            descriptor["secrets"]["useProfiles"][broker_mod.LEGO_PROFILE] = {}
            with self.assertRaises(m.TailnetNameError) as caught:
                factory({broker_mod.LEGO_PROFILE: {"argv": ["x"]}})
            self.assertEqual(caught.exception.code, m.SECRET_UNAVAILABLE)


class StateTests(unittest.TestCase):
    def test_put_load_drop_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = state.StateStore(Path(tmp), "r1")
            self.assertEqual(store.load(), {})
            record = m.TailnetNameRecord(FQDN, "example.test", "127.0.0.1:8025", TAILNET_IP)
            store.put(record)
            self.assertEqual(store.load()[FQDN].upstream, "127.0.0.1:8025")
            path = Path(tmp) / "r1.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            store.drop(FQDN)
            self.assertEqual(store.load(), {})

    def test_corrupt_state_fails_closed(self):
        record = m.TailnetNameRecord(FQDN, "example.test", "127.0.0.1:8025", TAILNET_IP).as_dict()
        with tempfile.TemporaryDirectory() as tmp:
            store = state.StateStore(Path(tmp), "r1")
            path = Path(tmp) / "r1.json"
            for content in ("not json", json.dumps({"version": 2, "names": {}}),
                            json.dumps({"version": 1, "names": []}),
                            json.dumps({"version": 1, "names": {"bad": {"fqdn": 3}, FQDN: record}}),
                            json.dumps({"version": 1, "names": {"other.example.test": record}})):
                with self.subTest(content=content[:40]):
                    path.write_text(content)
                    with self.assertRaises(m.TailnetNameError) as caught:
                        store.load()
                    self.assertEqual(caught.exception.code, m.STATE_CORRUPT)

    def test_lock_is_exclusive_and_non_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = state.StateStore(Path(tmp), "r1")
            with store.lock():
                with self.assertRaises(m.TailnetNameError) as caught:
                    with state.StateStore(Path(tmp), "r1").lock():
                        pass
            self.assertEqual(caught.exception.code, m.BUSY)
            with store.lock():
                pass

    def test_remote_name_is_validated(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            state.StateStore(Path(tmp), "../x")


# ---------------------------------------------------------------------------
# Certificates and probes (F1)
# ---------------------------------------------------------------------------

def _openssl(*args, cwd):
    from tests.subprocess_support import run_test_process
    res = run_test_process(["openssl", *args], cwd=cwd, capture_output=True, timeout=60)
    if res.returncode != 0:
        raise AssertionError(res.stderr)


_CERTS: dict = {}


def make_cert(name: str, *, san: str | None = None, days: int = 90) -> tuple[str, str, str]:
    """(cert path, key path, leaf sha256) of a self-signed certificate."""
    key = (name, san, days)
    if key not in _CERTS:
        d = tempfile.mkdtemp()
        args = ["req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                "-nodes", "-keyout", "k.pem", "-out", "c.pem", "-days", str(days),
                "-subj", "/CN=" + name]
        if san:
            args += ["-addext", "subjectAltName=" + san]
        _openssl(*args, cwd=d)
        cert = os.path.join(d, "c.pem")
        der = ssl.PEM_cert_to_DER_cert(Path(cert).read_text())
        _CERTS[key] = (cert, os.path.join(d, "k.pem"), hashlib.sha256(der).hexdigest())
    return _CERTS[key]


class _TlsServer:
    """One-shot local TLS server answering a fixed HTTP status."""

    def __init__(self, cert, key, status=200):
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert, key)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.status = status
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
            with self.context.wrap_socket(conn, server_side=True) as tls:
                tls.recv(4096)
                tls.sendall(f"HTTP/1.1 {self.status} X\r\nContent-Length: 0\r\n\r\n".encode())
        except (OSError, ssl.SSLError):
            pass

    def close(self):
        self.sock.close()
        self.thread.join(5)


class X509Tests(unittest.TestCase):
    def test_parse_leaf_reads_san_cn_and_expiry(self):
        cert, _key, _sha = make_cert(FQDN, san="DNS:%s,DNS:*.lab.example.test" % FQDN, days=30)
        leaf = x509.parse_leaf(x509.first_der(Path(cert).read_text()))
        self.assertEqual(set(leaf.names), {FQDN, "*.lab.example.test"})
        days = (leaf.not_after - dt.datetime.now(dt.timezone.utc)).days
        self.assertIn(days, (28, 29, 30))
        cn_only, _k, _s = make_cert("cn.example.test")
        self.assertIn("cn.example.test",
                      x509.parse_leaf(x509.first_der(Path(cn_only).read_text())).names)
        for bad in (b"", b"\x30\x03\x02\x01", b"garbage"):
            with self.assertRaises(ValueError):
                x509.parse_leaf(bad)

    def test_covers_exact_and_single_label_wildcards(self):
        self.assertTrue(x509.covers((FQDN,), FQDN))
        self.assertTrue(x509.covers(("*.example.test",), FQDN))
        self.assertFalse(x509.covers(("*.example.test",), "a.b.example.test"))
        self.assertFalse(x509.covers(("*.example.test",), "example.test"))
        self.assertFalse(x509.covers(("other.example.test",), FQDN))
        self.assertTrue(x509.covers(("APP.Example.Test",), FQDN))


class VerifyTests(unittest.TestCase):
    def probe(self, cert, status=200, send_request=True):
        server = _TlsServer(cert[0], cert[1], status)
        try:
            return verify.probe("127.0.0.1", FQDN, port=server.port, timeout=5,
                                send_request=send_request)
        finally:
            server.close()

    def test_tailnet_probe_matches_fingerprint_and_status(self):
        ours = make_cert(FQDN, san="DNS:" + FQDN)
        result = self.probe(ours)
        self.assertEqual((result.category, result.leaf_sha256, result.http_status),
                         ("handshake", ours[2], 200))
        self.assertEqual(result.leaf_names, (FQDN,))
        self.assertEqual(verify.tailnet_outcome(result, ours[2]).status, "done")
        self.assertEqual(verify.tailnet_outcome(result, "0" * 64).code, m.TAILNET_PROBE_FAILED)
        bad = self.probe(ours, status=502)
        self.assertEqual(verify.tailnet_outcome(bad, ours[2]).code, m.TAILNET_PROBE_FAILED)

    def test_public_probe_fails_on_any_certificate_valid_for_the_name(self):
        ours = make_cert(FQDN, san="DNS:" + FQDN)
        verdict = verify.public_outcome(self.probe(ours, send_request=False), ours[2], FQDN)
        self.assertEqual((verdict.outcome.code, verdict.exposed, verdict.ours),
                         (m.PUBLIC_EXPOSED, True, True))
        for other in (make_cert("x.example.test", san="DNS:" + FQDN),
                      make_cert("w.example.test", san="DNS:*.example.test")):
            with self.subTest(other=other[2][:8]):
                verdict = verify.public_outcome(self.probe(other, send_request=False), ours[2],
                                                FQDN)
                self.assertEqual((verdict.outcome.code, verdict.exposed, verdict.ours),
                                 (m.PUBLIC_EXPOSED, True, False))
        unrelated = make_cert("other.example.test", san="DNS:other.example.test")
        verdict = verify.public_outcome(self.probe(unrelated, send_request=False), ours[2], FQDN)
        self.assertEqual((verdict.outcome.status, verdict.exposed), ("done", False))

    def test_a_refused_connection_passes_and_a_timeout_does_not(self):
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        refused = verify.probe("127.0.0.1", FQDN, port=port, timeout=2, send_request=False)
        self.assertEqual(refused.category, "refused")
        verdict = verify.public_outcome(refused, "0" * 64, FQDN)
        self.assertEqual((verdict.outcome.status, verdict.exposed), ("done", False))
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)  # accepts TCP, never answers TLS
        try:
            stalled = verify.probe("127.0.0.1", FQDN, port=silent.getsockname()[1], timeout=1,
                                   send_request=False)
        finally:
            silent.close()
        self.assertEqual(stalled.category, "timeout")
        verdict = verify.public_outcome(stalled, "0" * 64, FQDN)
        self.assertEqual((verdict.outcome.status, verdict.outcome.code),
                         ("failed", m.PUBLIC_PROBE_INCONCLUSIVE))

    def test_categories_are_distinct_and_only_refusals_pass(self):
        self.assertEqual(verify._connect_category(ConnectionRefusedError()), "refused")
        self.assertEqual(verify._connect_category(ConnectionResetError()), "reset")
        self.assertEqual(verify._connect_category(socket.timeout()), "timeout")
        self.assertEqual(verify._connect_category(socket.gaierror()), "dns")
        self.assertEqual(verify._connect_category(OSError(errno.EHOSTUNREACH, "x")), "unreachable")
        self.assertEqual(verify._connect_category(OSError(errno.EACCES, "x")), "error")
        self.assertEqual(verify._tls_category(ssl.SSLZeroReturnError()), "closed")
        alert = ssl.SSLError(1, "[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error")
        self.assertEqual(verify._tls_category(alert), "tls_alert")
        self.assertEqual(verify._tls_category(ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER]")),
                         "tls_error")
        for category in ("refused", "reset", "closed", "tls_alert"):
            verdict = verify.public_outcome(verify.ProbeResult(category), None, FQDN)
            self.assertEqual(verdict.outcome.status, "done", category)
        for category in ("timeout", "unreachable", "dns", "tls_error", "error"):
            verdict = verify.public_outcome(verify.ProbeResult(category), None, FQDN)
            self.assertEqual((verdict.outcome.status, verdict.outcome.code),
                             ("failed", m.PUBLIC_PROBE_INCONCLUSIVE), category)
        undecodable = verify.ProbeResult("handshake", "ee" * 32, None)
        self.assertEqual(verify.public_outcome(undecodable, "ab" * 32, FQDN).outcome.code,
                         m.PUBLIC_PROBE_INCONCLUSIVE)


# ---------------------------------------------------------------------------
# Service flows over fakes
# ---------------------------------------------------------------------------

FUTURE = "2027-02-08T00:00:00Z"
SOON = "2026-11-20T00:00:00Z"
CHAIN = "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FakeRemote:
    """The remote program (leases, ownership, journal) in memory."""

    def __init__(self):
        self.blocks: dict[str, str] = {}
        self.chains: dict[str, dict] = {}
        self.keys: set[str] = set()
        self.activation: dict[str, str] = {}
        self.leases: dict[str, str] = {}
        self.foreign_block: set[str] = set()
        self.foreign_dir: set[str] = set()
        self.unsafe_dir: set[str] = set()
        self.mismatch: set[str] = set()
        self.locks: list[str] = []
        self.bound = True
        self.calls: list[tuple[str, dict]] = []
        self.reloads = 0
        self.commit_error: str | None = None
        self.remove_error: str | None = None
        self.preflight_error: FrontDoorError | None = None

    def actions(self, *names):
        return [action for action, _ in self.calls if not names or action in names]

    def payloads(self, action):
        return [payload for name, payload in self.calls if name == action]

    def call(self, entry, action, payload):
        self.calls.append((action, json.loads(json.dumps(payload))))
        fqdn = payload.get("fqdn")
        if action == "inspect":
            names = [fqdn] if fqdn else sorted(set(self.blocks) | set(self.keys)
                                               | self.foreign_block | self.foreign_dir)
            return {"ok": True, "tailnet_ip_bound": self.bound, "locks_held": list(self.locks),
                    "inventory": [self._row(n) for n in names]}
        if action == "reserve":
            holder = self.leases.get(fqdn)
            if holder and holder != payload["token"]:
                return {"ok": False, "code": "busy", "detail": "another run holds this name"}
            self.leases[fqdn] = payload["token"]
            return {"ok": True}
        if action == "release":
            if self.leases.get(fqdn) == payload["token"]:
                del self.leases[fqdn]
            return {"ok": True}
        if self.leases.get(fqdn) != payload.get("token"):
            return {"ok": False, "code": "lease_lost"}
        if action == "csr":
            if fqdn in self.foreign_dir:
                return {"ok": False, "code": "unmanaged"}
            self.keys.add(fqdn)
            return {"ok": True, "csr": "-----BEGIN CERTIFICATE REQUEST-----\n"}
        if action == "commit":
            assert payload.get("jobs_probe") == {"home": "/home/u/sandbox"}, payload
            if self.commit_error:
                return {"ok": False, "code": self.commit_error, "detail": "refused"}
            chain = payload.get("chain") or (self.chains.get(fqdn) or {}).get("pem")
            if chain in self.mismatch:
                return {"ok": False, "code": "certificate_mismatch"}
            block = payload.get("block") or self.blocks.get(fqdn)
            same = (self.activation.get(fqdn) == "loaded" and self.blocks.get(fqdn) == block
                    and (self.chains.get(fqdn) or {}).get("pem") == chain)
            if not same:
                self.blocks[fqdn] = block
                if payload.get("chain"):
                    self.chains[fqdn] = {"pem": chain, "not_after": FUTURE, "sha": sha(chain)}
                self.activation[fqdn] = "loaded"
                self.reloads += 1
            current = self.chains[fqdn]
            return {"ok": True, "changed": not same, "settled": None,
                    "not_after": current["not_after"], "sha256": current["sha"],
                    "generation": self.generation(fqdn)}
        if action == "remove":
            if self.remove_error:
                return {"ok": False, "code": self.remove_error}
            if fqdn in self.foreign_block or fqdn in self.foreign_dir:
                return {"ok": False, "code": "unmanaged"}
            removed = self.blocks.pop(fqdn, None) is not None
            if removed:
                self.reloads += 1
            if payload.get("keep_directory"):
                return {"ok": True, "block_removed": removed, "directory_removed": False}
            had_dir = fqdn in self.keys
            self.keys.discard(fqdn)
            self.chains.pop(fqdn, None)
            self.activation.pop(fqdn, None)
            return {"ok": True, "block_removed": removed, "directory_removed": had_dir}
        raise AssertionError(action)

    def generation(self, fqdn):
        return sha((self.blocks.get(fqdn) or "") + "\0" + ((self.chains.get(fqdn) or {}).get("pem") or ""))

    def served(self, fqdn):
        return (self.chains.get(fqdn) or {}).get("sha") if fqdn in self.blocks else None

    def _row(self, fqdn):
        block = self.blocks.get(fqdn)
        chain = self.chains.get(fqdn)
        row = {"fqdn": fqdn, "block_present": block is not None or fqdn in self.foreign_block,
               "block_managed": block is not None and fqdn not in self.foreign_block,
               "header": render.parse_header(block) if block else None,
               "dir_present": fqdn in self.keys or fqdn in self.foreign_dir,
               "dir_managed": fqdn in self.keys and fqdn not in self.foreign_dir,
               "dir_unsafe": fqdn in self.unsafe_dir,
               "cert_present": chain is not None, "key_present": fqdn in self.keys,
               "cert_not_after": chain and chain["not_after"], "cert_sha256": chain and chain["sha"],
               "cert_names": [fqdn] if chain else [], "key_matches": True if chain else None,
               "activation": self.activation.get(fqdn),
               "generation": self.generation(fqdn) if self.activation.get(fqdn) == "loaded"
               else None}
        return row

    def preflight(self, entry, declared, own_file):
        self.calls.append(("preflight", {"declared": list(declared), "own_file": own_file}))
        if self.preflight_error:
            raise self.preflight_error
        return {"version": [1, 26, 0], "realip": True}


class FakeDns:
    def __init__(self):
        self.records: dict[str, dict] = {}
        self.requests: list[dict] = []
        self.fail = False

    def __call__(self, cfg, request):
        self.requests.append(dict(request))
        if self.fail:
            return m.DnsResult("error", code="unreachable")
        row = self.records.get(request["name"])
        op = request["op"]
        if op in ("ensure", "inspect"):
            if row is None:
                if op == "inspect":
                    return m.DnsResult("absent")
                row = {"id": "c" * 32, "content": request["ip"], "proxied": False}
                self.records[request["name"]] = row
                return m.DnsResult("created", row["id"], row["content"], False)
            if row["content"] == request.get("ip", row["content"]) and not row["proxied"]:
                return m.DnsResult("exists", row["id"], row["content"], False)
            return m.DnsResult("conflict", row["id"], row["content"], row["proxied"])
        if op == "delete":
            if row is None:
                return m.DnsResult("absent")
            if row["id"] != request["record_id"] or row["content"] != request["ip"] or row["proxied"]:
                return m.DnsResult("conflict", row["id"], row["content"], row["proxied"],
                                   code="record_changed")
            del self.records[request["name"]]
            return m.DnsResult("deleted", row["id"])
        raise AssertionError(op)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = FakeRemote()
        self.dns = FakeDns()
        self.issued: list[tuple] = []
        self.cached: str | None = None
        self.jobs_active = False
        self.jobs_error = False
        self.public: verify.ProbeResult | None = verify.ProbeResult("refused")
        self.public_after_removal: verify.ProbeResult | None = None
        self.tailnet_status = 200
        self.sleeps: list[float] = []
        self.lego_error: m.TailnetNameError | None = None
        self.tokens = iter(f"{i:032x}" for i in range(1, 1000))
        self.store = state.StateStore(Path(self.tmp.name), "r1")
        self.deps = service.Deps(
            remote_call=self.remote.call, preflight=self.remote.preflight, dns=self.dns,
            issue=self._issue, cached_chain=lambda fqdn: self.cached, find_lego=self._lego,
            active_jobs=self._jobs, jobs_probe=lambda entry: {"home": "/home/u/sandbox"},
            probe=self._probe, store=lambda remote: self.store, now=lambda: NOW,
            new_token=lambda: next(self.tokens), sleep=self.sleeps.append)
        self.ctx = service.Context(config(), target())
        self.name = m.TailnetName(FQDN, "example.test")
        self.upstream = m.Upstream("127.0.0.1", 8025)

    def tearDown(self):
        self.tmp.cleanup()

    def _lego(self, cfg):
        if self.lego_error:
            raise self.lego_error
        return "/opt/lego"

    def _issue(self, cfg, fqdn, csr, lego):
        self.issued.append((fqdn, lego))
        return CHAIN.replace("leaf", f"leaf{len(self.issued)}")

    def _jobs(self, remote):
        if self.jobs_error:
            raise RuntimeError("unreachable")
        return self.jobs_active

    def _probe(self, address, fqdn, *, send_request):
        if address == TAILNET_IP:
            return verify.ProbeResult("handshake", self.remote.served(fqdn), (fqdn,),
                                      self.tailnet_status)
        if self.public_after_removal is not None and self.remote.actions("remove"):
            return self.public_after_removal
        return self.public

    def exposed(self, ours=True):
        leaf = self.remote.served(FQDN) if ours else "ee" * 32
        return verify.ProbeResult("handshake", leaf, (FQDN,))

    def add(self, **kw):
        return service.add(self.ctx, self.deps, self.name, self.upstream, **kw)

    def steps(self, result):
        return [(s.step, s.status) for s in result.steps]


class AddTests(ServiceTestCase):
    def test_full_add_in_order(self):
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.steps(result), [
            ("preflight", "done"), ("gate", "done"), ("dns", "done"), ("certificate", "done"),
            ("activate", "done"), ("verify_tailnet", "done"), ("verify_public", "done")])
        self.assertEqual(self.dns.records[FQDN]["content"], TAILNET_IP)
        self.assertEqual(self.issued, [(FQDN, "/opt/lego")])
        self.assertEqual(self.remote.actions("reserve", "csr", "commit", "release"),
                         ["reserve", "csr", "commit", "release"])
        self.assertIn(f"listen {TAILNET_IP}:443 ssl;", self.remote.blocks[FQDN])
        record = self.store.load()[FQDN]
        self.assertEqual((record.state, record.upstream, record.dns_record_id, record.dns_adopted),
                         ("published", "127.0.0.1:8025", "c" * 32, False))
        self.assertEqual(record.cert_sha256, self.remote.served(FQDN))
        self.assertEqual(record.lease_token, f"{1:032x}")
        self.assertEqual(self.remote.leases, {}, "the lease is released")

    def test_rerun_skips_and_reuses_the_lease_token(self):
        self.add()
        reloads = self.remote.reloads
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.steps(result), [
            ("preflight", "done"), ("gate", "done"), ("dns", "skipped"),
            ("certificate", "skipped"), ("activate", "skipped"), ("verify_tailnet", "done"),
            ("verify_public", "done")])
        self.assertEqual(len(self.issued), 1)
        self.assertEqual(self.remote.reloads, reloads)
        tokens = {p["token"] for p in self.remote.payloads("reserve")}
        self.assertEqual(tokens, {f"{1:032x}"})

    def test_an_unproven_activation_is_reconciled_not_skipped(self):
        """F8: a block on disk without a loaded journal is tested and reloaded."""
        self.add()
        reloads = self.remote.reloads
        self.remote.activation[FQDN] = "written"
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertIn(("activate", "done"), self.steps(result))
        self.assertEqual(self.remote.reloads, reloads + 1)

    def test_identical_existing_record_is_adopted(self):
        self.dns.records[FQDN] = {"id": "d" * 32, "content": TAILNET_IP, "proxied": False}
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertTrue(self.store.load()[FQDN].dns_adopted)

    def test_conflicting_record_refuses_before_any_certificate_work(self):
        self.dns.records[FQDN] = {"id": "d" * 32, "content": "100.64.0.99", "proxied": False}
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.DNS_CONFLICT))
        self.assertEqual(self.remote.actions("csr", "commit"), [])
        self.assertEqual(self.issued, [])

    def test_a_live_lease_held_by_another_run_refuses(self):
        """F7: two controllers on one name serialize through the remote lease."""
        self.remote.leases[FQDN] = "f" * 32
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.BUSY))
        self.assertEqual(self.dns.requests, [])
        self.assertEqual(self.remote.leases[FQDN], "f" * 32, "another run's lease is kept")

    def test_a_lost_lease_stops_the_run(self):
        original = self.remote.call

        def steal(entry, action, payload):
            if action == "commit":
                self.remote.leases[FQDN] = "f" * 32
            return original(entry, action, payload)
        self.deps.remote_call = steal
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.BUSY))
        self.assertNotIn(FQDN, self.remote.blocks)

    def test_remote_gate_and_nginx_refusals_map_to_codes(self):
        for remote_code, code in (("gate_unavailable", m.GATE_UNAVAILABLE), ("busy", m.BUSY),
                                  ("nginx_rejected", m.NGINX_REJECTED),
                                  ("reload_failed", m.RELOAD_FAILED),
                                  ("unmanaged", m.UNMANAGED),
                                  ("path_unsafe", m.REMOTE_PATH_UNSAFE)):
            with self.subTest(remote_code=remote_code):
                self.remote.commit_error = remote_code
                result = self.add()
                self.assertEqual((result.ok, result.code), (False, code))
                self.assertEqual(self.store.load()[FQDN].steps["activate"], "failed")

    def test_busy_gate_refuses_without_waiting(self):
        self.remote.locks = ["/run/lock/sandbox-docker-pool.lock"]
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.BUSY))
        self.assertEqual(self.dns.requests, [])
        self.remote.locks = []
        self.jobs_active = True
        self.assertEqual(self.add().code, m.BUSY)
        self.jobs_active, self.jobs_error = False, True
        self.assertEqual(self.add().code, m.GATE_UNAVAILABLE)
        self.assertEqual(self.remote.actions("reserve"), [])

    def test_foreign_or_unsafe_remote_paths_refuse_before_dns(self):
        """F4: Sandbox never adopts a block or directory it did not write."""
        for attr, code in (("foreign_block", m.UNMANAGED), ("foreign_dir", m.UNMANAGED),
                           ("unsafe_dir", m.REMOTE_PATH_UNSAFE)):
            with self.subTest(attr=attr):
                remote = FakeRemote()
                getattr(remote, attr).add(FQDN)
                self.deps.remote_call = remote.call
                result = self.add()
                self.assertEqual((result.ok, result.code), (False, code))
                self.assertEqual(remote.actions("reserve", "csr", "commit"), [])
        self.assertEqual(self.dns.requests, [])

    def test_unbound_tailnet_address_and_preflight_conflict_refuse(self):
        self.remote.bound = False
        self.assertEqual(self.add().code, m.TAILNET_ADDRESS_UNBOUND)
        self.remote.bound = True
        self.remote.preflight_error = FrontDoorError("front_door_conflict", "name served elsewhere")
        self.assertEqual(self.add().code, "front_door_conflict")
        self.assertEqual(self.dns.requests, [])

    def test_upstream_differs_needs_replace(self):
        self.add()
        self.upstream = m.Upstream("127.0.0.1", 9000)
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.UPSTREAM_DIFFERS))
        result = self.add(replace_upstream=True)
        self.assertTrue(result.ok, result.envelope())
        self.assertIn("127.0.0.1:9000", self.remote.blocks[FQDN])

    def test_public_exposure_removes_only_the_block(self):
        self.public = None
        self.deps.probe = lambda address, fqdn, *, send_request: (
            self._probe(address, fqdn, send_request=send_request) if address == TAILNET_IP
            else self.exposed() if FQDN in self.remote.blocks else verify.ProbeResult("refused"))
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.PUBLIC_EXPOSED))
        [removal] = self.remote.payloads("remove")
        self.assertTrue(removal["keep_directory"])
        self.assertNotIn(FQDN, self.remote.blocks)
        self.assertIn(FQDN, self.remote.keys)
        self.assertIn("no longer served", result.error)
        self.assertEqual(self.store.load()[FQDN].steps["activate"], "removed")

    def test_another_certificate_for_the_name_is_exposure(self):
        """F1: any public leaf valid for the name fails, not only ours."""
        self.public = self.exposed(ours=False)
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.PUBLIC_EXPOSED))
        self.assertIn("something else serves it", result.error)

    def test_exposure_with_failed_removal_is_recorded_as_such(self):
        self.public = self.exposed(ours=False)
        self.remote.remove_error = "nginx_rejected"
        result = self.add()
        self.assertEqual(result.code, m.PUBLIC_EXPOSED)
        self.assertIn("FAILED", result.error)
        self.assertEqual(self.store.load()[FQDN].steps["activate"], "remove_failed")

    def test_an_inconclusive_public_probe_fails_and_keeps_the_block(self):
        """F1: a timeout is not a refusal."""
        for category in ("timeout", "unreachable", "dns", "error"):
            with self.subTest(category=category):
                self.public = verify.ProbeResult(category)
                result = self.add()
                self.assertEqual((result.ok, result.code), (False, m.PUBLIC_PROBE_INCONCLUSIVE))
                self.assertIn(FQDN, self.remote.blocks)
                self.assertEqual(self.store.load()[FQDN].state, "publishing")
        self.assertEqual(self.remote.actions("remove"), [])

    def test_a_certificate_not_for_the_name_passes(self):
        self.public = verify.ProbeResult("handshake", "ee" * 32, ("other.example.test",))
        self.assertTrue(self.add().ok)

    def test_tailnet_probe_retries_then_fails_and_rerun_repeats_verify(self):
        self.tailnet_status = 502
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.TAILNET_PROBE_FAILED))
        self.assertEqual(len(self.sleeps), service.TAILNET_ATTEMPTS - 1)
        self.assertEqual(self.store.load()[FQDN].state, "publishing")
        self.tailnet_status = 200
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.store.load()[FQDN].state, "published")

    def test_no_public_address_is_not_ok(self):
        self.ctx = service.Context(config(), target(public_ip=None))
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.PUBLIC_PROBE_UNAVAILABLE))

    def test_interruption_then_rerun_finishes(self):
        self.dns.fail = True
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.DNS_FAILED))
        self.assertEqual(self.store.load()[FQDN].steps["dns"], "failed")
        self.assertEqual(self.remote.leases, {})
        self.dns.fail = False
        self.assertTrue(self.add().ok)

    def test_missing_or_untrusted_lego_refuses_first(self):
        for code in (m.ACME_CLIENT_MISSING, m.ACME_CLIENT_UNTRUSTED):
            with self.subTest(code=code):
                self.lego_error = m.TailnetNameError(code, "no")
                result = self.add()
                self.assertEqual((result.ok, result.code), (False, code))
        self.assertEqual(self.remote.calls, [])

    def test_a_cached_chain_is_reused_and_replaced_when_the_remote_rejects_it(self):
        cert, _key, _sha = make_cert(FQDN, san="DNS:" + FQDN)
        self.cached = Path(cert).read_text()
        self.deps.now = lambda: dt.datetime.now(dt.timezone.utc)
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.issued, [])
        self.remote = FakeRemote()
        self.deps.remote_call = self.remote.call
        self.remote.mismatch.add(self.cached)
        self.store.drop(FQDN)
        result = self.add()
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(len(self.issued), 1)

    def test_a_cached_chain_for_another_name_is_not_reused(self):
        cert, _key, _sha = make_cert("other.example.test", san="DNS:other.example.test")
        self.cached = Path(cert).read_text()
        self.deps.now = lambda: dt.datetime.now(dt.timezone.utc)
        self.assertTrue(self.add().ok)
        self.assertEqual(len(self.issued), 1)

    def test_a_stale_zone_refuses_before_any_api_call(self):
        """F6: today's zones are checked before any DNS, ACME or remote mutation."""
        self.add()
        mutations = len(self.remote.actions("reserve", "csr", "commit", "remove"))
        requests = len(self.dns.requests)
        self.ctx = service.Context(config(zones=["lab.example.test"]), target())
        for result in (self.add(), service.remove(self.ctx, self.deps, self.name),
                       service.renew(self.ctx, self.deps, self.name, force=True).results[0]):
            self.assertEqual((result.ok, result.code), (False, m.ZONE_NOT_ALLOWED))
        self.assertEqual(len(self.dns.requests), requests)
        self.assertEqual(len(self.remote.actions("reserve", "csr", "commit", "remove")),
                         mutations)
        self.assertEqual(len(self.issued), 1)

    def test_a_record_saved_under_another_zone_refuses(self):
        self.ctx = service.Context(config(zones=["example.test", "lab.example.test"]), target())
        self.name = m.TailnetName("a.lab.example.test", "lab.example.test")
        self.assertTrue(self.add().ok)
        path = Path(self.tmp.name) / "r1.json"
        raw = json.loads(path.read_text())
        raw["names"][self.name.fqdn]["zone"] = "example.test"
        path.write_text(json.dumps(raw))
        requests = len(self.dns.requests)
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.ZONE_NOT_ALLOWED))
        self.assertEqual(len(self.dns.requests), requests)
        self.assertIn(self.name.fqdn, self.remote.blocks)

    def test_corrupt_state_refuses_before_any_remote_call(self):
        (Path(self.tmp.name) / "r1.json").write_text("{broken")
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.STATE_CORRUPT))
        self.assertEqual(self.remote.actions("reserve", "csr", "commit"), [])
        self.assertEqual(self.dns.requests, [])


class ListRenewRemoveTests(ServiceTestCase):
    def test_list_merges_inventory_and_state_with_drift(self):
        self.add()
        self.remote.blocks["b.example.test"] = render.render_block(
            m.TailnetName("b.example.test", "example.test"), self.upstream, TAILNET_IP,
            http2_directive=True)
        self.remote.activation["b.example.test"] = "written"
        self.remote.foreign_block.add("c.example.test")
        names = {row["fqdn"]: row for row in service.list_names(self.ctx, self.deps).names}
        self.assertEqual(names[FQDN]["drift"], [])
        self.assertFalse(names[FQDN]["renewal_due"])
        self.assertIn("not_recorded", names["b.example.test"]["drift"])
        self.assertIn("activation_written", names["b.example.test"]["drift"])
        self.assertIn("unmanaged_block", names["c.example.test"]["drift"])
        ctx = service.Context(config(zones=["lab.example.test"]), target())
        names = {row["fqdn"]: row for row in service.list_names(ctx, self.deps).names}
        self.assertIn("zone_not_allowed", names[FQDN]["drift"])

    def test_renew_not_due_is_skipped_and_force_reissues(self):
        self.add()
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertEqual(result.results[0].steps[0].code, m.NOT_DUE)
        self.assertTrue(result.ok)
        result = service.renew(self.ctx, self.deps, self.name, force=True)
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(len(self.issued), 2)
        [commit] = self.remote.payloads("commit")[-1:]
        self.assertIsNone(commit["block"])

    def test_renew_reconciles_an_unproven_activation(self):
        """F8: renew never skips a name whose loaded state is not proven."""
        self.add()
        reloads = self.remote.reloads
        for state_ in ("tested", None):
            with self.subTest(activation=state_):
                if state_ is None:
                    self.remote.activation.pop(FQDN)
                else:
                    self.remote.activation[FQDN] = state_
                result = service.renew(self.ctx, self.deps, self.name)
                self.assertTrue(result.ok, result.envelope())
                self.assertEqual(self.remote.activation[FQDN], "loaded")
        self.assertEqual(self.remote.reloads, reloads + 2)
        self.assertEqual(len(self.issued), 1, "a current certificate is not reissued")

    def test_renew_all_renews_only_due_names_and_reports_disallowed_zones(self):
        self.add()
        self.remote.chains[FQDN]["not_after"] = SOON
        other = m.TailnetName("b.example.test", "example.test")
        service.add(self.ctx, self.deps, other, self.upstream)
        self.remote.blocks["x.other.test"] = render.render_block(
            m.TailnetName("x.other.test", "other.test"), self.upstream, TAILNET_IP,
            http2_directive=True)
        issued = len(self.issued)
        result = service.renew(self.ctx, self.deps, None, all_names=True)
        by_name = {r.fqdn: r for r in result.results}
        self.assertEqual(by_name["x.other.test"].code, m.ZONE_NOT_ALLOWED)
        self.assertEqual(by_name["b.example.test"].steps[0].code, m.NOT_DUE)
        self.assertTrue(by_name[FQDN].ok, by_name[FQDN].envelope())
        self.assertEqual(len(self.issued), issued + 1)

    def test_renew_commit_failure_is_reported(self):
        self.add()
        self.remote.commit_error = "nginx_rejected"
        result = service.renew(self.ctx, self.deps, self.name, force=True)
        self.assertEqual(result.results[0].code, m.NGINX_REJECTED)

    def test_renew_unknown_name(self):
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertEqual(result.results[0].code, m.NOT_FOUND)

    def test_remove_in_order_and_rerun(self):
        self.add()
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.steps(result), [
            ("preflight", "done"), ("gate", "done"), ("remove_block", "done"),
            ("remove_certificate", "done"), ("remove_dns", "done")])
        self.assertEqual(self.remote.payloads("remove")[-1]["jobs_probe"],
                         {"home": "/home/u/sandbox"})
        self.assertNotIn(FQDN, self.dns.records)
        self.assertEqual(self.store.load(), {})
        self.assertEqual(service.remove(self.ctx, self.deps, self.name).code, m.NOT_FOUND)

    def test_remove_refuses_foreign_paths_and_leaves_them(self):
        self.add()
        self.remote.foreign_block.add(FQDN)
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.UNMANAGED))
        self.assertIn(FQDN, self.dns.records)

    def test_remove_leaves_a_record_it_no_longer_owns(self):
        self.add()
        self.dns.records[FQDN]["content"] = "100.64.0.99"
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.DNS_NOT_OWNED))
        self.assertIn(FQDN, self.dns.records)
        self.assertIn(FQDN, self.store.load())

    def test_remove_resumes_after_partial_run(self):
        self.add()
        self.dns.fail = True
        self.assertEqual(service.remove(self.ctx, self.deps, self.name).code, m.DNS_FAILED)
        self.dns.fail = False
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertIn(("remove_block", "skipped"), self.steps(result))

    def test_remove_without_a_recorded_record_leaves_dns(self):
        self.add()
        self.store.drop(FQDN)
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.DNS_NOT_OWNED))
        self.assertIn(FQDN, self.dns.records)

    def test_remove_while_another_run_holds_the_lease(self):
        self.add()
        self.remote.leases[FQDN] = "f" * 32
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.BUSY))
        self.assertIn(FQDN, self.remote.blocks)

    def test_renew_does_not_skip_a_name_that_failed_verification(self):
        """Re-review N1: NOT_DUE needs a verified activation, not just a loaded one."""
        self.public = verify.ProbeResult("timeout")
        self.assertEqual(self.add().code, m.PUBLIC_PROBE_INCONCLUSIVE)
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertEqual(result.results[0].code, m.PUBLIC_PROBE_INCONCLUSIVE)
        self.assertEqual(len(self.issued), 1, "a current certificate is not reissued")
        self.public = verify.ProbeResult("refused")
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertNotEqual(result.results[0].steps[0].code, m.NOT_DUE)
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertEqual(result.results[0].steps[0].code, m.NOT_DUE)

    def test_renew_verifies_after_a_crash_between_commit_and_verify(self):
        calls = {"n": 0}

        def crash(address, fqdn, *, send_request):
            calls["n"] += 1
            raise KeyboardInterrupt("simulated crash")
        self.deps.probe = crash
        with self.assertRaises(KeyboardInterrupt):
            self.add()
        self.assertEqual(self.store.load()[FQDN].steps["verify"], "pending")
        self.deps.probe = self._probe
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertIn("verify_public", [step.step for step in result.results[0].steps])
        self.assertEqual(self.store.load()[FQDN].state, "published")

    def test_renew_does_not_trust_a_stale_activation(self):
        self.add()
        self.remote.activation[FQDN] = "stale"
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertNotEqual(result.results[0].steps[0].code, m.NOT_DUE)

    def test_remove_holds_the_lease_through_the_dns_delete(self):
        """Re-review N2: no other controller can publish between the remote
        removal and the DNS delete."""
        self.add()
        seen = {}
        dns = self.deps.dns

        def checking(cfg, request):
            if request["op"] == "delete":
                other = self.remote.call(None, "reserve", {"fqdn": FQDN, "token": "f" * 32,
                                                           "seconds": 600})
                seen["other"] = other
            return dns(cfg, request)
        self.deps.dns = checking
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(seen["other"]["code"], "busy")
        self.assertEqual(self.remote.leases, {}, "released after the DNS step")

    def test_policy_is_decided_from_the_remote_as_it_is_under_the_lease(self):
        """Re-review N6: a replacement made by another controller just before
        this run took the lease is not silently undone."""
        self.add()
        call = self.remote.call
        theirs = render.render_block(self.name, m.Upstream("127.0.0.1", 9000), TAILNET_IP,
                                     http2_directive=True)

        def racing(entry, action, payload):
            if action == "reserve":
                self.remote.blocks[FQDN] = theirs
            return call(entry, action, payload)
        self.deps.remote_call = racing
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.UPSTREAM_DIFFERS))
        self.assertEqual(self.remote.blocks[FQDN], theirs)
        self.assertEqual(self.remote.leases, {})


class CliTests(unittest.TestCase):
    def _main(self, *argv: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "SANDBOX_HOME": home, "PATH": os.environ.get("PATH", ""),
                   "PYTHONPATH": str(REPO), "SANDBOX_AUTO_UPDATE": "0"}
            code = ("import sys; sys.argv = ['sb', *sys.argv[1:]]\n"
                    "from sandbox.cli import main\nmain()\n")
            return subprocess.run([sys.executable, "-c", code, *argv], cwd=home, env=env,
                                  capture_output=True, text=True, timeout=120, check=False)

    def test_add_without_configuration_refuses_with_json(self):
        result = self._main("host", "tailnet-name", "add", FQDN, "--remote", "nosuch",
                            "--upstream", "127.0.0.1:8025", "--json")
        line = [item for item in result.stdout.splitlines() if item.startswith("{")][-1]
        payload = json.loads(line)
        self.assertEqual((payload["ok"], payload["code"]), (False, m.CONFIG_MISSING), result.stderr)
        self.assertEqual(result.returncode, 1)

    def test_fqdn_positional_is_refused_elsewhere(self):
        result = self._main("host", "logs", FQDN, "--remote", "nosuch")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tailnet-name", result.stderr + result.stdout)

    def test_handler_validates_action_and_arguments(self):
        from sandbox.commands import host_tailnet_name as cli
        base = dict(action="tailnet-name", remote="r1", json=True, upstream=None,
                    tailnet_hostname=None, all=False, force=False, replace_upstream=False,
                    public_address=None, acme_server=None, project_dir=None)
        for overrides, code in (({"image_action": None}, "tailnet_name_usage"),
                                ({"image_action": "up"}, "tailnet_name_usage"),
                                ({"image_action": "add", "tailnet_hostname": FQDN}, "tailnet_name_usage"),
                                ({"image_action": "list", "remote": None}, "tailnet_name_usage"),
                                ({"image_action": "renew"}, "tailnet_name_usage")):
            with self.subTest(overrides=overrides):
                out = io.StringIO()
                with mock.patch("sys.stdout", out), self.assertRaises(SystemExit) as caught:
                    cli.cmd_host_tailnet_name(SimpleNamespace(**{**base, **overrides}))
                self.assertEqual(caught.exception.code, 1)
                self.assertEqual(json.loads(out.getvalue().splitlines()[-1])["code"], code)

    def test_other_host_actions_reject_the_positional(self):
        from sandbox.commands import hosting as hosting_cmd
        buffer = io.StringIO()
        with redirect_stderr(buffer), self.assertRaises(SystemExit) as caught:
            hosting_cmd.cmd_host({}, SimpleNamespace(action="logs", tailnet_hostname=FQDN))
        self.assertEqual(caught.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
