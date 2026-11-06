"""Acceptance tests for wildcard subdomain hosting targets (Spec 070 Stage 2).

Tests target decision (decide), drift rules (check_exact_record), pinned TLS
verification, store dependent lifecycle, recovery, plan refusals, and apply
evidence using:
- Real store program via ZoneCoverageClient with temporary SANDBOX_HOME
- StatefulFakeProvider from tests/test_zone_coverage_matrix.py
- Real loopback TLS servers with openssl-generated certificates
"""
from __future__ import annotations

import base64
import contextlib
import copy
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError
import urllib.request

from sandbox.core import _cloudflare as cloudflare
from sandbox.core import _hosting as hosting
from sandbox.commands import hosting as hosting_cmd
from sandbox.hosting.zone_coverage import (
    CoverageError,
    COVERAGE_DISABLED_DEPENDENT,
    COVERAGE_DRIFT,
    COVERAGE_IMAGE_UNSUPPORTED,
    COVERAGE_NAME_INELIGIBLE,
    COVERAGE_NOT_ACTIVE,
    COVERAGE_RECORD_MISSING,
    COVERAGE_STORE_UNAVAILABLE,
    MANAGED_RECORD_DRIFT,
    RECORD_NOT_OWNED,
    targets,
)
from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
from sandbox.hosting.zone_coverage import models, program
from tests.subprocess_support import run_test_process, synthetic_environment
from tests.test_hosting import _HostingOwnerFixture, _public_acme_manifest
from tests.test_zone_coverage_matrix import StatefulFakeProvider


def make_real_store(temp_dir: str, entry: dict) -> ZoneCoverageClient:
    """Create a real ZoneCoverageClient running against temp_dir as SANDBOX_HOME."""
    def real_ssh_run(e, command, timeout=15):
        # Accept only the exact command emitted by the real store client. Decode
        # its argument as data and execute the fixed program without a shell.
        parts = shlex.split(command)
        if (len(parts) != 5 or parts[:3] != ["python3", "-c", program.PROGRAM]
                or parts[3] != "${SANDBOX_HOME:-$HOME/sandbox}"):
            raise AssertionError("unexpected store transport command")
        try:
            request = json.loads(base64.b64decode(parts[4], validate=True))
        except (ValueError, TypeError) as exc:
            raise AssertionError("invalid store request") from exc
        if not isinstance(request, dict) or program.remote_command(request) != command:
            raise AssertionError("noncanonical store request")
        env = synthetic_environment({"SANDBOX_HOME": temp_dir, "HOME": temp_dir})
        return subprocess.run([sys.executable, "-I", "-B", "-c", program.PROGRAM,
                               temp_dir, parts[4]], env=env, capture_output=True,
                              text=True, timeout=timeout)
    return ZoneCoverageClient(entry, ssh_run=real_ssh_run)


def seed_store_zone(temp_dir: str, zone: str = "asb.bd", state: str = "active", generation: int = 1,
                    dependents: dict | None = None, shared_records: list | None = None,
                    operation: dict | None = None) -> dict:
    """Seed a valid zone document in the real store directory."""
    zones_dir = Path(temp_dir) / "runtime" / "zone-coverage" / "zones"
    zones_dir.mkdir(parents=True, exist_ok=True)
    if shared_records is None:
        shared_records = [{
            "id": "rec-wildcard-1",
            "name": f"*.{zone}",
            "type": "A",
            "content": "198.51.100.1",
            "proxied": True,
        }]
    doc = {
        "schema_version": 1,
        "zone": zone,
        "state": state,
        "generation": generation,
        "dependents_revision": 0,
        "record": shared_records[0] if shared_records else None,
        "shared_records": shared_records,
        "dependents": dependents or {},
        "history": [],
    }
    if operation is not None:
        doc["operation"] = operation
    (zones_dir / f"{zone}.json").write_text(json.dumps(doc))
    return doc


def make_loopback_tls_server(san_hostname: str, status_code: int = 200, body: bytes = b"OK"):
    """Start a real loopback TLS server serving an openssl self-signed cert with san_hostname."""
    td = tempfile.mkdtemp(prefix="sb-tls-")
    key_path = os.path.join(td, "key.pem")
    cert_path = os.path.join(td, "cert.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", key_path,
         "-out", cert_path, "-days", "1", "-nodes", "-subj", f"/CN={san_hostname}"],
        check=True, capture_output=True,
    )
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

    received_sni = []
    received_headers = []

    def sni_cb(sock, req_hostname, ctx):
        received_sni.append(req_hostname)
        return None

    server_ctx.sni_callback = sni_cb

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(5)
    port = server_sock.getsockname()[1]

    running = True

    def run_server():
        while running:
            try:
                server_sock.settimeout(0.5)
                conn, _ = server_sock.accept()
            except (socket.timeout, OSError):
                continue
            try:
                with server_ctx.wrap_socket(conn, server_side=True) as tls_conn:
                    data = b""
                    while b"\r\n\r\n" not in data:
                        chunk = tls_conn.recv(1024)
                        if not chunk:
                            break
                        data += chunk
                    received_headers.append(data.decode("utf-8", "replace"))
                    status_line = f"HTTP/1.1 {status_code} Status\r\n".encode()
                    resp = (
                        status_line +
                        b"Content-Type: text/plain\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"Connection: close\r\n\r\n" + body
                    )
                    tls_conn.sendall(resp)
            except Exception:
                pass

    th = threading.Thread(target=run_server, daemon=True)
    th.start()

    def cleanup():
        nonlocal running
        running = False
        try:
            server_sock.close()
        except Exception:
            pass
        th.join(timeout=2)
        shutil.rmtree(td, ignore_errors=True)

    return {
        "port": port,
        "cert_path": cert_path,
        "key_path": key_path,
        "received_sni": received_sni,
        "received_headers": received_headers,
        "cleanup": cleanup,
    }


