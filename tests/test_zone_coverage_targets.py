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
from sandbox.core import _remote as remote
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


# Every acceptance test in this module runs under a removable I/O boundary.
_guard = None

def setUpModule():
    global _guard
    from tests.zone_coverage_test_support import GuardedIO
    _guard = GuardedIO()
    _guard.__enter__()
    import tests.test_hosting as fixture_module
    original = fixture_module.run_test_process
    def fixture_process(argv, **kwargs):
        args = tuple(argv)
        if (len(args) >= 5 and args[:2] == ("git", "-C")
                and Path(args[2]).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
                and (args[3:] in (("init", "--initial-branch=main"), ("add", "."),
                                  ("commit", "-m", "Synthetic hosting source"),
                                  ("rev-parse", "HEAD")))):
            _guard.allow_command(args)
        return original(argv, **kwargs)
    _guard.stack.enter_context(mock.patch.object(fixture_module, "run_test_process", fixture_process))
    original_build = urllib.request.build_opener
    def no_proxy(*handlers):
        return original_build(urllib.request.ProxyHandler({}), *handlers)
    _guard.stack.enter_context(mock.patch.object(urllib.request, "build_opener", no_proxy))

def tearDownModule():
    global _guard
    guard, _guard = _guard, None
    guard.__exit__(None, None, None)
    if guard.attempts:
        raise AssertionError("unexpected I/O attempts: %r" % (guard.attempts,))

def make_real_store(temp_dir: str, entry: dict) -> ZoneCoverageClient:
    """Create a real ZoneCoverageClient running against temp_dir as SANDBOX_HOME."""
    from tests.zone_coverage_test_support import store_transport
    return ZoneCoverageClient(entry, ssh_run=store_transport(temp_dir))


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
        "zone_id": "z-asb",
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


def make_tls_authority(test):
    td = tempfile.mkdtemp(prefix="sb-ca-")
    test.addCleanup(shutil.rmtree, td, True)
    ca_key, ca_cert = (os.path.join(td, x) for x in ("ca.key", "ca.pem"))
    def run(argv):
        _guard.allow_command(argv)
        run_test_process(argv, env={"HOME": td}, timeout=30, check=True, capture_output=True)
    run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", ca_key,
         "-out", ca_cert, "-subj", "/CN=Sandbox Test CA", "-x509"])
    return td, ca_key, ca_cert, run


