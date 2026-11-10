"""Tailnet-only hostnames (spec 078): models, render, broker, verify, state,
service flows over fakes, and the CLI surface.

Placeholders only: example.test, 203.0.113.x (public), 100.64.0.x (tailnet).
"""
from __future__ import annotations

import datetime as dt
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
from sandbox.hosting.tailnet_names import render, service, state, verify  # noqa: E402
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


class FakeSecretService:
    def __init__(self, *, output="", exit_code=0, error=None, on_run=None):
        self.calls = []
        self.output, self.exit_code, self.error, self.on_run = output, exit_code, error, on_run

    def run(self, source, key, argv, *, destination, timeout_seconds=300,
            max_output_bytes=1_048_576, surface="cli"):
        self.calls.append({"source": source, "key": key, "argv": list(argv),
                           "destination": destination, "timeout": timeout_seconds,
                           "surface": surface})
        if self.error:
            raise self.error
        if self.on_run:
            self.on_run(list(argv))
        return {"ok": True, "operation": "run",
                "result": {"exit_code": self.exit_code, "termination": "exited",
                           "output": self.output, "truncated": False}}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "acme"

    def tearDown(self):
        self.tmp.cleanup()

    def test_dns_runs_the_shipped_child_with_the_token_destination(self):
        fake = FakeSecretService(output='noise\n{"result": "created", "record_id": "%s"}\n' % ("a" * 32))
        result = Broker(lambda: fake, acme_root=self.root).dns(
            config(), {"op": "ensure", "zone": "example.test", "name": FQDN, "ip": TAILNET_IP})
        self.assertEqual(result.result, "created")
        call = fake.calls[0]
        self.assertEqual((call["source"], call["key"], call["destination"], call["surface"]),
                         ("personal", "EXAMPLE_CF_API_TOKEN", m.DNS_TOKEN_DESTINATION, "cli"))
        self.assertEqual(call["argv"][:2], [sys.executable, "-I"])
        self.assertTrue(call["argv"][2].endswith("tailnet_names/cloudflare_child.py"))
        self.assertEqual(json.loads(call["argv"][3])["name"], FQDN)

    def test_broker_refusal_maps_without_detail_leak(self):
        fake = FakeSecretService(error=SecretBrokerError("key_missing", "secret key does not exist"))
        with self.assertRaises(m.TailnetNameError) as caught:
            Broker(lambda: fake, acme_root=self.root).dns(config(), {"op": "inspect"})
        self.assertEqual(caught.exception.code, m.SECRET_UNAVAILABLE)
        self.assertIn("key_missing", str(caught.exception))

    def test_dns_child_without_result_is_a_dns_failure(self):
        fake = FakeSecretService(output="Traceback\n", exit_code=1)
        with self.assertRaises(m.TailnetNameError) as caught:
            Broker(lambda: fake, acme_root=self.root).dns(config(), {"op": "inspect"})
        self.assertEqual(caught.exception.code, m.DNS_FAILED)

    def test_issue_runs_lego_in_csr_mode_and_reads_the_bundle(self):
        stale = self.root / "certificates" / (FQDN + ".crt")
        stale.parent.mkdir(parents=True)
        stale.write_text("stale")

        def on_run(argv):
            self.assertFalse(stale.exists(), "old output must be cleared before lego runs")
            stale.write_text("-----BEGIN CERTIFICATE-----\nnew\n-----END CERTIFICATE-----\n")
        fake = FakeSecretService(on_run=on_run)
        chain = Broker(lambda: fake, acme_root=self.root).issue(
            config(), FQDN, "-----BEGIN CERTIFICATE REQUEST-----\nx\n", lego="/opt/lego")
        self.assertIn("new", chain)
        argv = fake.calls[0]["argv"]
        csr = str(self.root / "csr" / (FQDN + ".csr"))
        self.assertEqual(argv, ["/opt/lego", "run", "--accept-tos", "--email", "ops@example.test",
                                "--server", m.ACME_SERVERS["letsencrypt"], "--dns", "cloudflare",
                                "--csr", csr, "--path", str(self.root)])
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(fake.calls[0]["destination"], m.DNS_TOKEN_DESTINATION)

    def test_issue_failure_reports_a_bounded_tail(self):
        output = "\n".join(f"line {i}" for i in range(50))
        fake = FakeSecretService(output=output, exit_code=1)
        with self.assertRaises(m.TailnetNameError) as caught:
            Broker(lambda: fake, acme_root=self.root).issue(config(), FQDN, "csr", lego="/opt/lego")
        self.assertEqual(caught.exception.code, m.CERTIFICATE_FAILED)
        self.assertIn("line 49", str(caught.exception))
        self.assertNotIn("line 40", str(caught.exception))

    def test_find_lego(self):
        broker = Broker(lambda: None, acme_root=self.root)
        self.assertIsNone(broker.find_lego(config(lego="/nonexistent/lego")))
        tool = Path(self.tmp.name) / "lego"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        self.assertEqual(broker.find_lego(config(lego=str(tool))), str(tool))