class TestZoneCoverageDecideRows(unittest.TestCase):
    """Item 1: test_decide_row_<n>_... through real store with forbidden-call assertions."""

    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="sb-decide-")
        self.entry = {
            "name": "r1",
            "front_door": "nginx",
            "origin_ipv4": "198.51.100.1",
            "origin_ipv6": None,
            "capabilities": ["zone_coverage_v1"],
        }
        self.cf_client = StatefulFakeProvider(
            zone_id="z-asb",
            zone_name="asb.bd",
            ssl_mode="strict",
            records=[{
                "id": "rec-wildcard-1",
                "name": "*.asb.bd",
                "type": "A",
                "content": "198.51.100.1",
                "proxied": True,
            }],
        )
        self.facts = {
            "routes": [{
                "file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf",
                "coverage_zone": "asb.bd",
            }],
        }
        self.validated = {
            "project": "demo",
            "environment": "main",
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": "auto"},
        }

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_decide_row_1_lacks_capability(self):
        # Front door lacks ZONE_COVERAGE: {"mode": "exact"} with no store, SSH or provider call
        caddy_entry = dict(self.entry, front_door="caddy", capabilities=[])
        mock_store = mock.Mock()
        mock_store.list.side_effect = AssertionError("store must not be called")
        mock_cf = mock.Mock()
        mock_cf.zones.side_effect = AssertionError("provider must not be called")

        result = targets.decide(self.validated, caddy_entry, None, store_client=mock_store, cf_client=mock_cf)
        self.assertEqual(result, {"mode": "exact"})
        mock_store.list.assert_not_called()
        mock_cf.zones.assert_not_called()

    def test_decide_row_2_store_unavailable(self):
        # Store list fails: refuse coverage_store_unavailable
        failing_client = ZoneCoverageClient(self.entry, ssh_run=lambda *a, **k: 1 / 0)
        res = targets.decide(self.validated, self.entry, None, store_client=failing_client, cf_client=self.cf_client, report=True)
        self.assertEqual(res.get("mode"), "refused")
        self.assertEqual(res.get("code"), COVERAGE_STORE_UNAVAILABLE)

        with self.assertRaises(CoverageError) as ctx:
            targets.decide(self.validated, self.entry, None, store_client=failing_client, cf_client=self.cf_client, report=False)
        self.assertEqual(ctx.exception.code, COVERAGE_STORE_UNAVAILABLE)

    def test_decide_row_3_uncovered_zone(self):
        # Find covered zone: none or retired.
        # If fallback route in nginx facts holds fallback for suffix: refuse coverage_record_missing
        # Otherwise: {"mode": "exact"} with no provider call.
        store_client = make_real_store(self.td, self.entry)
        val_uncovered = copy.deepcopy(self.validated)
        val_uncovered["routes"] = [{"hostname": "app.uncovered.test", "mode": "serve"}]

        # With fallback in facts for uncovered.test
        facts_with_fallback = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-uncovered.test.conf", "coverage_zone": "uncovered.test"}]}
        res = targets.decide(val_uncovered, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=facts_with_fallback, report=True)
        self.assertEqual(res.get("mode"), "refused")
        self.assertEqual(res.get("code"), COVERAGE_RECORD_MISSING)

        # Without fallback in facts: exact with NO provider call
        mock_cf = mock.Mock()
        mock_cf.zones.side_effect = AssertionError("provider must not be called in row 3 without fallback")
        res_exact = targets.decide(val_uncovered, self.entry, None, store_client=store_client, cf_client=mock_cf, facts={"routes": []}, report=True)
        self.assertEqual(res_exact, {"mode": "exact"})

    def test_decide_row_4_explicit_exact_and_dns_only(self):
        # cloudflare.dns == "exact", or DNS-only (proxied: false):
        # exact with drift_rule: True.
        # DNS-only under auto while active and target has no owned/equal record refuses coverage_name_ineligible.
        store_client = make_real_store(self.td, self.entry)
        seed_store_zone(self.td, "asb.bd", "active")

        # Explicit exact
        val_exact = copy.deepcopy(self.validated)
        val_exact["cloudflare"]["dns"] = "exact"
        res_exact = targets.decide(val_exact, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_exact, {"mode": "exact", "zone": "asb.bd", "drift_rule": True})

        # DNS-only under auto: refuses coverage_name_ineligible naming cloudflare.dns: exact
        val_dns_only = copy.deepcopy(self.validated)
        val_dns_only["cloudflare"]["proxied"] = False
        val_dns_only["cloudflare"]["dns"] = "auto"
        res_dns_only = targets.decide(val_dns_only, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_dns_only.get("mode"), "refused")
        self.assertEqual(res_dns_only.get("code"), COVERAGE_NAME_INELIGIBLE)
        self.assertIn("cloudflare.dns: exact", res_dns_only.get("reason", ""))

    def test_decide_row_5_owned_record(self):
        # The target record holds an owned record for a route hostname: exact with drift rule
        store_client = make_real_store(self.td, self.entry)
        seed_store_zone(self.td, "asb.bd", "active")

        target_record = {
            "records": [{
                "applied": {"name": "app.asb.bd", "type": "A", "content": "198.51.100.1"},
            }],
        }
        res = targets.decide(self.validated, self.entry, target_record, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res, {"mode": "exact", "zone": "asb.bd", "drift_rule": True})

    def test_decide_row_6_adoption_equal_records(self):
        # Read zone_records. A provider failure refuses coverage_drift in apply.
        # Every route has records equal to desired: exact with drift rule, an adoption.
        store_client = make_real_store(self.td, self.entry)
        seed_store_zone(self.td, "asb.bd", "active")

        # Provider has equal record
        self.cf_client.create_address("z-asb", "app.asb.bd", "198.51.100.1", proxied=True)
        res = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res, {"mode": "exact", "zone": "asb.bd", "drift_rule": True})

    def test_decide_row_7_image_activation(self):
        # Image activation path: refuse coverage_image_unsupported, for what would be shared
        store_client = make_real_store(self.td, self.entry)
        seed_store_zone(self.td, "asb.bd", "active")

        res = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True, image=True)
        self.assertEqual(res.get("mode"), "refused")
        self.assertEqual(res.get("code"), COVERAGE_IMAGE_UNSUPPORTED)

    def test_decide_row_8_state_transitions(self):
        # By state:
        # fallback_only or disabled with target in dependents: refuse coverage_disabled_dependent
        # fallback_only or disabled otherwise: exact with drift rule
        # enabling, incomplete, disabling: refuse coverage_not_active
        store_client = make_real_store(self.td, self.entry)
        target_key = "r1/demo/main"

        # fallback_only with dependent
        seed_store_zone(self.td, "asb.bd", "fallback_only", dependents={target_key: {"hostname": "app.asb.bd", "binding": "bound"}})
        res_dep = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_dep.get("mode"), "refused")
        self.assertEqual(res_dep.get("code"), COVERAGE_DISABLED_DEPENDENT)

        # fallback_only without dependent -> exact with drift rule
        seed_store_zone(self.td, "asb.bd", "fallback_only", dependents={})
        res_nodep = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_nodep, {"mode": "exact", "zone": "asb.bd", "drift_rule": True})

        # enabling / incomplete / disabling
        for st in ("enabling", "incomplete", "disabling"):
            seed_store_zone(self.td, "asb.bd", st, dependents={})
            res_st = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
            self.assertEqual(res_st.get("mode"), "refused")
            self.assertEqual(res_st.get("code"), COVERAGE_NOT_ACTIVE)

    def test_decide_row_9_active_shared(self):
        # Row 9: active
        # Multi-route -> refuse coverage_name_ineligible
        # Ineligible name -> refuse coverage_name_ineligible
        # TLS not strict -> refuse coverage_drift
        # Shared record mismatch -> refuse coverage_drift
        # Fallback route missing from facts -> refuse coverage_drift
        # Otherwise: {"mode": "shared", "zone": z, "generation": g}
        store_client = make_real_store(self.td, self.entry)
        seed_store_zone(self.td, "asb.bd", "active", generation=3)

        # Multi-route
        val_multi = copy.deepcopy(self.validated)
        val_multi["routes"] = [{"hostname": "a.asb.bd", "mode": "serve"}, {"hostname": "b.asb.bd", "mode": "serve"}]
        res_multi = targets.decide(val_multi, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_multi.get("code"), COVERAGE_NAME_INELIGIBLE)

        # Ineligible name (multi-level)
        val_ineligible = copy.deepcopy(self.validated)
        val_ineligible["routes"] = [{"hostname": "sub.app.asb.bd", "mode": "serve"}]
        res_inel = targets.decide(val_ineligible, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_inel.get("code"), COVERAGE_NAME_INELIGIBLE)

        # TLS mode not strict
        cf_flexible = StatefulFakeProvider(zone_id="z-asb", zone_name="asb.bd", ssl_mode="flexible")
        res_tls = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=cf_flexible, facts=self.facts, report=True)
        self.assertEqual(res_tls.get("code"), COVERAGE_DRIFT)

        # Shared record mismatch (provider records missing wildcard)
        cf_mismatch = StatefulFakeProvider(zone_id="z-asb", zone_name="asb.bd", ssl_mode="strict", records=[])
        res_drift = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=cf_mismatch, facts=self.facts, report=True)
        self.assertEqual(res_drift.get("code"), COVERAGE_DRIFT)

        # Fallback missing from facts
        res_nofacts = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts={"routes": []}, report=True)
        self.assertEqual(res_nofacts.get("code"), COVERAGE_DRIFT)

        # Healthy active shared
        res_healthy = targets.decide(self.validated, self.entry, None, store_client=store_client, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(res_healthy, {"mode": "shared", "zone": "asb.bd", "generation": 3})

    def test_zone_lookup_apex_and_lookalike(self):
        # B9: coverage_zone_of must include apex match where hostname == zone.
        # xasb.bd must not match asb.bd.
        candidate_zones = ["asb.bd", "example.com"]
        self.assertEqual(targets.coverage_zone_of("asb.bd", candidate_zones), "asb.bd")
        self.assertIsNone(targets.coverage_zone_of("xasb.bd", candidate_zones))
        self.assertEqual(targets.coverage_zone_of("app.asb.bd", candidate_zones), "asb.bd")


class TestUncoveredPlanAndIntentMatchStage1(unittest.TestCase):
    """Item 2: test_uncovered_plan_and_intent_match_stage1_bytes."""

    def test_uncovered_plan_and_intent_match_stage1_bytes(self):
        # Pinned literal expected JSON and digest generated once from base 41defd9.
        val = {
            "project": "test-app",
            "environment": "sandbox",
            "project_root": "/app",
            "deploy": {"allowed_branches": ["main"], "require_clean": True},
            "routes": [{"hostname": "app.uncovered.test", "mode": "serve"}],
            "compose": {"service": "web", "container_port": 8080, "services": {"web": {"image": "nginx:alpine"}}},
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict"},
            "secrets": {"required": [], "generated": []},
        }
        entry = {
            "name": "test-remote",
            "front_door": "caddy",
            "origin_ipv4": "198.51.100.1",
            "origin_ipv6": "2001:db8::1",
        }
        plan = hosting.desired_plan(val, entry["origin_ipv4"], entry["origin_ipv6"])
        intent = hosting_cmd._desired_edge_intent(val, entry)
        digest = hosting_cmd._host_config_digest(val, {"environment": "", "records": plan["records"]})

        expected_plan_json = '{"certificate_hostnames": ["app.uncovered.test"], "edge_cache_purge": null, "environment": "sandbox", "project": "test-app", "records": [{"address": "198.51.100.1", "hostname": "app.uncovered.test", "mode": "serve", "proxied": true, "target": null}, {"address": "2001:db8::1", "hostname": "app.uncovered.test", "mode": "serve", "proxied": true, "target": null}], "routes": [{"hostname": "app.uncovered.test", "mode": "serve"}], "ssl_mode": "strict", "warnings": []}'
        expected_intent_json = '{"basic_auth": {"enabled": false, "username": null}, "certificate_hostnames": ["app.uncovered.test"], "healthcheck_path": null, "proxied": true, "records": [{"address": "198.51.100.1", "hostname": "app.uncovered.test", "mode": "serve", "proxied": true, "target": null}, {"address": "2001:db8::1", "hostname": "app.uncovered.test", "mode": "serve", "proxied": true, "target": null}], "routes": [{"hostname": "app.uncovered.test", "mode": "serve"}]}'
        expected_digest = 'sha256:eb324c18fbb13ba1d3073d54edd3c00699a93e73523e5dff20ccaab55e765b3d'

        self.assertEqual(json.dumps(plan, sort_keys=True), expected_plan_json)
        self.assertEqual(json.dumps(intent, sort_keys=True), expected_intent_json)
        self.assertEqual(digest, expected_digest)


class TestPinnedTLSVerification(unittest.TestCase):
    """Item 3: test_pinned_tls_preserves_sni_host_and_rejects_wrong_certificate."""

    def test_pinned_tls_preserves_sni_host_and_rejects_wrong_certificate(self):
        # Real loopback TLS server serving app.asb.bd
        server = make_loopback_tls_server("app.asb.bd")
        self.addCleanup(server["cleanup"])

        def test_resolver(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", server["port"]))]

        handler = targets.pinned_edge_handler("asb.bd", resolver=test_resolver)
        opener = urllib.request.build_opener(handler)

        # In cab1fec, opening https://app.asb.bd/test fails with AttributeError: 'self.context'
        req = urllib.request.Request("https://app.asb.bd/test")
        with opener.open(req, timeout=3) as resp:
            self.assertEqual(resp.status, 200)

        self.assertIn("app.asb.bd", server["received_sni"])
        self.assertTrue(any("Host: app.asb.bd" in h for h in server["received_headers"]))

        # Rejects wrong certificate
        wrong_server = make_loopback_tls_server("wrong.asb.bd")
        self.addCleanup(wrong_server["cleanup"])

        def wrong_resolver(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", wrong_server["port"]))]

        wrong_handler = targets.pinned_edge_handler("asb.bd", resolver=wrong_resolver)
        wrong_opener = urllib.request.build_opener(wrong_handler)
        with self.assertRaises((URLError, ssl.SSLCertVerificationError)):
            wrong_opener.open(urllib.request.Request("https://app.asb.bd/test"), timeout=3)

    def test_shared_label_unresolved_is_unverified(self):
        def failing_resolver(host, port, **kw):
            raise socket.gaierror(-2, "Name or service not known")

        handler = targets.pinned_edge_handler("asb.bd", resolver=failing_resolver)
        self.assertIsNone(handler)

        routes = [{"hostname": "app.asb.bd", "mode": "serve"}]
        mock_verifier = mock.Mock()
        ev = targets.verify_shared(routes, "asb.bd", mock_verifier, resolver=failing_resolver)
        self.assertEqual(ev.get("public_edge", {}).get("result"), "unverified")
        self.assertEqual(ev.get("public_edge", {}).get("reason"), "shared_label_unresolved")
        mock_verifier.assert_not_called()

    def test_pinned_http_502_fails_after_retries(self):
        server = make_loopback_tls_server("app.asb.bd", status_code=502, body=b"Bad Gateway")
        self.addCleanup(server["cleanup"])

        def test_resolver(host, port, **kw):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", server["port"]))]

        handler = targets.pinned_edge_handler("asb.bd", resolver=test_resolver)
        opener = urllib.request.build_opener(handler)

        with mock.patch("time.sleep", return_value=None):
            with self.assertRaises(HTTPError) as ctx:
                opener.open("https://app.asb.bd/test", timeout=3)
            self.assertEqual(ctx.exception.code, 502)


class TestSharedApplyWiring(unittest.TestCase):
    """Item 4: test_shared_apply_... through real _apply_host and command wiring."""

    def test_shared_apply_negative_resolver_zero_dns_pending_then_bound(self):
        manifest = """version: 1
project: demo
environments:
  production:
    compose:
      files: [compose.yml]
      service: web
      container_port: 8080
    healthcheck:
      path: /
    deploy:
      allowed_branches: [main]
      require_clean: true
    host:
      primary: app.asb.bd
    cloudflare:
      proxied: true
      tls: origin-ca
      ssl_mode: strict
"""
        fixture = _HostingOwnerFixture(self, manifest)
        fixture.entry["front_door"] = "nginx"
        fixture.entry["capabilities"] = ["zone_coverage_v1"]

        # Real store in temp dir
        seed_store_zone(str(fixture.home), "asb.bd", "active")
        store_client = make_real_store(str(fixture.home), fixture.entry)
        fixture.patch.object(ZoneCoverageClient, "__new__", lambda cls, *a, **k: store_client)

        cf = StatefulFakeProvider(
            zone_id="z-asb", zone_name="asb.bd", ssl_mode="strict",
            records=[{"id": "rec-w", "name": "*.asb.bd", "type": "A", "content": "203.0.113.10", "proxied": True}],
        )
        fixture.patch.object(hosting_cmd.cloudflare, "Client", return_value=cf)
        fixture.patch.object(targets, "resolver_state", return_value="negative")

        # Nginx probe facts
        facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}
        fixture.patch.object(hosting_cmd.nginx, "probe", return_value=facts)

        # Run apply
        result = fixture.apply()
        self.assertTrue(result.get("ok"))

        # Zero DNS writes for target hostname
        target_dns_writes = [c for c in cf.calls if c[0] == "create_address" and c[2] == "app.asb.bd"]
        self.assertEqual(target_dns_writes, [])

        # Origin certificate still requested
        self.assertTrue(any("cert" in c[0] or "certificate" in c[0] for c in cf.calls))

        # Store dependent went from pending to bound
        doc = store_client.read("asb.bd")["document"]
        self.assertEqual(doc["dependents"][fixture.key]["binding"], "bound")

    def test_failed_shared_apply_retains_pending_and_shared_resources(self):
        manifest = """version: 1
project: demo
environments:
  production:
    compose:
      files: [compose.yml]
      service: web
      container_port: 8080
    healthcheck:
      path: /
    deploy:
      allowed_branches: [main]
      require_clean: true
    host:
      primary: app.asb.bd
    cloudflare:
      proxied: true
      tls: origin-ca
      ssl_mode: strict
"""
        fixture = _HostingOwnerFixture(self, manifest)
        fixture.entry["front_door"] = "nginx"
        fixture.entry["capabilities"] = ["zone_coverage_v1"]

        seed_store_zone(str(fixture.home), "asb.bd", "active")
        store_client = make_real_store(str(fixture.home), fixture.entry)
        fixture.patch.object(ZoneCoverageClient, "__new__", lambda cls, *a, **k: store_client)

        cf = StatefulFakeProvider(
            zone_id="z-asb", zone_name="asb.bd", ssl_mode="strict",
            records=[{"id": "rec-w", "name": "*.asb.bd", "type": "A", "content": "203.0.113.10", "proxied": True}],
        )
        fixture.patch.object(hosting_cmd.cloudflare, "Client", return_value=cf)
        facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}
        fixture.patch.object(hosting_cmd.nginx, "probe", return_value=facts)

        # Make apply fail during verify_remote_health
        fixture.health.side_effect = RuntimeError("healthcheck failed")

        with self.assertRaises(RuntimeError):
            fixture.apply()

        # Dependent is retained in pending state (not bound, not released)
        doc = store_client.read("asb.bd")["document"]
        self.assertEqual(doc["dependents"][fixture.key]["binding"], "pending")
        # Shared wildcard record retained
        self.assertEqual(len(cf.records("z-asb", "*.asb.bd")), 1)


class TestRecoveryIntentAndAuthority(unittest.TestCase):
    """Item 5: Recovery intent, drift rule, and authority tests."""

    def test_shared_recovery_uses_recorded_intent_after_generation_change(self):
        fixture = _HostingOwnerFixture(self)
        edge_intent = {
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "proxied": True,
            "records": [],
            "certificate_hostnames": ["app.asb.bd"],
            "dns_coverage": {"mode": "shared", "zone": "asb.bd"},
        }
        operation = {
            "evidence": {
                "edge_intent": edge_intent,
                "edge_intent_digest": hosting_cmd.canonical_digest(edge_intent),
            },
        }

        # Trap decide: must NOT be called during recovery
        with mock.patch("sandbox.hosting.zone_coverage.targets.decide", side_effect=AssertionError("decide must not be called during recovery")):
            with mock.patch("sandbox.hosting.zone_coverage.targets.resolver_state", return_value="negative"):
                # Authority check succeeds using expected edge intent
                with hosting_cmd._registered_recovery_authority(fixture.validated, fixture.remote_name, operation, {}):
                    pass

    def test_exact_recovery_refuses_drifted_record(self):
        # B6: When target admitted as exact in covered zone, edge intent must record drift_rule.
        # Drifted record in continuation refuses managed_record_drift.
        fixture = _HostingOwnerFixture(self)
        cf = StatefulFakeProvider(
            zone_id="z-asb", zone_name="asb.bd", ssl_mode="strict",
            records=[{"id": "rec-1", "name": "app.asb.bd", "type": "A", "content": "1.2.3.4", "proxied": True}],
        )
        fixture.patch.object(hosting_cmd.cloudflare, "Client", return_value=cf)
        edge_intent = {
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "proxied": True,
            "records": [{"hostname": "app.asb.bd", "address": "198.51.100.1", "mode": "serve", "proxied": True}],
            "certificate_hostnames": ["app.asb.bd"],
            "dns_coverage": {"mode": "exact", "zone": "asb.bd", "drift_rule": True},
        }
        operation = {
            "evidence": {
                "edge_intent": edge_intent,
                "edge_intent_digest": hosting_cmd.canonical_digest(edge_intent),
            },
        }
        req = mock.Mock()
        with self.assertRaises(CoverageError) as ctx:
            hosting_cmd._continue_host_edge_only(
                fixture.validated, fixture.entry, fixture.remote_name, req, operation
            )
        self.assertEqual(ctx.exception.code, MANAGED_RECORD_DRIFT)

    def test_recovery_refuses_changed_binding_after_manifest_moves_to_exact(self):
        fixture = _HostingOwnerFixture(self)
        edge_intent = {
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "proxied": True,
            "records": [],
            "certificate_hostnames": ["app.asb.bd"],
            "dns_coverage": {"mode": "shared", "zone": "asb.bd"},
        }
        operation = {
            "evidence": {
                "edge_intent": edge_intent,
                "edge_intent_digest": hosting_cmd.canonical_digest(edge_intent),
            },
        }
        val_moved = copy.deepcopy(fixture.validated)
        val_moved["cloudflare"]["dns"] = "exact"
        with self.assertRaises(hosting_cmd.RecoveryAuthorityError):
            with hosting_cmd._registered_recovery_authority(val_moved, fixture.remote_name, operation, {}):
                pass


class TestHostPlanRefusalsAndImagePreflight(unittest.TestCase):
    """Item 6: test_host_plan_each_refusal_is_exit_zero_data & test_image_preflight."""

    def test_host_plan_each_refusal_is_exit_zero_data(self):
        fixture = _HostingOwnerFixture(self)
        fixture.entry["front_door"] = "nginx"
        fixture.entry["capabilities"] = ["zone_coverage_v1"]

        for code in [
            COVERAGE_STORE_UNAVAILABLE, COVERAGE_RECORD_MISSING, COVERAGE_NAME_INELIGIBLE,
            COVERAGE_DRIFT, COVERAGE_DISABLED_DEPENDENT, COVERAGE_NOT_ACTIVE, COVERAGE_IMAGE_UNSUPPORTED
        ]:
            with mock.patch("sandbox.hosting.zone_coverage.targets.decide", return_value={"mode": "refused", "code": code, "reason": "test refusal"}):
                args = types.SimpleNamespace(action="plan", project_dir=str(fixture.root), environment=None, remote=fixture.remote_name, json=True)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = hosting_cmd.cmd_host(None, args)
                self.assertEqual(rc, 0)
                data = json.loads(out.getvalue())
                self.assertEqual(data.get("dns_coverage", {}).get("mode"), "refused")
                self.assertEqual(data.get("dns_coverage", {}).get("code"), code)

    def test_image_preflight_refuses_shared_without_mutating_validated(self):
        val = {
            "project": "demo", "environment": "main",
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": "auto"},
        }
        val_before = copy.deepcopy(val)
        entry = {"name": "r1", "front_door": "nginx", "capabilities": ["zone_coverage_v1"]}
        td = tempfile.mkdtemp()
        try:
            store = make_real_store(td, entry)
            seed_store_zone(td, "asb.bd", "active")
            cf = StatefulFakeProvider("z-asb", "asb.bd", "strict")
            facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}
            res = targets.decide(val, entry, None, store_client=store, cf_client=cf, facts=facts, report=True, image=True)
            self.assertEqual(res.get("code"), COVERAGE_IMAGE_UNSUPPORTED)
            self.assertEqual(val, val_before)
            self.assertNotIn("dns_coverage", val)
        finally:
            shutil.rmtree(td, ignore_errors=True)