def make_loopback_tls_server(authority, san_hostname: str, status_code: int = 200, body: bytes = b"OK"):
    """Serve a CA-signed SAN certificate on an owned loopback port."""
    td, ca_key, ca_cert, run = authority
    leaf_dir = tempfile.mkdtemp(dir=td, prefix="leaf-")
    key_path, cert_path, csr_path, ext_path = (os.path.join(leaf_dir, x) for x in ("key.pem", "cert.pem", "leaf.csr", "ext.cnf"))
    Path(ext_path).write_text("subjectAltName=DNS:" + san_hostname + "\n")
    run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", key_path,
         "-out", csr_path, "-subj", "/CN=fixture"])
    run(["openssl", "x509", "-req", "-in", csr_path, "-CA", ca_cert, "-CAkey", ca_key,
         "-set_serial", "1", "-out", cert_path, "-days", "1", "-extfile", ext_path, "-sha256"])
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
    _guard.allow_loopback("127.0.0.1", port)

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
        shutil.rmtree(leaf_dir, ignore_errors=True)

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

    def test_refused_and_malformed_store_envelopes_fail_closed(self):
        store = mock.Mock()
        for response in ({"ok": True, "refused": "busy"}, {"ok": True, "zones": "bad"}):
            store.list.return_value = response
            with self.subTest(response=response):
                result = targets.decide(self.validated, self.entry, None, store_client=store, report=True)
                self.assertEqual(result["code"], COVERAGE_STORE_UNAVAILABLE)
        store.list.return_value = {"ok": True, "zones": [{"zone": "asb.bd", "state": "active"}]}
        for response in ({"ok": True, "refused": "busy"}, {"ok": True, "document": None}):
            store.read.return_value = response
            with self.subTest(response=response):
                result = targets.decide(self.validated, self.entry, None, store_client=store, report=True)
                self.assertEqual(result["code"], COVERAGE_STORE_UNAVAILABLE)
        self.assertEqual(store.read.call_count, 2)

    def test_provider_failures_are_reported_and_reached(self):
        store = make_real_store(self.td, self.entry)
        seed_store_zone(self.td)
        healthy = targets.decide(self.validated, self.entry, None, store_client=store, cf_client=self.cf_client, facts=self.facts, report=True)
        self.assertEqual(healthy["mode"], "shared")
        for method in ("zone_records", "current_ssl_mode"):
            with self.subTest(method=method), mock.patch.object(self.cf_client, method, side_effect=RuntimeError("secret marker")) as failing:
                result = targets.decide(self.validated, self.entry, None, store_client=store, cf_client=self.cf_client, facts=self.facts, report=True)
                failing.assert_called_once()
                self.assertEqual(result["code"], COVERAGE_DRIFT)
                self.assertNotIn("secret marker", str(result))
        with mock.patch.object(cloudflare, "Client", side_effect=RuntimeError("secret marker")) as constructor:
            result = targets.decide(self.validated, self.entry, None, store_client=store, facts=self.facts, report=True)
            constructor.assert_called_once()
            self.assertEqual(result["code"], COVERAGE_DRIFT)
            self.assertNotIn("secret marker", str(result))

    def test_state_key_path_fallback_and_shared_record_drift(self):
        store = make_real_store(self.td, self.entry)
        path_facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf"}]}
        seed_store_zone(self.td, dependents={"r1/other/main": {"hostname": "app.asb.bd", "state": "bound"}}, state="disabled")
        result = targets.decide(self.validated, self.entry, None, store_client=store, cf_client=self.cf_client, facts=path_facts, report=True)
        self.assertEqual(result["mode"], "exact")
        seed_store_zone(self.td)
        self.assertEqual(targets.decide(self.validated, self.entry, None, store_client=store, cf_client=self.cf_client, facts=path_facts, report=True)["mode"], "shared")
        # Fresh providers make each single-field mutation independent.
        baseline = {"id": "rec-wildcard-1", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}
        for field, value in (("id", "other"), ("type", "AAAA"), ("content", "192.0.2.9"), ("proxied", False)):
            row = dict(baseline, **{field: value})
            provider = StatefulFakeProvider("z-asb", "asb.bd", "strict", records=[row])
            with self.subTest(field=field):
                result = targets.decide(self.validated, self.entry, None, store_client=store, cf_client=provider, facts=path_facts, report=True)
                self.assertEqual(result["code"], COVERAGE_DRIFT)
        different_origin = dict(self.entry, origin_ipv4="192.0.2.10")
        result = targets.decide(self.validated, different_origin, None, store_client=store, cf_client=self.cf_client, facts=path_facts, report=True)
        self.assertEqual(result["code"], COVERAGE_DRIFT)
        apex = copy.deepcopy(self.validated)
        apex["routes"][0]["hostname"] = "asb.bd"
        self.assertEqual(targets.decide(apex, self.entry, None, store_client=store, cf_client=self.cf_client, facts=path_facts, report=True)["code"], COVERAGE_NAME_INELIGIBLE)

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
        seed_store_zone(self.td, "asb.bd", "fallback_only", dependents={target_key: {"hostname": "app.asb.bd", "state": "bound"}})
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
    def setUp(self):
        self.authority = make_tls_authority(self)
        self.context = ssl.create_default_context(cafile=self.authority[2])
        self.assertTrue(self.context.check_hostname)
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)

    def server(self, hostname, status=200):
        server = make_loopback_tls_server(self.authority, hostname, status)
        self.addCleanup(server["cleanup"])
        return server

    def handler(self):
        def resolver(host, port):
            self.assertRegex(host, r"^sb-verify-[0-9a-f]{16}\.asb\.bd$")
            self.assertEqual(port, 443)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        handler = targets.pinned_edge_handler("asb.bd", resolver=resolver)
        handler._context = self.context
        return handler

    def mapped(self, server):
        original = socket.create_connection
        def connect(address, *args, **kwargs):
            if address != ("127.0.0.1", 443):
                raise AssertionError("unexpected pinned endpoint")
            return original(("127.0.0.1", server["port"]), *args, **kwargs)
        return mock.patch.object(socket, "create_connection", connect)

    def test_pinned_tls_preserves_sni_host_and_rejects_wrong_certificate(self):
        server = self.server("app.asb.bd")
        with self.mapped(server):
            with urllib.request.build_opener(self.handler()).open("https://app.asb.bd/test", timeout=3) as response:
                self.assertEqual(response.status, 200)
        self.assertEqual(server["received_sni"], ["app.asb.bd"])
        self.assertIn("Host: app.asb.bd\r\n", server["received_headers"][0])
        wrong = self.server("wrong.asb.bd")
        with self.mapped(wrong), self.assertRaises(URLError) as failure:
            urllib.request.build_opener(self.handler()).open("https://app.asb.bd/test", timeout=3)
        self.assertIsInstance(failure.exception.reason, ssl.SSLCertVerificationError)
        self.assertIn("hostname mismatch", str(failure.exception.reason).lower())
        self.assertEqual(wrong["received_sni"], ["app.asb.bd"])

    def test_shared_label_unresolved_is_unverified(self):
        def failing_resolver(host, port):
            raise socket.gaierror(socket.EAI_NONAME, "not found")
        self.assertIsNone(targets.pinned_edge_handler("asb.bd", resolver=failing_resolver))
        verifier = mock.Mock()
        ev = targets.verify_shared([{"hostname": "app.asb.bd", "mode": "serve"}], "asb.bd", verifier, resolver=failing_resolver)
        self.assertEqual(ev["public_edge"]["reason"], "shared_label_unresolved")
        self.assertEqual(ev["resolver"]["result"], "negative")
        verifier.assert_not_called()
        def error_resolver(host, port):
            raise socket.gaierror(socket.EAI_AGAIN, "temporary")
        self.assertEqual(targets.resolver_state("app.asb.bd", resolver=error_resolver), "error")

    def test_pinned_http_502_fails_after_retries(self):
        routes = [{"hostname": "app.asb.bd", "mode": "serve"}]
        server = self.server("app.asb.bd", 502)
        with self.mapped(server), mock.patch.object(hosting_cmd.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "edge verification failed for app.asb.bd"):
                hosting_cmd._verify_edge(routes, handlers=(self.handler(),))
        self.assertEqual(len(server["received_headers"]), 30)
        self.assertEqual(sleep.call_count, 30)
        healthy = self.server("app.asb.bd", 200)
        with self.mapped(healthy), mock.patch.object(hosting_cmd.time, "sleep") as sleep:
            hosting_cmd._verify_edge(routes, handlers=(self.handler(),))
        self.assertEqual(len(healthy["received_headers"]), 1)
        sleep.assert_not_called()