class StateTests(unittest.TestCase):
    def test_put_load_drop_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = state.StateStore(Path(tmp), "r1")
            record = m.TailnetNameRecord(FQDN, "example.test", "127.0.0.1:8025", TAILNET_IP)
            store.put(record)
            self.assertEqual(store.load()[FQDN].upstream, "127.0.0.1:8025")
            path = Path(tmp) / "r1.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.write_text(json.dumps({"version": 1, "names": {"bad": {"fqdn": 3}, FQDN: record.as_dict()}}))
            self.assertEqual(list(store.load()), [FQDN])
            store.drop(FQDN)
            self.assertEqual(store.load(), {})

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


def _openssl(*args, cwd):
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True, timeout=60)


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


class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        _openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-keyout", "k.pem", "-out", "c.pem", "-days", "1", "-subj", "/CN=" + FQDN,
                 cwd=d)
        cls.cert, cls.key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
        der = ssl.PEM_cert_to_DER_cert(Path(cls.cert).read_text())
        import hashlib
        cls.sha = hashlib.sha256(der).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def probe(self, status=200, send_request=True):
        server = _TlsServer(self.cert, self.key, status)
        try:
            return verify.probe("127.0.0.1", FQDN, port=server.port, timeout=5,
                                send_request=send_request)
        finally:
            server.close()

    def test_tailnet_probe_matches_fingerprint_and_status(self):
        result = self.probe()
        self.assertEqual((result.handshake, result.leaf_sha256, result.http_status),
                         (True, self.sha, 200))
        self.assertEqual(verify.tailnet_outcome(result, self.sha).status, "done")
        self.assertEqual(verify.tailnet_outcome(result, "0" * 64).code, m.TAILNET_PROBE_FAILED)
        bad = self.probe(status=502)
        self.assertEqual(verify.tailnet_outcome(bad, self.sha).code, m.TAILNET_PROBE_FAILED)

    def test_public_probe(self):
        ours = self.probe(send_request=False)
        outcome, exposed = verify.public_outcome(ours, self.sha)
        self.assertTrue(exposed)
        self.assertEqual(outcome.code, m.PUBLIC_EXPOSED)
        outcome, exposed = verify.public_outcome(ours, "0" * 64)
        self.assertFalse(exposed)
        self.assertEqual(outcome.status, "done")
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        refused = verify.probe("127.0.0.1", FQDN, port=port, timeout=2, send_request=False)
        self.assertFalse(refused.connected)
        outcome, exposed = verify.public_outcome(refused, self.sha)
        self.assertEqual((outcome.status, exposed), ("done", False))


# ---------------------------------------------------------------------------
# Service flows over fakes
# ---------------------------------------------------------------------------

LEAF_SHA = "ab" * 32
FUTURE = "2027-02-08T00:00:00Z"
SOON = "2026-11-20T00:00:00Z"