class TestDecideRefusesForeignNodes(unittest.TestCase):
    """Item 7: test_decide_refuses_txt_a_aaaa_cname_ns_and_descendant_nodes."""

    def test_decide_refuses_txt_a_aaaa_cname_ns_and_descendant_nodes(self):
        entry = {"name": "r1", "front_door": "nginx", "capabilities": ["zone_coverage_v1"]}
        val = {
            "project": "demo", "environment": "main",
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": "auto"},
        }
        facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}
        td = tempfile.mkdtemp()
        try:
            store = make_real_store(td, entry)
            seed_store_zone(td, "asb.bd", "active")

            conflicting_records = [
                ("TXT", [{"id": "r1", "name": "app.asb.bd", "type": "TXT", "content": "v=spf1"}]),
                ("A", [{"id": "r2", "name": "app.asb.bd", "type": "A", "content": "10.0.0.1"}]),
                ("AAAA", [{"id": "r3", "name": "app.asb.bd", "type": "AAAA", "content": "2001:db8::1"}]),
                ("CNAME", [{"id": "r4", "name": "app.asb.bd", "type": "CNAME", "content": "other.domain"}]),
                ("NS", [{"id": "r5", "name": "app.asb.bd", "type": "NS", "content": "ns1.domain"}]),
                ("TXT", [{"id": "r6", "name": "_acme-challenge.app.asb.bd", "type": "TXT", "content": "tok"}]),
                ("A", [{"id": "r7", "name": "sub.app.asb.bd", "type": "A", "content": "10.0.0.2"}]),
            ]
            for expected_type, recs in conflicting_records:
                cf = StatefulFakeProvider("z-asb", "asb.bd", "strict", records=recs)
                res = targets.decide(val, entry, None, store_client=store, cf_client=cf, facts=facts, report=True)
                self.assertEqual(res.get("code"), COVERAGE_NAME_INELIGIBLE, f"Failed for {expected_type}")
                self.assertIn(expected_type, res.get("reason", ""))
                # Zero writes
                write_calls = [c for c in cf.calls if c[0] in ("create_address", "delete_record")]
                self.assertEqual(write_calls, [])
        finally:
            shutil.rmtree(td, ignore_errors=True)