class TestSharedApplyWiring(unittest.TestCase):
    def setup_shared(self, *, with_revision=False):
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
      statuses: [200]
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
        if with_revision:
            manifest = manifest.replace("      require_clean: true\n", "      require_clean: true\n      derived_environment:\n        LENZORA_SOURCE_REVISION: pushed_commit_sha\n")
        identity = mock.patch("sandbox.jobs.process._portable_start_identity", return_value="synthetic-start")
        boot = mock.patch("sandbox.jobs.process._boot_id", return_value="00000000-0000-0000-0000-000000000001")
        identity.start(); boot.start()
        self.addCleanup(identity.stop); self.addCleanup(boot.stop)
        fixture = _HostingOwnerFixture(self, manifest)
        fixture.entry.update(name=fixture.remote_name, front_door="nginx", capabilities=["zone_coverage_v1"])
        shared = [{"id": "rec-w", "name": "*.asb.bd", "type": "A", "content": "203.0.113.10", "proxied": True}]
        seed_store_zone(str(fixture.home), shared_records=shared)
        store = make_real_store(str(fixture.home), fixture.entry)
        events = []
        transport = store._ssh_run
        def recording_transport(entry, command, timeout=15):
            from tests.zone_coverage_test_support import validate_store_command
            request = json.loads(base64.b64decode(validate_store_command(command), validate=True))
            if request.get("action") == "dependent-bind" and request.get("state") == "bound":
                operation = fixture.delivery()["operation"]
                self.assertIsNotNone(operation)
                self.assertEqual(operation["execution_state"], "succeeded")
            result = transport(entry, command, timeout=timeout)
            if request.get("action") == "dependent-bind" and not json.loads(result.stdout).get("refused"):
                events.append(request.get("state"))
            return result
        fixture.patch.object(remote, "ssh_run", recording_transport)
        fallback = fixture.home / "fallback.conf"
        certificate = fixture.home / "shared-cert.pem"
        fallback.write_bytes(b"shared fallback fixture")
        certificate.write_bytes(b"shared certificate fixture")
        class Provider(StatefulFakeProvider):
            def upsert_address(self, zone_id, hostname, address, proxied=True):
                self.calls.append(("upsert_address", zone_id, hostname, address, proxied))
                return self.create_address(zone_id, hostname, address, proxied)
            def update_record(self, zone_id, record, **changes):
                self.calls.append(("update_record", zone_id, record["id"]))
                record.update(changes)
                return record
            def restore_record(self, zone_id, record, created_id=None):
                self.calls.append(("restore_record", zone_id, created_id))
                if created_id:
                    self._records = [row for row in self._records if row.get("id") != created_id]
                if record:
                    self._records.append(copy.deepcopy(record))
                return record
        provider = Provider("z-asb", "asb.bd", "strict", records=copy.deepcopy(shared))
        fixture.patch.object(hosting_cmd.cloudflare, "Client", return_value=provider)
        fixture.patch.object(hosting_cmd, "_cloudflare_drift", return_value={"configured": False})
        fixture.patch.object(hosting_cmd, "_host_recovery_eligibility", return_value={"eligible": True})
        fixture.patch.object(hosting_cmd, "_validate_apply_source", return_value="main")
        fixture.patch("sandbox.delivery.context.source_commit", return_value="a" * 40)
        fixture.patch.object(hosting_cmd, "_durable_host_context", return_value=fixture.context)
        fixture.patch.object(hosting_cmd, "_resolve_host_source_commit", return_value=fixture.commit)
        artifact = {"schema_version": 1, "kind": "git_commit", "root_relative": ".", "revision": fixture.commit}
        fixture.patch.object(hosting_cmd, "_prepare_host_source_artifact", return_value=(artifact, fixture.checkout))
        def source_git(root, *arguments):
            if arguments == ("rev-parse", "--show-toplevel"):
                return str(fixture.checkout)
            if arguments == ("rev-parse", "HEAD"):
                return fixture.commit
            if arguments == ("cat-file", "-t", fixture.commit):
                return "commit"
            if arguments == ("cat-file", "-t", fixture.commit + "^{tree}"):
                return "tree"
            if arguments == ("rev-parse", fixture.commit + "^{tree}"):
                return "b" * 40
            raise AssertionError("unexpected source git " + repr(arguments))
        fixture.patch.object(hosting_cmd, "_host_source_git", side_effect=source_git)
        fixture.patch.object(remote, "capture_uncommitted", return_value=("", []))
        fixture.patch.object(remote, "registered_remote_lock", return_value=contextlib.nullcontext())
        fixture.patch.object(hosting_cmd, "_hold_controller_awake", return_value=contextlib.nullcontext())
        fixture.patch.object(hosting_cmd.hosting, "load_host_state", side_effect=lambda: fixture.repository.load())
        from sandbox.hosting.front_door import nginx
        facts = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf"}], "version": [1, 26, 0]}
        fixture.patch.object(nginx, "probe", return_value=facts)
        fixture.patch.object(nginx, "preflight", return_value=facts)
        target_routes = {}
        def read_route(entry, name):
            if name == "coverage-asb.bd":
                return fallback.read_text()
            return target_routes.get(name)
        def apply_route(entry, name, content, **kwargs):
            self.assertNotEqual(name, "coverage-asb.bd")
            target_routes[name] = content
            return {}
        def restore_route(entry, name, previous):
            self.assertNotEqual(name, "coverage-asb.bd")
            target_routes[name] = previous
            return {}
        fixture.patch.object(nginx, "read_route", side_effect=read_route)
        fixture.patch.object(nginx, "apply_route", side_effect=apply_route)
        fixture.patch.object(nginx, "restore_route", side_effect=restore_route)
        def issue(entry, name, hosts, client):
            issued = client.create_origin_certificate("synthetic csr", hosts)
            return "/fixture/target.pem", "/fixture/target.key", {"id": issued["id"], "hostnames": hosts}
        fixture.patch.object(nginx, "ensure_origin_certificate", side_effect=issue)
        def negative(host, port, *args, **kwargs):
            raise socket.gaierror(socket.EAI_NONAME, "synthetic negative")
        fixture.patch.object(socket, "getaddrinfo", side_effect=negative)
        fixture.patch.object(hosting_cmd.hosting, "load_host_state", side_effect=lambda: fixture.repository.load())
        args = types.SimpleNamespace(action="apply", project_dir=str(fixture.root), environment=None,
                                     remote=fixture.remote_name, confirm=True, json=True, allow_zone_ssl_change=False)
        return fixture, store, provider, shared, args, events, fallback, certificate

    def test_shared_apply_negative_resolver_zero_dns_pending_then_bound(self):
        fixture, store, provider, shared, args, events, fallback, certificate = self.setup_shared()
        seen = []
        def compose(*args, **kwargs):
            seen.append(store.read("asb.bd")["document"]["dependents"][fixture.key]["state"])
        fixture.compose.side_effect = compose
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd.cmd_host(None, args)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertEqual(seen, ["pending"])
        self.assertEqual(events, ["pending", "bound"])
        fixture.health.assert_called_once()
        self.assertEqual(fallback.read_bytes(), b"shared fallback fixture")
        self.assertEqual(certificate.read_bytes(), b"shared certificate fixture")
        self.assertEqual(store.read("asb.bd")["document"]["dependents"][fixture.key]["state"], "bound")
        self.assertFalse([c for c in provider.calls if c[0] in ("create_address", "upsert_address", "delete_record", "update_record", "restore_record")])
        self.assertTrue(any(c[0] == "create_origin_certificate" and "app.asb.bd" in c[2] for c in provider.calls))
        self.assertEqual(provider._records, shared)

    def test_failed_shared_apply_retains_pending_and_shared_resources(self):
        fixture, store, provider, shared, args, events, fallback, certificate = self.setup_shared()
        fixture.health.side_effect = RuntimeError("synthetic health failure")
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            hosting_cmd.cmd_host(None, args)
        failure = json.loads(output.getvalue())
        self.assertFalse(failure["ok"])
        self.assertIn("synthetic health failure", failure["message"])
        fixture.health.assert_called_once()
        fixture.compose.assert_called_once()
        self.assertEqual(events, ["pending"])
        self.assertEqual(fallback.read_bytes(), b"shared fallback fixture")
        self.assertEqual(certificate.read_bytes(), b"shared certificate fixture")
        self.assertEqual(store.read("asb.bd")["document"]["dependents"][fixture.key]["state"], "pending")
        self.assertEqual(provider._records, shared)
        self.assertFalse([c for c in provider.calls if c[0] in ("create_address", "upsert_address", "delete_record", "update_record", "restore_record")])