class FakeRemote:
    """The remote program, nginx route transaction and preflight, in memory."""

    def __init__(self):
        self.blocks: dict[str, str] = {}
        self.certs: dict[str, dict] = {}
        self.keys: set[str] = set()
        self.locks: list[str] = []
        self.bound = True
        self.calls: list[tuple] = []
        self.reject_nginx = False
        self.install_error: str | None = None
        self.preflight_error: FrontDoorError | None = None

    # remote program
    def call(self, entry, action, payload):
        self.calls.append(("program", action, json.dumps(payload, sort_keys=True)))
        if action == "inspect":
            names = [payload["fqdn"]] if payload.get("fqdn") else sorted(
                {n[len("tailnet-"):] for n in self.blocks} | set(self.certs))
            return {"ok": True, "tailnet_ip_bound": self.bound, "locks_held": list(self.locks),
                    "inventory": [self._row(n) for n in names]}
        if action == "csr":
            self.keys.add(payload["fqdn"])
            return {"ok": True, "csr": "-----BEGIN CERTIFICATE REQUEST-----\n"}
        if action == "install":
            if self.install_error:
                return {"ok": False, "code": self.install_error, "detail": "nginx -t failed"}
            self.certs[payload["fqdn"]] = {"not_after": FUTURE, "sha": LEAF_SHA}
            return {"ok": True, "changed": True, "not_after": FUTURE, "sha256": LEAF_SHA}
        if action == "remove_cert":
            self.certs.pop(payload["fqdn"], None)
            self.keys.discard(payload["fqdn"])
            return {"ok": True, "removed": True}
        raise AssertionError(action)

    def _row(self, fqdn):
        block = self.blocks.get("tailnet-" + fqdn)
        cert = self.certs.get(fqdn)
        return {"fqdn": fqdn, "block_present": block is not None,
                "header": render.parse_header(block) if block else None,
                "cert_present": cert is not None, "key_present": fqdn in self.keys,
                "cert_not_after": cert and cert["not_after"], "cert_sha256": cert and cert["sha"],
                "cert_names": [fqdn] if cert else [], "key_matches": True if cert else None}

    def preflight(self, entry, declared, own_file):
        self.calls.append(("preflight", tuple(declared), own_file))
        if self.preflight_error:
            raise self.preflight_error
        return {"version": [1, 26, 0], "realip": True}

    def apply_route(self, entry, name, content):
        self.calls.append(("apply_route", name, content))
        if self.reject_nginx:
            raise FrontDoorError("front_door_validate_failed", "nginx -t failed; restored")
        if self.blocks.get(name) == content:
            return {"state": "unchanged"}
        self.blocks[name] = content
        return {"state": "changed"}

    def remove_route(self, entry, name):
        self.calls.append(("remove_route", name))
        self.blocks.pop(name, None)
        return {"state": "removed"}


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
                return m.DnsResult("conflict", row["id"], row["content"], row["proxied"])
            del self.records[request["name"]]
            return m.DnsResult("deleted", row["id"])
        raise AssertionError(op)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.remote = FakeRemote()
        self.dns = FakeDns()
        self.issued: list[tuple] = []
        self.jobs_active = False
        self.jobs_error = False
        self.public_leaf: str | None = None  # None: handshake refused
        self.tailnet_status = 200
        self.store = state.StateStore(Path(self.tmp.name), "r1")
        self.deps = service.Deps(
            remote_call=self.remote.call, preflight=self.remote.preflight,
            apply_route=self.remote.apply_route, remove_route=self.remote.remove_route,
            dns=self.dns, issue=self._issue, find_lego=lambda cfg: "/opt/lego",
            active_jobs=self._jobs, probe=self._probe, store=lambda remote: self.store,
            now=lambda: NOW)
        self.ctx = service.Context(config(), target())
        self.name = m.TailnetName(FQDN, "example.test")
        self.upstream = m.Upstream("127.0.0.1", 8025)

    def tearDown(self):
        self.tmp.cleanup()

    def _issue(self, cfg, fqdn, csr, lego):
        self.issued.append((fqdn, lego))
        return "-----BEGIN CERTIFICATE-----\nleaf\n-----END CERTIFICATE-----\n"

    def _jobs(self, remote):
        if self.jobs_error:
            raise RuntimeError("unreachable")
        return self.jobs_active

    def _probe(self, address, fqdn, *, send_request):
        if address == TAILNET_IP:
            return verify.ProbeResult(True, True, LEAF_SHA, self.tailnet_status, None)
        if self.public_leaf is None:
            return verify.ProbeResult(True, False, None, None, "handshake refused")
        return verify.ProbeResult(True, True, self.public_leaf, None, None)

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
            ("install", "done"), ("gate", "done"), ("server_block", "done"),
            ("verify_tailnet", "done"), ("verify_public", "done")])
        self.assertEqual(self.dns.records[FQDN]["content"], TAILNET_IP)
        self.assertEqual(self.issued, [(FQDN, "/opt/lego")])
        install = [c for c in self.remote.calls if c[:2] == ("program", "install")][0]
        self.assertFalse(json.loads(install[2])["reload"], "a fresh name has no block to reload")
        block = self.remote.blocks["tailnet-" + FQDN]
        self.assertIn(f"listen {TAILNET_IP}:443 ssl;", block)
        record = self.store.load()[FQDN]
        self.assertEqual((record.state, record.dns_record_id, record.dns_adopted, record.cert_sha256),
                         ("published", "c" * 32, False, LEAF_SHA))
        preflight = [c for c in self.remote.calls if c[0] == "preflight"][0]
        self.assertEqual(preflight[1:], ((FQDN,), "/etc/nginx/conf.d/sandbox-tailnet-" + FQDN + ".conf"))

    def test_rerun_skips_every_step_and_writes_nothing(self):
        self.assertTrue(self.add().ok)
        self.remote.calls.clear()
        self.dns.requests.clear()
        self.issued.clear()
        result = self.add()
        self.assertTrue(result.ok)
        statuses = dict((s, st) for s, st in self.steps(result) if s != "gate")
        self.assertEqual(statuses, {"preflight": "done", "dns": "skipped", "certificate": "skipped",
                                    "install": "skipped", "server_block": "skipped",
                                    "verify_tailnet": "done", "verify_public": "done"})
        self.assertEqual(self.issued, [])
        self.assertFalse([c for c in self.remote.calls if c[:2] in (("program", "csr"),
                                                                     ("program", "install"))])
        self.assertEqual([r["op"] for r in self.dns.requests], ["ensure"])

    def test_identical_existing_record_is_adopted(self):
        self.dns.records[FQDN] = {"id": "d" * 32, "content": TAILNET_IP, "proxied": False}
        result = self.add()
        self.assertTrue(result.ok)
        self.assertIn(("dns", "skipped"), self.steps(result))
        record = self.store.load()[FQDN]
        self.assertEqual((record.dns_record_id, record.dns_adopted), ("d" * 32, True))

    def test_conflicting_record_refuses_before_any_remote_write(self):
        for row in ({"id": "e" * 32, "content": "100.64.0.99", "proxied": False},
                    {"id": "e" * 32, "content": TAILNET_IP, "proxied": True}):
            with self.subTest(row=row):
                self.remote.calls.clear()
                self.dns.records[FQDN] = dict(row)
                result = self.add()
                self.assertFalse(result.ok)
                self.assertEqual(result.code, m.DNS_CONFLICT)
                self.assertEqual(self.steps(result)[-1], ("dns", "refused"))
                self.assertFalse([c for c in self.remote.calls
                                  if c[0] == "apply_route" or c[:2] == ("program", "csr")])
                self.assertEqual(self.dns.records[FQDN], row)

    def test_nginx_rejection_reports_and_leaves_no_block(self):
        self.remote.reject_nginx = True
        result = self.add()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, m.NGINX_REJECTED)
        self.assertEqual(self.steps(result)[-1], ("server_block", "failed"))
        self.assertNotIn("tailnet-" + FQDN, self.remote.blocks)
        self.assertEqual(self.store.load()[FQDN].state, "publishing")

    def test_busy_gate_refuses_without_waiting(self):
        for setup, code in ((lambda: setattr(self, "jobs_active", True), m.BUSY),
                            (lambda: self.remote.locks.append("/run/lock/sandbox-docker-pool.lock"),
                             m.BUSY),
                            (lambda: setattr(self, "jobs_error", True), m.GATE_UNAVAILABLE)):
            with self.subTest(code=code):
                self.jobs_active = self.jobs_error = False
                self.remote.locks.clear()
                setup()
                result = self.add()
                self.assertFalse(result.ok)
                self.assertEqual(result.code, code)
                self.assertEqual(self.steps(result)[-1], ("gate", "refused"))
                self.assertEqual(self.dns.requests, [])

    def test_unbound_tailnet_address_and_preflight_conflict_refuse(self):
        self.remote.bound = False
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.TAILNET_ADDRESS_UNBOUND))
        self.remote.bound = True
        self.remote.preflight_error = FrontDoorError("front_door_hostname_conflict", "served")
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, "front_door_hostname_conflict"))
        self.assertEqual(self.dns.requests, [])

    def test_upstream_differs_needs_replace(self):
        self.assertTrue(self.add().ok)
        self.upstream = m.Upstream("127.0.0.1", 9000)
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.UPSTREAM_DIFFERS))
        self.assertIn("8025", self.remote.blocks["tailnet-" + FQDN])
        result = self.add(replace_upstream=True)
        self.assertTrue(result.ok)
        self.assertIn(("server_block", "done"), self.steps(result))
        self.assertIn("proxy_pass http://127.0.0.1:9000;", self.remote.blocks["tailnet-" + FQDN])
        self.assertEqual(self.store.load()[FQDN].upstream, "127.0.0.1:9000")

    def test_public_exposure_removes_the_block(self):
        self.public_leaf = LEAF_SHA
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.PUBLIC_EXPOSED))
        self.assertNotIn("tailnet-" + FQDN, self.remote.blocks)
        self.assertIn(("remove_route", "tailnet-" + FQDN), self.remote.calls)

    def test_other_certificate_on_public_address_passes(self):
        self.public_leaf = "ff" * 32
        self.assertTrue(self.add().ok)

    def test_tailnet_probe_failure_keeps_the_name_and_rerun_repeats_verify(self):
        self.tailnet_status = 502
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.TAILNET_PROBE_FAILED))
        self.assertIn("tailnet-" + FQDN, self.remote.blocks)
        self.assertEqual(self.store.load()[FQDN].state, "publishing")
        self.tailnet_status = 200
        result = self.add()
        self.assertTrue(result.ok)
        self.assertEqual(self.store.load()[FQDN].state, "published")

    def test_no_public_address_is_not_ok(self):
        self.ctx = service.Context(config(), target(public_ip=None))
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.PUBLIC_PROBE_UNAVAILABLE))
        self.assertIn(("verify_public", "not_run"), self.steps(result))

    def test_interruption_then_rerun_finishes(self):
        def boom(*_a, **_k):
            raise KeyboardInterrupt
        original = self.deps.apply_route
        self.deps.apply_route = boom
        with self.assertRaises(KeyboardInterrupt):
            self.add()
        record = self.store.load()[FQDN]
        self.assertEqual(record.steps.get("install"), "done")
        self.deps.apply_route = original
        self.issued.clear()
        result = self.add()
        self.assertTrue(result.ok)
        self.assertEqual(self.issued, [], "the installed certificate is reused")

    def test_dns_provider_failure_is_recorded(self):
        self.dns.fail = True
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.DNS_FAILED))
        self.assertEqual(self.store.load()[FQDN].steps.get("dns"), "failed")

    def test_missing_lego_refuses_first(self):
        self.deps.find_lego = lambda cfg: None
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.ACME_CLIENT_MISSING))
        self.assertEqual(self.remote.calls, [])

    def test_install_failure(self):
        self.remote.install_error = "certificate_mismatch"
        result = self.add()
        self.assertEqual((result.ok, result.code), (False, m.INSTALL_FAILED))
        self.assertFalse([c for c in self.remote.calls if c[0] == "apply_route"])

    def test_token_marker_never_reaches_outputs_state_or_argv(self):
        """SC-004: a marker configured as the key name's value never leaks."""
        result = self.add()
        blob = json.dumps(result.envelope()) + json.dumps(self.remote.calls) + \
            json.dumps(self.dns.requests) + (Path(self.tmp.name) / "r1.json").read_text()
        self.assertNotIn(MARKER, blob)