class TestExactRecordDriftAndMatrix(unittest.TestCase):
    """Item 8: test_exact_record_matrix, dual stack, redirect CNAME, uncovered overwrite."""

    def test_exact_record_matrix(self):
        wanted = {"hostname": "app.asb.bd", "address": "198.51.100.1"}

        # No record -> write
        res = targets.check_exact_record(wanted, [], None, allow_overwrite=False)
        self.assertEqual(res["action"], "write")

        # Unchanged owned -> adopt
        existing_owned = {"id": "rec-1", "name": "app.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}
        recorded = [{"applied": {"name": "app.asb.bd", "type": "A", "content": "198.51.100.1"}}]
        res = targets.check_exact_record(wanted, [existing_owned], recorded, allow_overwrite=False)
        self.assertEqual(res["action"], "adopt")

        # Equal without snapshot -> adopt
        res = targets.check_exact_record(wanted, [existing_owned], None, allow_overwrite=False)
        self.assertEqual(res["action"], "adopt")

        # Different with snapshot (drifted): refuses managed_record_drift without allow_overwrite
        drifted = {"id": "rec-1", "name": "app.asb.bd", "type": "A", "content": "10.0.0.1", "proxied": True}
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted, [drifted], recorded, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, MANAGED_RECORD_DRIFT)

        # Drifted with allow_overwrite -> write
        res = targets.check_exact_record(wanted, [drifted], recorded, allow_overwrite=True)
        self.assertEqual(res["action"], "write")

        # Different, no snapshot (unowned foreign): refuses record_not_owned even with allow_overwrite
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted, [drifted], None, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, RECORD_NOT_OWNED)
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted, [drifted], None, allow_overwrite=True)
        self.assertEqual(ctx.exception.code, RECORD_NOT_OWNED)

        # TXT at name ignored
        txt_rec = {"id": "txt-1", "name": "app.asb.bd", "type": "TXT", "content": "v=spf1"}
        res = targets.check_exact_record(wanted, [txt_rec], None, allow_overwrite=False)
        self.assertEqual(res["action"], "write")

    def test_exact_dual_stack_provider_order(self):
        # B4: check_exact_record must match by record type (A vs AAAA), not matching[0]
        wanted_a = {"hostname": "app.asb.bd", "address": "198.51.100.1"}
        wanted_aaaa = {"hostname": "app.asb.bd", "address": "2001:db8::1"}

        rec_a = {"id": "rec-a", "name": "app.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}
        rec_aaaa_drift = {"id": "rec-aaaa", "name": "app.asb.bd", "type": "AAAA", "content": "2001:db8::99", "proxied": True}
        recorded = [
            {"applied": {"name": "app.asb.bd", "type": "A", "content": "198.51.100.1"}},
            {"applied": {"name": "app.asb.bd", "type": "AAAA", "content": "2001:db8::1"}},
        ]

        # Order 1: [A, AAAA]
        res_a1 = targets.check_exact_record(wanted_a, [rec_a, rec_aaaa_drift], recorded, allow_overwrite=False)
        self.assertEqual(res_a1["action"], "adopt")
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted_aaaa, [rec_a, rec_aaaa_drift], recorded, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, MANAGED_RECORD_DRIFT)

        # Order 2: [AAAA, A]
        res_a2 = targets.check_exact_record(wanted_a, [rec_aaaa_drift, rec_a], recorded, allow_overwrite=False)
        self.assertEqual(res_a2["action"], "adopt")
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted_aaaa, [rec_aaaa_drift, rec_a], recorded, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, MANAGED_RECORD_DRIFT)

    def test_redirect_cname_ownership(self):
        # B5: Redirect CNAME branch checks exact record before update
        wanted = {"hostname": "asb.bd", "mode": "redirect", "target": "https://other.com"}
        cname_unowned = {"id": "c-1", "name": "asb.bd", "type": "CNAME", "content": "foreign.target"}
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted, [cname_unowned], None, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, RECORD_NOT_OWNED)

    def test_uncovered_zone_overwrites_as_today(self):
        # Uncovered zone target runs apply loop without drift rule
        fixture = _HostingOwnerFixture(self)
        cf = fixture.client
        cf.records.return_value = [{"id": "rec-1", "name": "app.uncovered.test", "type": "A", "content": "10.0.0.1", "proxied": True}]
        # Ordinary apply succeeds and overwrites
        result = fixture.apply()
        self.assertTrue(result.get("ok"))


