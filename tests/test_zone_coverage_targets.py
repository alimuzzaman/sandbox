from __future__ import annotations

import contextlib
import copy
import http.client
import json
from pathlib import Path
import socket
import ssl
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock

import sandbox.core._hosting as hosting
from sandbox.hosting.zone_coverage import (
    COVERAGE_DISABLED_DEPENDENT,
    COVERAGE_DRIFT,
    COVERAGE_IMAGE_UNSUPPORTED,
    COVERAGE_NAME_INELIGIBLE,
    COVERAGE_NOT_ACTIVE,
    COVERAGE_RECORD_MISSING,
    COVERAGE_STORE_UNAVAILABLE,
    MANAGED_RECORD_DRIFT,
    RECORD_NOT_OWNED,
    CoverageError,
    targets,
)
from sandbox.commands.hosting import _desired_edge_intent


class TestZoneCoverageTargets(unittest.TestCase):
    def _sample_validated(self, hostname="app.example.com", dns=None):
        cf = {"proxied": True}
        if dns is not None:
            cf["dns"] = dns
        return {
            "project": "myapp",
            "environment": "sandbox",
            "project_root": "/tmp/proj",
            "deploy": {},
            "routes": [{"hostname": hostname, "mode": "serve"}],
            "compose": {"services": {"web": {"image": "nginx"}}},
            "cloudflare": cf,
            "secrets": {"required": [], "generated": []},
        }

    def _sample_doc(self, zone="example.com", state="active", generation=1, target_record=None, dependents=None):
        if target_record is None:
            target_record = {
                "id": "rec-wildcard",
                "name": f"*.{zone}",
                "type": "A",
                "content": "192.0.2.1",
                "proxied": True,
            }
        return {
            "schema_version": 1,
            "zone": zone,
            "state": state,
            "generation": generation,
            "record": target_record,
            "shared_records": [target_record],
            "updated_at": "2026-11-05T00:00:00Z",
            "dependents": dependents or {},
        }

    # T021: parse cloudflare.dns validation
    def test_t021_parse_cloudflare_dns(self):
        import tempfile
        import yaml
        def _check(cf_dict):
            with tempfile.TemporaryDirectory() as td:
                m = {
                    "version": 1, "project": "p",
                    "environments": {"sandbox": {
                        "compose": {
                            "files": ["compose.yml"],
                            "service": "web",
                            "container_port": 80,
                            "services": {"web": {"image": "img"}},
                        },
                        "healthcheck": {"path": "/", "statuses": [200]},
                        "deploy": {"allowed_branches": ["main"], "require_clean": True},
                        "host": {"primary": "app.test"},
                        "routes": [{"hostname": "app.test", "mode": "serve"}],
                        "cloudflare": cf_dict,
                    }},
                }
                (Path(td) / "sandbox.hosting.yml").write_text(yaml.safe_dump(m))
                (Path(td) / "compose.yml").write_text("services: {}\n")
                return hosting.validate_manifest(td)

        # Valid "auto" and "exact"
        for val in ("auto", "exact"):
            v = _check({"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": val})
            self.assertEqual(v["cloudflare"]["dns"], val)

        # Invalid values raise HostingError
        for bad in ("foo", "", True, False, 123, [], {}):
            with self.subTest(bad=bad), self.assertRaises(hosting.HostingError):
                _check({"proxied": True, "tls": "origin-ca", "ssl_mode": "strict", "dns": bad})

        # Undeclared: key is omitted from result
        v_no_dns = _check({"proxied": True, "tls": "origin-ca", "ssl_mode": "strict"})
        self.assertNotIn("dns", v_no_dns["cloudflare"])

    # T022: coverage_zone_of candidate matching and depths
    def test_t022_coverage_zone_of(self):
        zones = ["example.com", "sub.domain.co.uk"]
        # Direct subdomain matches
        self.assertEqual(targets.coverage_zone_of("app.example.com", zones), "example.com")
        self.assertEqual(targets.coverage_zone_of("foo.sub.domain.co.uk", zones), "sub.domain.co.uk")
        # Multi-level matches
        self.assertEqual(targets.coverage_zone_of("a.b.c.example.com", zones), "example.com")
        # Apex does not match wildcard
        self.assertIsNone(targets.coverage_zone_of("example.com", zones))
        self.assertIsNone(targets.coverage_zone_of("sub.domain.co.uk", zones))
        # Unrelated does not match
        self.assertIsNone(targets.coverage_zone_of("app.other.com", zones))
        self.assertIsNone(targets.coverage_zone_of("evil-example.com", zones))

    # T023: decide decision table
    def test_t023_decide_decision_table(self):
        entry_with_cap = {
            "name": "r1", "origin_ipv4": "192.0.2.1", "front_door": "nginx",
            "capabilities": ["zone_coverage_v1"],
        }
        entry_without_cap = {
            "name": "r1", "origin_ipv4": "192.0.2.1", "front_door": "nginx",
            "capabilities": [],
        }

        shared_rec = {
            "id": "rec-wildcard", "name": "*.example.com", "type": "A",
            "content": "192.0.2.1", "proxied": True,
        }
        mock_cf = mock.MagicMock()
        mock_cf.zones.return_value = [{"id": "z1", "name": "example.com"}]
        mock_cf.zone.return_value = {"id": "z1", "name": "example.com"}
        mock_cf.zone_records.return_value = [shared_rec]
        mock_cf.current_ssl_mode.return_value = "strict"

        mock_store = mock.MagicMock()
        mock_store.list.return_value = [{"zone": "example.com", "state": "active"}]
        mock_store.read.return_value = self._sample_doc("example.com", state="active")

        # Step 1: no zone -> exact
        val_no_zone = self._sample_validated(hostname="app.uncovered.test")
        res1 = targets.decide(val_no_zone, entry_with_cap, None, store_client=mock_store, cf_client=mock_cf)
        self.assertEqual(res1["mode"], "exact")
        self.assertIsNone(res1.get("zone"))

        # Step 2: remote has no capability -> exact
        val = self._sample_validated(hostname="app.example.com")
        res2 = targets.decide(val, entry_without_cap, None, cf_client=mock_cf)
        self.assertEqual(res2["mode"], "exact")
        self.assertIsNone(res2.get("zone"))

        # Step 3: image flag set on would-be shared -> CoverageError(COVERAGE_IMAGE_UNSUPPORTED)
        with self.assertRaises(CoverageError) as ctx:
            targets.decide(val, entry_with_cap, None, cf_client=mock_cf, store_client=mock_store, image=True)
        self.assertEqual(ctx.exception.code, COVERAGE_IMAGE_UNSUPPORTED)

        # Step 4: dns: exact declared -> exact with zone and drift_rule
        val_exact = self._sample_validated(hostname="app.example.com", dns="exact")
        res4 = targets.decide(val_exact, entry_with_cap, None, cf_client=mock_cf, store_client=mock_store)
        self.assertEqual(res4["mode"], "exact")
        self.assertEqual(res4["zone"], "example.com")
        self.assertTrue(res4["drift_rule"])

        # Step 5: dns: auto declared (or omitted), active generation matches, store healthy -> shared
        val_auto = self._sample_validated(hostname="app.example.com", dns="auto")
        res5 = targets.decide(val_auto, entry_with_cap, None, cf_client=mock_cf, store_client=mock_store)
        self.assertEqual(res5["mode"], "shared")
        self.assertEqual(res5["zone"], "example.com")
        self.assertEqual(res5["generation"], 1)

        # Step 6: target holds owned record -> exact with zone
        target_rec_with_records = {
            "records": [{"zone_id": "z1", "created_id": "rec-123", "applied": {"name": "app.example.com"}}],
        }
        res6 = targets.decide(val_auto, entry_with_cap, target_rec_with_records, cf_client=mock_cf, store_client=mock_store)
        self.assertEqual(res6["mode"], "exact")
        self.assertEqual(res6["zone"], "example.com")
        self.assertTrue(res6["drift_rule"])

        # Step 7: active but generation drifted -> CoverageError(COVERAGE_DRIFT)
        target_rec = {"dns_coverage": {"generation": 1}}
        mock_store.read.return_value = self._sample_doc("example.com", state="active", generation=2)
        with self.assertRaises(CoverageError) as ctx:
            targets.decide(val_auto, entry_with_cap, target_rec, cf_client=mock_cf, store_client=mock_store)
        self.assertEqual(ctx.exception.code, COVERAGE_DRIFT)

        # Step 8: record drifted on Cloudflare -> CoverageError(COVERAGE_DRIFT)
        mock_store.read.return_value = self._sample_doc("example.com", state="active", generation=1)
        mock_cf_drifted = mock.MagicMock()
        mock_cf_drifted.zone.return_value = {"id": "z1", "name": "example.com"}
        mock_cf_drifted.zone_records.return_value = [{
            "id": "rec-other", "name": "*.example.com", "type": "A", "content": "192.0.2.99",
        }]
        mock_cf_drifted.current_ssl_mode.return_value = "strict"
        with self.assertRaises(CoverageError) as ctx:
            targets.decide(val_auto, entry_with_cap, None, cf_client=mock_cf_drifted, store_client=mock_store)
        self.assertEqual(ctx.exception.code, COVERAGE_DRIFT)

        # Step 8b: fallback route exists in facts but zone retired -> CoverageError(COVERAGE_RECORD_MISSING)
        mock_store_retired = mock.MagicMock()
        mock_store_retired.list.return_value = [{"zone": "example.com", "state": "retired"}]
        facts_with_fallback = {
            "routes": [{"file": "/etc/nginx/conf.d/sandbox-coverage-example.com.conf", "coverage_zone": "example.com"}]
        }
        with self.assertRaises(CoverageError) as ctx:
            targets.decide(val_auto, entry_with_cap, None, store_client=mock_store_retired, facts=facts_with_fallback)
        self.assertEqual(ctx.exception.code, COVERAGE_RECORD_MISSING)

        # Step 9: disabled with dependent for this target -> CoverageError(COVERAGE_DISABLED_DEPENDENT)
        key = hosting.state_key("r1", val_auto)
        mock_store.read.return_value = self._sample_doc("example.com", state="disabled", dependents={key: {"state": "bound"}})
        with self.assertRaises(CoverageError) as ctx:
            targets.decide(val_auto, entry_with_cap, None, cf_client=mock_cf, store_client=mock_store)
        self.assertEqual(ctx.exception.code, COVERAGE_DISABLED_DEPENDENT)

        # Report flag: produces refused report without raising
        report_res = targets.decide(val_auto, entry_with_cap, None, cf_client=mock_cf, store_client=mock_store, report=True)
        self.assertEqual(report_res["mode"], "refused")
        self.assertEqual(report_res["code"], COVERAGE_DISABLED_DEPENDENT)

    # T024: desired_plan output
    def test_t024_desired_plan_records(self):
        val = self._sample_validated()
        # Normal (no dns_coverage): returns A/AAAA records
        plan_normal = hosting.desired_plan(val, "192.0.2.1", "2001:db8::1")
        self.assertGreater(len(plan_normal["records"]), 0)

        # With shared dns_coverage: returns empty records list
        val_shared = copy.deepcopy(val)
        val_shared["dns_coverage"] = {"mode": "shared", "zone": "example.com"}
        plan_shared = hosting.desired_plan(val_shared, "192.0.2.1", "2001:db8::1")
        self.assertEqual(plan_shared["records"], [])

        # Non-shared byte-identical check
        plan_exact = hosting.desired_plan(copy.deepcopy(val), "192.0.2.1", "2001:db8::1")
        self.assertEqual(plan_normal, plan_exact)

    # T026: edge intent and recovery
    def test_t026_edge_intent_dns_coverage(self):
        entry = {"origin_ipv4": "192.0.2.1"}
        val_no_cov = self._sample_validated()
        intent1 = _desired_edge_intent(val_no_cov, entry)
        self.assertNotIn("dns_coverage", intent1)

        val_shared = copy.deepcopy(val_no_cov)
        val_shared["dns_coverage"] = {"mode": "shared", "zone": "example.com"}
        intent2 = _desired_edge_intent(val_shared, entry)
        self.assertIn("dns_coverage", intent2)
        self.assertEqual(intent2["dns_coverage"], {"mode": "shared", "zone": "example.com"})

    # T027: store dependent bindings
    def test_t027_dependent_bindings(self):
        mock_client = mock.MagicMock()
        mock_client.dependent_bind.return_value = {"ok": True}
        mock_client.dependent_release.return_value = {"ok": True}

        # bind_pending
        targets.bind_pending(mock_client, "example.com", "target-key", "app.example.com", generation=1)
        mock_client.dependent_bind.assert_called_with("example.com", "target-key", "app.example.com", "pending", generation=1, operation_id=None)

        # bind_bound
        targets.bind_bound(mock_client, "example.com", "target-key", "app.example.com", generation=1)
        mock_client.dependent_bind.assert_called_with("example.com", "target-key", "app.example.com", "bound", generation=1, operation_id=None)

        # release
        targets.release(mock_client, "example.com", "target-key")
        mock_client.dependent_release.assert_called_with("example.com", "target-key")

    # T028: exact record drift rule
    def test_t028_check_exact_record(self):
        wanted = {"hostname": "app.example.com", "address": "192.0.2.1"}
        matching_rec = {"id": "rec-1", "name": "app.example.com", "type": "A", "content": "192.0.2.1"}
        drifted_rec = {"id": "rec-1", "name": "app.example.com", "type": "A", "content": "192.0.2.99"}
        other_rec = {"id": "rec-2", "name": "app.example.com", "type": "A", "content": "198.51.100.1"}

        # Target matches existing -> adopt
        res = targets.check_exact_record(wanted, [matching_rec], None)
        self.assertEqual(res["action"], "adopt")

        # Drifted without flag -> CoverageError(MANAGED_RECORD_DRIFT)
        recorded = [{
            "created_id": "rec-1",
            "zone_id": "z1",
            "applied": {"name": "app.example.com", "type": "A", "content": "192.0.2.1", "proxied": True},
        }]
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted, [drifted_rec], recorded, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, MANAGED_RECORD_DRIFT)

        # Drifted with allow_overwrite -> write
        res_ov = targets.check_exact_record(wanted, [drifted_rec], recorded, allow_overwrite=True)
        self.assertEqual(res_ov["action"], "write")

        # Unowned record present -> CoverageError(RECORD_NOT_OWNED)
        with self.assertRaises(CoverageError) as ctx:
            targets.check_exact_record(wanted, [other_rec], None, allow_overwrite=False)
        self.assertEqual(ctx.exception.code, RECORD_NOT_OWNED)

    # T030: resolver_state
    def test_t030_resolver_state(self):
        with mock.patch("socket.getaddrinfo") as mock_gai:
            mock_gai.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))]
            self.assertEqual(targets.resolver_state("app.example.com"), "resolved")

            mock_gai.side_effect = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
            self.assertEqual(targets.resolver_state("app.example.com"), "negative")

            mock_gai.side_effect = socket.gaierror(socket.EAI_AGAIN, "Temporary failure")
            self.assertEqual(targets.resolver_state("app.example.com"), "error")

    # T030: verify_shared evidence collection
    def test_t030_verify_shared_evidence(self):
        mock_verifier = mock.MagicMock()
        mock_res = mock.MagicMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))])
        evidence = targets.verify_shared(
            [{"hostname": "app.example.com", "mode": "serve"}],
            "example.com",
            mock_verifier,
            resolver=mock_res,
            fresh_hostnames=frozenset(["app.example.com"]),
        )
        self.assertEqual(evidence["public_edge"]["result"], "passed")
        self.assertEqual(evidence["origin"]["result"], "passed")
        self.assertIn("resolver", evidence)
        self.assertIn("observed_at", evidence)
        mock_verifier.assert_called_once()
        # Verify fresh_hostnames was cleared before calling underlying verifier
        call_kwargs = mock_verifier.call_args[1]
        self.assertEqual(call_kwargs.get("fresh_hostnames"), frozenset())
        self.assertTrue(call_kwargs.get("handlers"))

    # T033: CLI parser accepts --allow-record-overwrite
    def test_t033_cli_allow_record_overwrite(self):
        from tests.subprocess_support import run_test_process
        res = run_test_process([sys.executable, "-m", "sandbox.cli", "host", "--help"],
                               capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("--allow-record-overwrite", res.stdout)


if __name__ == "__main__":
    unittest.main()