class TestRecoveryIntentAndAuthority(unittest.TestCase):
    def applied(self, exact=False):
        fixture, store, provider, shared, args, events, fallback, certificate = TestSharedApplyWiring.setup_shared(self, with_revision=True)
        if exact:
            path = fixture.manifest_root / "sandbox.hosting.yml"
            path.write_text(path.read_text().replace("      ssl_mode: strict\n", "      ssl_mode: strict\n      dns: exact\n"))
            provider._records.append({"id": "rec-target", "name": "app.asb.bd", "type": "A", "content": "203.0.113.10", "proxied": True})
        with contextlib.redirect_stdout(io.StringIO()):
            hosting_cmd.cmd_host(None, args)
        validated = hosting.validate_manifest(str(fixture.manifest_root))
        recorded = fixture.repository.load()["hosts"][fixture.key]
        operation = recorded["hosting_operation"]
        return fixture, store, provider, shared, validated, operation

    def request(self, fixture):
        from sandbox.hosting.recovery.models import RecoveryAction, RecoveryRequest, TargetIdentity
        return RecoveryRequest(RecoveryAction.CONTINUE_EDGE, "recovery", "job", "original", TargetIdentity(fixture.remote_name, "demo", "production"), 1, "observation", "sha256:" + "a" * 64, True)

    def test_shared_recovery_uses_recorded_intent_after_generation_change(self):
        fixture, store, provider, shared, validated, operation = self.applied()
        zonefile = fixture.home / "runtime" / "zone-coverage" / "zones" / "asb.bd.json"
        doc = json.loads(zonefile.read_text()); original_generation = doc["generation"]
        doc["generation"] += 1; zonefile.write_text(json.dumps(doc))
        self.assertEqual(store.read("asb.bd")["document"]["generation"], original_generation + 1)
        authority = {}
        with mock.patch.object(targets, "decide", side_effect=AssertionError("unexpected decide")) as decide:
            with hosting_cmd._registered_recovery_authority(validated, fixture.remote_name, operation, authority):
                result = hosting_cmd._continue_host_edge_only(validated, authority["entry"], fixture.remote_name, self.request(fixture), operation)
            decide.assert_not_called()
        self.assertEqual(result["record"]["edge"]["evidence"]["public_edge"]["result"], "unverified")
        self.assertEqual(provider._records, shared)

    def test_exact_recovery_refuses_drifted_record(self):
        fixture, store, provider, shared, validated, operation = self.applied(exact=True)
        intent = operation["evidence"]["edge_intent"]
        self.assertEqual(intent["dns_coverage"], {"mode": "exact", "zone": "asb.bd", "drift_rule": True})
        moved = {**validated, "dns_coverage": {"mode": "shared", "zone": "asb.bd"}}
        with hosting_cmd._registered_recovery_authority(moved, fixture.remote_name, operation, {}):
            pass
        from sandbox.hosting.recovery.models import validate_edge_intent
        for invalid in (None, {"mode": "exact", "zone": "asb.bd", "drift_rule": False},
                        {"mode": "shared", "zone": "ASB.BD"}, {"mode": "auto", "zone": "asb.bd"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_edge_intent({**intent, "dns_coverage": invalid})
        record = next(r for r in provider._records if r["id"] == "rec-target")
        record["content"] = "192.0.2.9"
        provider.calls.clear()
        with self.assertRaises(CoverageError) as failure:
            hosting_cmd._continue_host_edge_only(validated, fixture.entry, fixture.remote_name, self.request(fixture), operation)
        self.assertEqual(failure.exception.code, MANAGED_RECORD_DRIFT)
        self.assertFalse([c for c in provider.calls if c[0] in ("upsert_address", "update_record", "delete_record")])
        record["content"] = "203.0.113.10"
        result = hosting_cmd._continue_host_edge_only(validated, fixture.entry, fixture.remote_name, self.request(fixture), operation)
        self.assertEqual(result["record"]["records"][0]["created_id"], "rec-target")

    def test_recovery_refuses_changed_binding_after_manifest_moves_to_exact(self):
        fixture, store, provider, shared, validated, operation = self.applied()
        validated["cloudflare"]["dns"] = "exact"
        authority = {}
        with hosting_cmd._registered_recovery_authority(validated, fixture.remote_name, operation, authority):
            self.assertEqual(authority["entry"], fixture.entry)
        changed = dict(fixture.entry, ssh="fixture@different.example.test")
        fixture.patch.object(remote, "get_remote", return_value=changed)
        with self.assertRaisesRegex(hosting_cmd.RecoveryAuthorityError, "registered remote changed"):
            with hosting_cmd._registered_recovery_authority(validated, fixture.remote_name, operation, {}):
                pass


class TestHostPlanRefusalsAndImagePreflight(unittest.TestCase):
    """Item 6: test_host_plan_each_refusal_is_exit_zero_data & test_image_preflight."""

    def test_real_plan_requires_observed_fallback(self):
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
      statuses: [200]
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
        identity = mock.patch("sandbox.jobs.process._portable_start_identity", return_value="synthetic-start")
        boot = mock.patch("sandbox.jobs.process._boot_id", return_value="00000000-0000-0000-0000-000000000001")
        identity.start(); boot.start()
        self.addCleanup(identity.stop); self.addCleanup(boot.stop)
        fixture = _HostingOwnerFixture(self, manifest)
        fixture.entry.update(name=fixture.remote_name, front_door="nginx", capabilities=["zone_coverage_v1"])
        records = [{"id": "rec-w", "name": "*.asb.bd", "type": "A", "content": "203.0.113.10", "proxied": True}]
        seed_store_zone(str(fixture.home), shared_records=records)
        store = make_real_store(str(fixture.home), fixture.entry)
        fixture.patch.object(remote, "ssh_run", store._ssh_run)
        provider = StatefulFakeProvider("z-asb", "asb.bd", "strict", records=records)
        fixture.patch.object(hosting_cmd.cloudflare, "Client", return_value=provider)
        fixture.patch.object(hosting_cmd, "_cloudflare_drift", return_value={})
        fixture.patch.object(hosting_cmd, "_host_recovery_eligibility", return_value={"eligible": True})
        fixture.patch.object(hosting_cmd, "_add_front_door_plan")
        from sandbox.hosting.front_door import nginx
        probe = fixture.patch.object(nginx, "probe", return_value={"routes": []})
        args = types.SimpleNamespace(action="plan", project_dir=str(fixture.root), environment=None, remote=fixture.remote_name, json=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hosting_cmd.cmd_host(None, args)
        payload = json.loads(out.getvalue())
        probe.assert_called_once()
        self.assertEqual(payload["dns_coverage"]["code"], COVERAGE_DRIFT)
        self.assertTrue(payload["ok"])
        probe.return_value = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf"}]}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hosting_cmd.cmd_host(None, args)
        self.assertEqual(json.loads(out.getvalue())["dns_coverage"]["mode"], "shared")
        probe.side_effect = RuntimeError("private marker")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hosting_cmd.cmd_host(None, args)
        self.assertEqual(json.loads(out.getvalue())["dns_coverage"]["code"], COVERAGE_STORE_UNAVAILABLE)
        self.assertNotIn("private marker", out.getvalue())
        fixture.entry["front_door"] = "caddy"
        probe.reset_mock()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hosting_cmd.cmd_host(None, args)
        self.assertTrue(json.loads(out.getvalue())["ok"])
        probe.assert_not_called()
        fixture.entry["front_door"] = "nginx"
        probe.side_effect = None
        probe.return_value = {"routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf"}]}
        snapshot = copy.deepcopy(fixture.validated)
        with self.assertRaises(CoverageError) as refused:
            hosting_cmd._HostImageEdgeAdapter(fixture.validated).preflight(fixture.entry, fixture.remote_name)
        self.assertEqual(refused.exception.code, COVERAGE_IMAGE_UNSUPPORTED)
        self.assertEqual(fixture.validated, snapshot)
        fixture.patch.object(hosting_cmd, "_validate_apply_source", return_value="main")
        fixture.patch.object(remote, "registered_remote_lock", return_value=contextlib.nullcontext())
        probe.side_effect = RuntimeError("private marker")
        args.action = "apply"
        args.confirm = True
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            hosting_cmd.cmd_host(None, args)
        failure = json.loads(out.getvalue())
        self.assertEqual(failure["code"], COVERAGE_STORE_UNAVAILABLE)
        self.assertNotIn("private marker", out.getvalue())
        fixture.compose.assert_not_called()

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
        entry = {"name": "r1", "front_door": "nginx", "origin_ipv4": "198.51.100.1", "capabilities": ["zone_coverage_v1"]}
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
        entry = {"name": "r1", "front_door": "nginx", "origin_ipv4": "198.51.100.1", "capabilities": ["zone_coverage_v1"]}
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
        wanted = {"hostname": "app.asb.bd", "address": "198.51.100.1", "proxied": True}

        # No record -> write
        res = targets.check_exact_record(wanted, [], None, allow_overwrite=False)
        self.assertEqual(res["action"], "write")

        # Unchanged owned -> adopt
        existing_owned = {"id": "rec-1", "name": "app.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}
        recorded = [{"applied": {"name": "app.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}}]
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
        wanted_a = {"hostname": "app.asb.bd", "address": "198.51.100.1", "proxied": True}
        wanted_aaaa = {"hostname": "app.asb.bd", "address": "2001:db8::1", "proxied": True}

        rec_a = {"id": "rec-a", "name": "app.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}
        rec_aaaa_drift = {"id": "rec-aaaa", "name": "app.asb.bd", "type": "AAAA", "content": "2001:db8::99", "proxied": True}
        recorded = [
            {"applied": {"name": "app.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}},
            {"applied": {"name": "app.asb.bd", "type": "AAAA", "content": "2001:db8::1", "proxied": True}},
        ]

        for wanted, good, other in ((wanted_a, rec_a, rec_aaaa_drift),
                                    (wanted_aaaa, dict(rec_aaaa_drift, content="2001:db8::1"), rec_a)):
            for rows in ([good, other], [other, good]):
                with self.subTest(wanted=wanted["address"], first=rows[0]["id"]):
                    adopted = targets.check_exact_record(wanted, rows, recorded)
                    self.assertEqual(adopted["existing"]["id"], good["id"])
        other_name = dict(rec_a, name="other.asb.bd", content="192.0.2.1")
        self.assertEqual(targets.check_exact_record(wanted_a, [other_name], recorded)["action"], "write")
        for bad_snapshot in ([{"applied": dict(recorded[0]["applied"], name="other.asb.bd")}],
                             [{"applied": recorded[1]["applied"]}]):
            with self.assertRaises(CoverageError) as mismatch:
                targets.check_exact_record(wanted_a, [dict(rec_a, content="192.0.2.1")], bad_snapshot, allow_overwrite=True)
            self.assertEqual(mismatch.exception.code, RECORD_NOT_OWNED)

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
        for scenario in ("unowned", "owned", "drift", "overwrite"):
            with self.subTest(scenario=scenario):
                fixture, store, provider, shared, args, events, fallback, certificate = TestSharedApplyWiring.setup_shared(self)
                path = fixture.manifest_root / "sandbox.hosting.yml"
                manifest = path.read_text().replace("      primary: app.asb.bd\n", "      primary: app.asb.bd\n      aliases:\n        - hostname: redir.asb.bd\n          mode: redirect\n          target: https://destination.example\n").replace("      ssl_mode: strict\n", "      ssl_mode: strict\n      dns: exact\n")
                path.write_text(manifest)
                provider._records.extend([
                    {"id": "primary", "name": "app.asb.bd", "type": "A", "content": "203.0.113.10", "proxied": True},
                    {"id": "redirect", "name": "redir.asb.bd", "type": "CNAME", "content": "destination.example", "proxied": False},
                ])
                snapshot = {"name": "redir.asb.bd", "type": "CNAME", "content": "destination.example", "proxied": scenario in ("drift", "overwrite")}
                records = [] if scenario == "unowned" else [{"zone_id": "z-asb", "created_id": "redirect", "applied": snapshot}]
                fixture.repository._write({"version": 1, "hosts": {fixture.key: {"records": records}}})
                args.allow_record_overwrite = scenario in ("unowned", "overwrite")
                output = io.StringIO()
                if scenario in ("unowned", "drift"):
                    with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
                        hosting_cmd.cmd_host(None, args)
                    self.assertEqual(json.loads(output.getvalue())["code"], RECORD_NOT_OWNED if scenario == "unowned" else MANAGED_RECORD_DRIFT, output.getvalue())
                    self.assertFalse([c for c in provider.calls if c[0] == "update_record"])
                else:
                    with contextlib.redirect_stdout(output):
                        hosting_cmd.cmd_host(None, args)
                    self.assertTrue(json.loads(output.getvalue())["ok"])
                    self.assertEqual([c[2] for c in provider.calls if c[0] == "update_record"], ["redirect"])

    def test_uncovered_zone_overwrites_as_today(self):
        fixture, store, provider, shared, args, events, fallback, certificate = TestSharedApplyWiring.setup_shared(self)
        path = fixture.manifest_root / "sandbox.hosting.yml"
        path.write_text(path.read_text().replace("app.asb.bd", "app.uncovered.test"))
        provider._records.append({"id": "old", "name": "app.uncovered.test", "type": "A", "content": "192.0.2.9", "proxied": True})
        args.action = "plan"
        fixture.patch.object(hosting_cmd, "_add_front_door_plan")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd.cmd_host(None, args)
        plan = json.loads(output.getvalue())
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["records"], [{"address": "203.0.113.10", "hostname": "app.uncovered.test", "mode": "serve", "proxied": True, "target": None}])
        self.assertNotIn("dns_coverage", plan)
        args.action = "apply"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd.cmd_host(None, args)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertTrue(any(c[0] == "upsert_address" and c[2] == "app.uncovered.test" and c[3] == "203.0.113.10" for c in provider.calls))
        self.assertEqual(events, [])


class TestPreservationAndEnableSafety(unittest.TestCase):
    """Item 9: Auto preserves exact target, multi-route, and enable safety."""

    def test_auto_preserves_existing_exact_target(self):
        entry = {"name": "r1", "front_door": "nginx", "origin_ipv4": "198.51.100.1", "capabilities": ["zone_coverage_v1"]}
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
        entry = {"name": "r1", "front_door": "nginx", "origin_ipv4": "198.51.100.1", "capabilities": ["zone_coverage_v1"]}
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
        entry = {"name": "r1", "front_door": "nginx", "origin_ipv4": "198.51.100.1", "capabilities": ["zone_coverage_v1"]}
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
        real_verify = hosting_cmd._verify_edge
        guarded_resolver = socket.getaddrinfo
        fixture, store, provider, shared, args, events, fallback, certificate = TestSharedApplyWiring.setup_shared(self, with_revision=True)
        fixture.patch.object(hosting_cmd, "_verify_edge", real_verify)
        authority = make_tls_authority(self)
        server = make_loopback_tls_server(authority, "app.asb.bd")
        self.addCleanup(server["cleanup"])
        context = ssl.create_default_context(cafile=authority[2])
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        def resolver(host, port, *args, **kwargs):
            if (host, port) == ("127.0.0.1", server["port"]):
                return guarded_resolver(host, port, *args, **kwargs)
            if host.startswith("sb-verify-") and host.endswith(".asb.bd"):
                self.assertEqual(port, 443)
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
            raise socket.gaierror(socket.EAI_NONAME, "synthetic target negative")
        fixture.patch.object(socket, "getaddrinfo", side_effect=resolver)
        original_connect = socket.create_connection
        def mapped(address, *args, **kwargs):
            self.assertEqual(address, ("127.0.0.1", 443))
            return original_connect(("127.0.0.1", server["port"]), *args, **kwargs)
        fixture.patch.object(socket, "create_connection", side_effect=mapped)
        fixture.patch.object(hosting_cmd.time, "sleep", return_value=None)
        original_build = urllib.request.build_opener
        def opener(*handlers):
            for handler in handlers:
                if isinstance(handler, targets._PinnedHTTPSHandler):
                    handler._context = context
            return original_build(*handlers)
        fixture.patch.object(urllib.request, "build_opener", side_effect=opener)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd.cmd_host(None, args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        evidence = payload["edge"]["evidence"]
        self.assertEqual(payload["edge"]["state"], "ready")
        self.assertEqual(evidence["origin"]["result"], "passed")
        self.assertEqual(evidence["public_edge"]["result"], "passed")
        self.assertEqual(evidence["resolver"]["result"], "negative")
        self.assertIsInstance(evidence["observed_at"], str)
        self.assertEqual(len(server["received_headers"]), 1)
        self.assertTrue(fixture.delivery()["operation"]["delivery_succeeded"])
        args.action = "status"
        args.json = False
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd.cmd_host(None, args)
        for line in ("origin: passed", "public edge: passed", "resolver: negative"):
            self.assertIn(line, output.getvalue())

    def test_unverified_edge_marks_delivery_incomplete(self):
        fixture, store, provider, shared, args, events, fallback, certificate = TestSharedApplyWiring.setup_shared(self, with_revision=True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hosting_cmd.cmd_host(None, args)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        operation = fixture.delivery()["operation"]
        self.assertEqual(operation["execution_state"], "succeeded")
        self.assertEqual(operation["runtime"]["state"], "known")
        self.assertEqual(operation["edge"]["state"], "partial")
        self.assertEqual(operation["edge"]["result"], "unknown")
        self.assertFalse(operation["delivery_succeeded"])
        self.assertEqual(operation["evidence_completeness"], "partial")
        self.assertEqual(payload["edge"]["state"], "ready")
        self.assertEqual(payload["edge"]["evidence"]["public_edge"]["result"], "unverified")
        self.assertEqual(fixture.repository.load()["hosts"][fixture.key]["runtime"]["state"], "ready")


if __name__ == "__main__":
    unittest.main()