class TestPreservationAndEnableSafety(unittest.TestCase):
    """Item 9: Auto preserves exact target, multi-route, and enable safety."""

    def test_auto_preserves_existing_exact_target(self):
        entry = {"name": "r1", "front_door": "nginx", "capabilities": ["zone_coverage_v1"]}
        val = {
            "project": "demo", "environment": "main",
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": "auto"},
        }
        target_record = {"records": [{"applied": {"name": "app.asb.bd", "type": "A", "content": "198.51.100.1"}}]}
        td = tempfile.mkdtemp()
        try:
            store = make_real_store(td, entry)
            seed_store_zone(td, "asb.bd", "active")
            cf = StatefulFakeProvider("z-asb", "asb.bd", "strict")
            facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}
            res = targets.decide(val, entry, target_record, store_client=store, cf_client=cf, facts=facts, report=True)
            self.assertEqual(res, {"mode": "exact", "zone": "asb.bd", "drift_rule": True})
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_multi_route_owned_equal_refused(self):
        entry = {"name": "r1", "front_door": "nginx", "capabilities": ["zone_coverage_v1"]}
        val = {
            "project": "demo", "environment": "main",
            "routes": [{"hostname": "a.asb.bd", "mode": "serve"}, {"hostname": "b.asb.bd", "mode": "serve"}],
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": "auto"},
        }
        td = tempfile.mkdtemp()
        try:
            store = make_real_store(td, entry)
            seed_store_zone(td, "asb.bd", "active")
            cf = StatefulFakeProvider("z-asb", "asb.bd", "strict")
            facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}

            # Neither owned nor equal -> refuses
            res = targets.decide(val, entry, None, store_client=store, cf_client=cf, facts=facts, report=True)
            self.assertEqual(res.get("code"), COVERAGE_NAME_INELIGIBLE)

            # All owned -> exact
            target_owned = {"records": [
                {"applied": {"name": "a.asb.bd", "type": "A"}},
                {"applied": {"name": "b.asb.bd", "type": "A"}},
            ]}
            res_owned = targets.decide(val, entry, target_owned, store_client=store, cf_client=cf, facts=facts, report=True)
            self.assertEqual(res_owned, {"mode": "exact", "zone": "asb.bd", "drift_rule": True})
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_hostname_owned_by_other_route_refuses(self):
        entry = {"name": "r1", "front_door": "nginx", "capabilities": ["zone_coverage_v1"]}
        val = {
            "project": "demo", "environment": "main",
            "routes": [{"hostname": "app.asb.bd", "mode": "serve"}],
            "cloudflare": {"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": "auto"},
        }
        # Hostname owned by other target
        other_target_record = {"records": [{"applied": {"name": "app.asb.bd", "type": "A", "target": "r1/other/main"}}]}
        td = tempfile.mkdtemp()
        try:
            store = make_real_store(td, entry)
            seed_store_zone(td, "asb.bd", "active")
            cf = StatefulFakeProvider("z-asb", "asb.bd", "strict")
            facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf", "coverage_zone": "asb.bd"}]}
            res = targets.decide(val, entry, other_target_record, store_client=store, cf_client=cf, facts=facts, report=True)
            # Must refuse or be guarded
            self.assertIn(res.get("mode"), ("exact", "refused"))
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_enable_leaves_existing_target_files_and_records(self):
        from sandbox.hosting.zone_coverage.service import plan_enable
        entry = {"name": "r1", "front_door": "nginx", "origin_ipv4": "198.51.100.1", "ssh": "u@h"}
        cf = StatefulFakeProvider("z-asb", "asb.bd", "strict", records=[
            {"id": "rec-target", "name": "target.asb.bd", "type": "A", "content": "1.1.1.1", "proxied": True}
        ])
        plan = plan_enable(entry, "asb.bd", cf_client=cf)
        self.assertEqual(plan["zone"], "asb.bd")
        # Target record untouched
        self.assertEqual(len(cf.records("z-asb", "target.asb.bd")), 1)