class ListRenewRemoveTests(ServiceTestCase):
    def test_list_merges_inventory_and_state_with_drift(self):
        self.assertTrue(self.add().ok)
        self.remote.blocks["tailnet-other.example.test"] = render.render_block(
            m.TailnetName("other.example.test", "example.test"), self.upstream, TAILNET_IP,
            http2_directive=True)
        self.store.put(m.TailnetNameRecord("gone.example.test", "example.test",
                                           "127.0.0.1:1", TAILNET_IP, state="published"))
        rows = {row["fqdn"]: row for row in service.list_names(self.ctx, self.deps).envelope()["names"]}
        self.assertEqual(rows[FQDN]["upstream"], "127.0.0.1:8025")
        self.assertEqual(rows[FQDN]["cert_not_after"], FUTURE)
        self.assertEqual(rows[FQDN]["days_remaining"], 90)
        self.assertFalse(rows[FQDN]["renewal_due"])
        self.assertEqual(rows[FQDN]["drift"], [])
        self.assertIn("not_recorded", rows["other.example.test"]["drift"])
        self.assertIn("block_missing", rows["gone.example.test"]["drift"])

    def test_renew_not_due_is_skipped_and_force_reissues(self):
        self.assertTrue(self.add().ok)
        self.issued.clear()
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok)
        self.assertEqual(result.results[0].steps[-1].code, m.NOT_DUE)
        self.assertEqual(self.issued, [])
        result = service.renew(self.ctx, self.deps, self.name, force=True)
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(len(self.issued), 1)
        install = [c for c in self.remote.calls if c[:2] == ("program", "install")][-1]
        self.assertTrue(json.loads(install[2])["reload"])

    def test_renew_all_renews_only_due_names(self):
        self.assertTrue(self.add().ok)
        other = m.TailnetName("other.example.test", "example.test")
        self.assertTrue(service.add(self.ctx, self.deps, other, self.upstream).ok)
        self.remote.certs["other.example.test"]["not_after"] = SOON
        self.issued.clear()
        result = service.renew(self.ctx, self.deps, None, all_names=True)
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual(self.issued, [("other.example.test", "/opt/lego")])
        self.assertEqual(len(result.envelope()["results"]), 2)

    def test_renew_install_failure_is_reported(self):
        self.assertTrue(self.add().ok)
        self.remote.install_error = "nginx_rejected"
        result = service.renew(self.ctx, self.deps, self.name, force=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.results[0].code, m.NGINX_REJECTED)

    def test_renew_unknown_name(self):
        result = service.renew(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.results[0].code), (False, m.NOT_FOUND))

    def test_remove_in_order_and_rerun(self):
        self.assertTrue(self.add().ok)
        self.remote.calls.clear()
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok, result.envelope())
        self.assertEqual([s for s, _ in self.steps(result)],
                         ["preflight", "gate", "remove_block", "remove_certificate", "remove_dns"])
        order = [c[0] if c[0] != "program" else c[1] for c in self.remote.calls]
        self.assertLess(order.index("remove_route"), order.index("remove_cert"))
        self.assertEqual(self.dns.records, {})
        self.assertNotIn(FQDN, self.store.load())
        again = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((again.ok, again.code), (False, m.NOT_FOUND))

    def test_remove_leaves_a_record_it_no_longer_owns(self):
        self.assertTrue(self.add().ok)
        self.dns.records[FQDN]["content"] = "100.64.0.99"
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.DNS_NOT_OWNED))
        self.assertIn(FQDN, self.dns.records)
        self.assertNotIn("tailnet-" + FQDN, self.remote.blocks)
        self.assertEqual(self.store.load()[FQDN].state, "removing")

    def test_remove_resumes_after_partial_run(self):
        self.assertTrue(self.add().ok)
        self.remote.remove_route(None, "tailnet-" + FQDN)
        self.store.put(m.TailnetNameRecord(**{**self.store.load()[FQDN].as_dict(),
                                              "state": "removing"}))
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertTrue(result.ok)
        self.assertIn(("remove_block", "skipped"), self.steps(result))
        self.assertIn(("remove_dns", "done"), self.steps(result))

    def test_remove_without_a_recorded_record_leaves_dns(self):
        self.assertTrue(self.add().ok)
        self.store.drop(FQDN)
        result = service.remove(self.ctx, self.deps, self.name)
        self.assertEqual((result.ok, result.code), (False, m.DNS_NOT_OWNED))
        self.assertIn(FQDN, self.dns.records)


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