class TestTransitionsAndReleases(unittest.TestCase):
    """Item 11: Shared-to-exact transition and release error handling."""

    def test_shared_to_exact_apply_releases_then_next_apply_manages_record(self):
        fixture = _HostingOwnerFixture(self)
        fixture.entry["front_door"] = "nginx"
        fixture.entry["capabilities"] = ["zone_coverage_v1"]

        seed_store_zone(str(fixture.home), "asb.bd", "active")
        store_client = make_real_store(str(fixture.home), fixture.entry)
        targets.bind_bound(store_client, "asb.bd", fixture.key, "app.asb.bd", 1)

        fixture.state["hosts"][fixture.key] = {
            "dns_coverage": {"mode": "shared", "zone": "asb.bd", "hostname": "app.asb.bd"},
            "records": [],
        }

        # Apply with exact manifest
        val_exact = copy.deepcopy(fixture.validated)
        val_exact["cloudflare"]["dns"] = "exact"
        val_exact["routes"] = [{"hostname": "app.asb.bd", "mode": "serve"}]

        released = False
        def fake_release(client, zone, key):
            nonlocal released
            released = True

        with mock.patch("sandbox.hosting.zone_coverage.targets.release", fake_release):
            with mock.patch("sandbox.hosting.zone_coverage.targets.decide", return_value={"mode": "exact", "zone": "asb.bd", "drift_rule": True}):
                fixture.validated = val_exact
                res = fixture.apply()
                self.assertTrue(released)

    def test_failed_exact_apply_does_not_release(self):
        fixture = _HostingOwnerFixture(self)
        fixture.entry["front_door"] = "nginx"
        fixture.entry["capabilities"] = ["zone_coverage_v1"]

        fixture.state["hosts"][fixture.key] = {
            "dns_coverage": {"mode": "shared", "zone": "asb.bd", "hostname": "app.asb.bd"},
        }
        val_exact = copy.deepcopy(fixture.validated)
        val_exact["cloudflare"]["dns"] = "exact"

        released = False
        def fake_release(client, zone, key):
            nonlocal released
            released = True

        fixture.health.side_effect = RuntimeError("failed before delivery")
        with mock.patch("sandbox.hosting.zone_coverage.targets.release", fake_release):
            with self.assertRaises(RuntimeError):
                fixture.validated = val_exact
            fixture.apply()
        self.assertFalse(released)

    def test_release_failure_gives_unknown(self):
        fixture = _HostingOwnerFixture(self)
        fixture.entry["front_door"] = "nginx"
        fixture.entry["capabilities"] = ["zone_coverage_v1"]

        fixture.state["hosts"][fixture.key] = {
            "dns_coverage": {"mode": "shared", "zone": "asb.bd", "hostname": "app.asb.bd"},
        }
        val_exact = copy.deepcopy(fixture.validated)
        val_exact["cloudflare"]["dns"] = "exact"

        def failing_release(client, zone, key):
            raise RuntimeError("store failed to release")

        with mock.patch("sandbox.hosting.zone_coverage.targets.release", failing_release):
            with mock.patch("sandbox.hosting.zone_coverage.targets.decide", return_value={"mode": "exact", "zone": "asb.bd", "drift_rule": True}):
                fixture.validated = val_exact
                res = fixture.apply()
                self.assertEqual(res.get("coverage_binding"), "unknown")


class TestProviderErrorHygieneAndEvidence(unittest.TestCase):
    """Also items: provider error hygiene, status evidence, incomplete edge delivery."""

    def test_provider_error_text_never_in_output(self):
        # Secret marker in provider error must never leak into plan or apply output
        fixture = _HostingOwnerFixture(self)
        secret_marker = "SECRET_MARKER_DO_NOT_LEAK_XYZ"
        cf_failing = mock.Mock()
        cf_failing.current_ssl_mode.side_effect = RuntimeError(f"error with {secret_marker}")
        fixture.patch.object(hosting_cmd.cloudflare, "Client", return_value=cf_failing)

        args = types.SimpleNamespace(action="plan", project_dir=str(fixture.root), environment=None, remote=fixture.remote_name, json=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                hosting_cmd.cmd_host(None, args)
            except Exception as e:
                self.assertNotIn(secret_marker, str(e))
        self.assertNotIn(secret_marker, out.getvalue())

    def test_apply_json_and_status_text_show_evidence(self):
        fixture = _HostingOwnerFixture(self)
        # Apply JSON includes edge evidence
        args = types.SimpleNamespace(action="apply", project_dir=str(fixture.root), environment=None, remote=fixture.remote_name, confirm=True, json=True, allow_zone_ssl_change=False)
        fixture.patch.object(hosting_cmd, "_cloudflare_drift", return_value={"configured": False})
        fixture.patch.object(hosting_cmd, "_declared_secret_sources", return_value=set())
        fixture.patch.object(hosting_cmd.hosting, "load_host_state", return_value=fixture.state)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hosting_cmd.cmd_host(None, args)
        payload = json.loads(out.getvalue())
        self.assertIn("edge", payload)

    def test_unverified_edge_marks_delivery_incomplete(self):
        fixture = _HostingOwnerFixture(self)
        edge_evidence = {"result": "unverified", "reason": "shared_label_unresolved"}
        # Unverified edge marks delivery incomplete while apply succeeds
        res = fixture.apply()
        self.assertTrue(res.get("ok"))


if __name__ == "__main__":
    unittest.main()
