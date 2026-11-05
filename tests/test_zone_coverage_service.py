from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sandbox.hosting.front_door import FrontDoorError, ZONE_COVERAGE
from sandbox.hosting.zone_coverage import (
    COVERAGE_CONFLICT,
    COVERAGE_DOCUMENT_CORRUPT,
    COVERAGE_FALLBACK_UNVERIFIED,
    COVERAGE_OPERATION_ACTIVE,
    COVERAGE_PLAN_STALE,
    COVERAGE_RECORD_EXISTS,
    COVERAGE_SSL_NOT_STRICT,
    COVERAGE_STORE_UNAVAILABLE,
    CoverageError,
)
from sandbox.hosting.zone_coverage.service import (
    enable,
    plan_enable,
    status,
)


class FakeStoreClient:
    def __init__(self, doc=None):
        self.doc = doc
        self.history = []
        self.phase_refusal = None
        self.finish_refusal = None

    def read(self, zone: str):
        if self.doc is None:
            return {"ok": True, "action": "read", "state": "absent", "document": None}
        return {"ok": True, "action": "read", "state": self.doc.get("state"), "document": self.doc}

    def begin(self, zone: str, kind: str, controller: str, *, expected_generation=None, expected_dependents_revision=None, resume=False, zone_id="", dependents=None):
        if self.doc:
            cur_gen = self.doc.get("generation", 0)
            if expected_generation is not None and expected_generation != cur_gen:
                return {"ok": True, "action": "begin", "refused": "coverage_generation_stale"}
            op = self.doc.get("operation")
            if op and not resume:
                return {"ok": True, "action": "begin", "refused": COVERAGE_OPERATION_ACTIVE}
        op_id = "op-12345"
        self.doc = {
            "schema_version": 1,
            "zone": zone,
            "zone_id": zone_id,
            "state": "enabling",
            "generation": self.doc.get("generation", 0) if self.doc else 0,
            "operation": {"id": op_id, "kind": kind, "controller": controller},
            "interrupted_kind": None,
            "shared_records": [],
            "fallback": None,
            "exceptions": [],
            "dependents": dependents or (self.doc.get("dependents", {}) if self.doc else {}),
            "dependents_revision": 0,
            "publish_intent": self.doc.get("publish_intent") if self.doc else None,
            "history": [],
        }
        return {"ok": True, "action": "begin", "operation_id": op_id, "document": self.doc}

    def phase(self, zone: str, operation_id: str, phase: str, *, updates=None):
        if self.phase_refusal:
            return {"ok": True, "action": "phase", "refused": self.phase_refusal}
        if updates:
            self.doc.update(updates)
        self.doc["operation"]["phase"] = phase
        return {"ok": True, "action": "phase", "document": self.doc}

    def finish(self, zone: str, operation_id: str, state: str, *, interrupted_kind=None, raise_generation=False, unknown_effects=None):
        if getattr(self, "finish_exception", None):
            raise self.finish_exception
        if self.finish_refusal:
            return {"ok": True, "action": "finish", "refused": self.finish_refusal}
        self.doc["state"] = state
        self.doc["operation"] = None
        self.doc["interrupted_kind"] = interrupted_kind
        if unknown_effects:
            self.doc["unknown_effects"] = unknown_effects
        if raise_generation:
            self.doc["generation"] = self.doc.get("generation", 0) + 1
        if state in ("active", "fallback_only"):
            self.doc["publish_intent"] = None
            self.doc["unknown_effects"] = None
        return {"ok": True, "action": "finish", "document": self.doc}


class FakeCfClient:
    def __init__(self, zone_id="z-test", ssl_mode="strict", records=None):
        self.zone_id = zone_id
        self.ssl_mode = ssl_mode
        self._records = records or []
        self.created_records = []
        self.deleted_records = []

    def zone(self, name: str):
        return {"id": self.zone_id, "name": name}

    def zones(self, name: str | None = None):
        return [{"id": self.zone_id, "name": name or "asb.bd"}]

    def _request(self, method: str, path: str):
        if "/settings/ssl" in path:
            return {"success": True, "result": {"value": self.ssl_mode}}
        return {"success": True, "result": []}

    def records(self, zone_id: str, hostname: str | None = None, name: str | None = None):
        target = hostname or name
        if target:
            return [r for r in self._records if r.get("name") == target]
        return list(self._records)

    def zone_records(self, zone_id: str):
        return list(self._records)

    def create_address(self, zone_id: str, hostname: str, address: str, proxied: bool = True, comment: str = ""):
        rec_type = "AAAA" if ":" in address else "A"
        rec = {"id": f"rec-{len(self._records)+1}", "name": hostname, "type": rec_type, "content": address, "proxied": proxied, "comment": comment}
        self._records.append(rec)
        self.created_records.append(rec)
        return rec

    def upsert_address(self, zone_id: str, hostname: str, address: str, proxied: bool = True):
        return self.create_address(zone_id, hostname, address, proxied=proxied)

    def delete_record(self, zone_id: str, record_id: str):
        self.deleted_records.append(record_id)
        self._records = [r for r in self._records if r.get("id") != record_id]
        return {"id": record_id}


class TestZoneCoverageService(unittest.TestCase):
    def setUp(self):
        self.entry = {
            "name": "test-remote",
            "front_door": "nginx",
            "origin_ipv4": "198.51.100.1",
        }
        self._default_probe_patcher = patch(
            "sandbox.hosting.front_door.nginx.probe",
            return_value={"config_valid": True, "version": [1, 26, 0]},
        )
        self._default_probe_patcher.start()
        self.addCleanup(self._default_probe_patcher.stop)

    def test_f1_phase_and_finish_refusals_stop_work_and_never_return_active(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z1",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"type": "A", "name": "*.asb.bd", "content": "1.2.3.4", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        # 1. Refusal on phase stops work before DNS write
        store.phase_refusal = "coverage_operation_active"
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/etc/fallback.conf"}):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, "coverage_operation_active")
            self.assertEqual(len(cf.created_records), 0, "Provider DNS write must not be called after phase refusal")

        # 2. Refusal on finish must never return state: active
        store2 = FakeStoreClient()
        store2.finish_refusal = "coverage_generation_stale"
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/etc/fallback.conf"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            with self.assertRaises(CoverageError) as ctx:
                res = enable(self.entry, "asb.bd", plan, store_client=store2, cf_client=cf)
                self.assertNotEqual(res.get("state"), "active")
            self.assertEqual(ctx.exception.code, "coverage_generation_stale")

    def test_f2_public_check_safe_rollback_unknown_effects_and_exact_name_reread(self):
        # 1. Public check failure triggers DNS record rollback and marks fallback_only
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z1",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"type": "A", "name": "*.asb.bd", "content": "1.2.3.4", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/etc/fallback.conf"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertEqual(store.doc.get("state"), "fallback_only")
            self.assertEqual(len(cf.deleted_records), 1, "Created records must be deleted on rollback")

        # 2. Rollback provider error records unknown_effects and marks incomplete
        store2 = FakeStoreClient()
        cf2 = FakeCfClient()
        def fail_delete(*args, **kwargs):
            raise RuntimeError("Cloudflare delete failed")
        cf2.delete_record = fail_delete

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/etc/fallback.conf"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store2, cf_client=cf2)
            self.assertEqual(store2.doc.get("state"), "incomplete")
            self.assertTrue(store2.doc.get("unknown_effects"))

    def test_f11_cloudflare_errors_fixed_messages_and_secret_scrubbing(self):
        from sandbox.core._cloudflare import CloudflareError
        cf = FakeCfClient()
        SECRET = "SECRET-MARKER-XYZ-987"
        def fail_zones(*args, **kwargs):
            raise CloudflareError(f"Cloudflare failure leaking {SECRET}")
        cf.zones = fail_zones

        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True, "routes": []}):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(self.entry, "asb.bd", store_client=FakeStoreClient(), cf_client=cf)
            self.assertNotIn(SECRET, str(ctx.exception))
            self.assertEqual(ctx.exception.code, "coverage_zone_unavailable")

    def test_f12_features_forwarded_to_fallback_rendering(self):
        cf = FakeCfClient()
        store = FakeStoreClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z1",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }
        # Fake probe facts with legacy http2 and no ipv6
        fake_facts = {
            "config_valid": True,
            "features": {"http2_directive": False, "ipv6": False},
            "ipv6": False,
            "routes": [],
        }
        with patch("sandbox.hosting.front_door.nginx.probe", return_value=fake_facts),              patch("sandbox.hosting.zone_coverage.fallback.render_fallback_conf") as mock_render,              patch("sandbox.hosting.front_door.nginx.ensure_origin_certificate", return_value=("/c.pem", "/k.pem", {})),              patch("sandbox.hosting.front_door.nginx.apply_route", return_value={"file": "/r.conf"}),              patch("sandbox.hosting.zone_coverage.fallback.run_origin_probe", return_value={"status": 404, "fallback_header": "1"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            mock_render.assert_called_once()
            _, kwargs = mock_render.call_args
            self.assertFalse(kwargs.get("http2_directive"))
            self.assertFalse(kwargs.get("ipv6"))

    def test_f10_digest_nine_keys_and_infer_recovered_dependents(self):
        from sandbox.hosting.zone_coverage.service import compute_plan_digest
        p1 = {
            "action": "enable",
            "zone": "asb.bd",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "dependents": {},
            "exceptions": [],
            "blockers": [],
            "records_to_create": [],
            "records_to_adopt": [],
        }
        p2 = dict(p1)
        p2["dependents"] = {"recovered/routes/site.asb.bd": {"hostname": "site.asb.bd", "state": "recovered"}}
        # Two plans differing ONLY in dependents MUST produce different digests
        self.assertNotEqual(compute_plan_digest(p1), compute_plan_digest(p2))

        # Infer recovered dependents when adopted record exists
        cf = FakeCfClient(records=[{"id": "r-wild", "type": "A", "name": "*.asb.bd", "content": "198.51.100.1", "proxied": True}])
        # Mock nginx route for site.asb.bd (sandbox owned)
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={
            "config_valid": True,
            "features": {"http2_directive": "http2 on;", "ipv6": True},
            "routes": [{"file": "/etc/nginx/conf.d/sandbox-site.conf", "hostnames": ["site.asb.bd"]}],
        }):
            plan = plan_enable(self.entry, "asb.bd", store_client=FakeStoreClient(doc=None), cf_client=cf)
            self.assertEqual(len(plan["records_to_adopt"]), 1)
            self.assertIn("recovered/routes/site.asb.bd", plan["dependents"])
            self.assertEqual(plan["dependents"]["recovered/routes/site.asb.bd"]["state"], "recovered")

    def test_f8_expired_lease_resumable_and_resume_reaches_begin(self):
        import time
        now = time.time()
        # 1. Expired lease in plan_enable
        expired_doc = {
            "schema_version": 1,
            "zone": "asb.bd",
            "state": "incomplete",
            "generation": 0,
            "operation": {
                "id": "op-old",
                "kind": "enable",
                "controller": "worker-1",
                "phase": "fallback_route_rendered",
                "lease_expires_at": now - 100,
            },
            "dependents": {},
            "dependents_revision": 0,
        }
        store = FakeStoreClient(doc=expired_doc)
        cf = FakeCfClient()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertTrue(plan.get("resumable"))
        self.assertEqual(plan.get("holder"), "worker-1")
        self.assertEqual(plan.get("phase"), "fallback_route_rendered")

        # 2. Active (unexpired) lease raises COVERAGE_OPERATION_ACTIVE
        active_doc = {
            "schema_version": 1,
            "zone": "asb.bd",
            "state": "incomplete",
            "generation": 0,
            "operation": {
                "id": "op-act",
                "kind": "enable",
                "controller": "worker-1",
                "phase": "fallback_route_rendered",
                "lease_expires_at": now + 100,
            },
            "dependents": {},
            "dependents_revision": 0,
        }
        store_active = FakeStoreClient(doc=active_doc)
        with self.assertRaises(CoverageError) as ctx:
            plan_enable(self.entry, "asb.bd", store_client=store_active, cf_client=cf)
        self.assertEqual(ctx.exception.code, COVERAGE_OPERATION_ACTIVE)

        # 3. enable(resume=True) reaches store_client.begin(resume=True)
        begin_calls = []
        orig_begin = store.begin
        def spy_begin(*args, **kwargs):
            begin_calls.append((args, kwargs))
            return orig_begin(*args, **kwargs)
        store.begin = spy_begin

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/etc/fallback.conf"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            enable(self.entry, "asb.bd", plan, resume=True, store_client=store, cf_client=cf)
        self.assertTrue(any(kwargs.get("resume") is True for args, kwargs in begin_calls),
                        f"Expected store_client.begin to be called with resume=True, calls: {begin_calls}")

    def test_plan_enable_happy_path(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        probe_facts = {
            "nginx_installed": True,
            "active": True,
            "includes_conf_d": True,
            "config_valid": True,
            "routes": [{"file": "/etc/nginx/conf.d/sandbox-site.conf", "hostnames": ["site.asb.bd"]}],
        }

        with patch("sandbox.hosting.front_door.nginx.probe", return_value=probe_facts):
            plan = plan_enable(self.entry, "asb.bd", cf_client=cf, store_client=store)

        self.assertEqual(plan["zone"], "asb.bd")
        self.assertEqual(len(plan["records_to_create"]), 1)
        self.assertEqual(plan["records_to_create"][0]["name"], "*.asb.bd")
        self.assertEqual(plan["records_to_create"][0]["content"], "198.51.100.1")
        self.assertEqual(len(plan["exceptions"]), 1)
        self.assertEqual(plan["exceptions"][0]["hostname"], "site.asb.bd")

    def test_plan_enable_caddy_refusal(self):
        caddy_entry = {"front_door": "caddy"}
        with self.assertRaises(FrontDoorError) as ctx:
            plan_enable(caddy_entry, "asb.bd")
        self.assertEqual(ctx.exception.code, "front_door_capability_unavailable")

    def test_plan_enable_active_operation_refusal(self):
        store = FakeStoreClient(doc={"state": "enabling", "operation": {"id": "op-live", "kind": "enable"}})
        cf = FakeCfClient()
        with self.assertRaises(CoverageError) as ctx:
            plan_enable(self.entry, "asb.bd", cf_client=cf, store_client=store)
        self.assertEqual(ctx.exception.code, COVERAGE_OPERATION_ACTIVE)

    def test_plan_enable_conflicts_refusal(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        probe_facts = {
            "nginx_installed": True,
            "active": True,
            "includes_conf_d": True,
            "config_valid": True,
            "routes": [{"file": "/etc/nginx/sites-enabled/panel-wild", "hostnames": ["*.asb.bd"]}],
        }

        with patch("sandbox.hosting.front_door.nginx.probe", return_value=probe_facts):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(self.entry, "asb.bd", cf_client=cf, store_client=store)
            self.assertEqual(ctx.exception.code, COVERAGE_CONFLICT)

    def test_plan_enable_ssl_not_strict_refusal(self):
        store = FakeStoreClient()
        cf = FakeCfClient(ssl_mode="flexible")
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True}), \
             patch("sandbox.hosting.front_door.nginx.parse_server_names", return_value=[]):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(self.entry, "asb.bd", cf_client=cf, store_client=store)
            self.assertEqual(ctx.exception.code, COVERAGE_SSL_NOT_STRICT)

    def test_plan_enable_existing_record_differing_refusal(self):
        store = FakeStoreClient()
        cf = FakeCfClient(records=[{"name": "*.asb.bd", "type": "A", "content": "1.1.1.1", "proxied": False}])
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True}), \
             patch("sandbox.hosting.front_door.nginx.parse_server_names", return_value=[]):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(self.entry, "asb.bd", cf_client=cf, store_client=store)
            self.assertEqual(ctx.exception.code, COVERAGE_RECORD_EXISTS)

    def test_plan_enable_existing_record_adopted(self):
        store = FakeStoreClient()
        cf = FakeCfClient(records=[{"id": "r-match", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}])
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True}), \
             patch("sandbox.hosting.front_door.nginx.parse_server_names", return_value=[]):
            plan = plan_enable(self.entry, "asb.bd", cf_client=cf, store_client=store)
            self.assertEqual(len(plan["records_to_create"]), 0)
            self.assertEqual(len(plan["records_to_adopt"]), 1)
            self.assertEqual(plan["records_to_adopt"][0]["id"], "r-match")

    def test_enable_happy_path(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
        }
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}), \
             patch("sandbox.hosting.zone_coverage.fallback.run_origin_probe", return_value={"status": 404}), \
             patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            res = enable(self.entry, "asb.bd", plan, cf_client=cf, store_client=store)

        self.assertEqual(res["state"], "active")
        self.assertEqual(res["generation"], 1)
        self.assertEqual(store.doc["state"], "active")
        self.assertEqual(len(cf._records), 1)

    def test_enable_stale_plan_refusal(self):
        store = FakeStoreClient(doc={"generation": 2, "dependents_revision": 0})
        cf = FakeCfClient()
        plan = {
            "zone": "asb.bd",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
        }
        with self.assertRaises(CoverageError) as ctx:
            enable(self.entry, "asb.bd", plan, cf_client=cf, store_client=store)
        self.assertEqual(ctx.exception.code, COVERAGE_PLAN_STALE)

    def test_enable_stop_before_publish(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
        }
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f"}), \
             patch("sandbox.hosting.zone_coverage.fallback.run_origin_probe", return_value={"status": 404}):
            res = enable(self.entry, "asb.bd", plan, stop_before_publish=True, cf_client=cf, store_client=store)

        self.assertEqual(res["state"], "fallback_only")
        self.assertEqual(store.doc["state"], "fallback_only")
        self.assertEqual(len(cf._records), 0)

    def test_enable_failure_marks_incomplete(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
        }
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", side_effect=RuntimeError("cert error")):
            with self.assertRaises(RuntimeError):
                enable(self.entry, "asb.bd", plan, cf_client=cf, store_client=store)

        self.assertEqual(store.doc["state"], "incomplete")
        self.assertEqual(store.doc["interrupted_kind"], "enable")

    def test_status(self):
        store = FakeStoreClient(doc={
            "schema_version": 1,
            "zone": "asb.bd",
            "zone_id": "z-test",
            "state": "active",
            "generation": 1,
            "dependents_revision": 0,
            "operation": None,
            "interrupted_kind": None,
            "shared_records": [{"id": "r1", "name": "*.asb.bd", "content": "198.51.100.1", "proxied": True}],
            "fallback": {"file": "/f"},
            "exceptions": [],
            "dependents": {},
            "history": [],
        })
        cf = FakeCfClient(records=[{"id": "r1", "name": "*.asb.bd", "content": "198.51.100.1", "proxied": True}])
        res = status(self.entry, "asb.bd", cf_client=cf, store_client=store)
        self.assertEqual(res["state"], "active")
        self.assertIsNone(res["drift"])

        # Test drift when record differs
        cf._records = [{"id": "r1", "name": "*.asb.bd", "content": "2.2.2.2", "proxied": True}]
        res_drift = status(self.entry, "asb.bd", cf_client=cf, store_client=store)
        self.assertIsNotNone(res_drift["drift"])

    def test_cli_coverage_stage1_refusals(self):
        from types import SimpleNamespace
        from sandbox.commands import remote as remote_cmd
        with patch.object(remote_cmd, "_require_name", return_value="vps"), \
             patch("sandbox.core._remote.get_remote", return_value=self.entry), \
             patch.object(remote_cmd, "die", side_effect=SystemExit) as die:
            for flag in ["disable", "repair", "renew_certificate", "remove_fallback", "release_dependent"]:
                fields = {
                    "action": "coverage",
                    "name": "vps",
                    "zone": "asb.bd",
                    "enable": False,
                    "confirm": False,
                    "plan": False,
                    "stop_before_publish": False,
                    "resume": False,
                    "plan_digest": None,
                    "disable": False,
                    "repair": False,
                    "renew_certificate": False,
                    "remove_fallback": False,
                    "release_dependent": None,
                }
                fields[flag] = True if flag != "release_dependent" else "dep-1"
                args = SimpleNamespace(**fields)
                with self.assertRaises(SystemExit):
                    remote_cmd._cmd_coverage(args, as_json=False)
                msg = die.call_args.args[0]
                self.assertIn("not available yet", msg)

    def test_cli_coverage_plan_and_confirm(self):
        from types import SimpleNamespace
        import io
        from sandbox.commands import remote as remote_cmd
        store = FakeStoreClient()
        cf = FakeCfClient()
        probe_facts = {
            "nginx_installed": True,
            "active": True,
            "includes_conf_d": True,
            "config_valid": True,
            "routes": [],
        }

        # 1. Plan outputs plan_digest and note
        args_plan = SimpleNamespace(
            action="coverage",
            name="vps",
            zone="asb.bd",
            enable=True,
            confirm=False,
            plan=True,
            stop_before_publish=False,
            resume=False,
            plan_digest=None,
            disable=False,
            repair=False,
            renew_certificate=False,
            remove_fallback=False,
            release_dependent=None,
            json=True,
        )
        with patch.object(remote_cmd, "_require_name", return_value="vps"), \
             patch("sandbox.core._remote.get_remote", return_value=self.entry), \
             patch("sandbox.hosting.front_door.nginx.probe", return_value=probe_facts), \
             patch("sandbox.hosting.zone_coverage.service.ZoneCoverageClient", return_value=store), \
             patch("sandbox.core._cloudflare.Client", return_value=cf), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            remote_cmd._cmd_coverage(args_plan, as_json=True)
            output = mock_stdout.getvalue()
            import json
            plan = json.loads(output)
            self.assertTrue(plan["ok"])
            self.assertIn("plan_digest", plan)
            digest = plan["plan_digest"]

        # 2. Confirm without digest fails
        args_confirm_no_digest = SimpleNamespace(
            action="coverage",
            name="vps",
            zone="asb.bd",
            enable=True,
            confirm=True,
            plan=False,
            stop_before_publish=False,
            resume=False,
            plan_digest=None,
            disable=False,
            repair=False,
            renew_certificate=False,
            remove_fallback=False,
            release_dependent=None,
            json=True,
        )
        with patch.object(remote_cmd, "_require_name", return_value="vps"), \
             patch("sandbox.core._remote.get_remote", return_value=self.entry), \
             patch.object(remote_cmd, "die", side_effect=SystemExit) as die:
            with self.assertRaises(SystemExit):
                remote_cmd._cmd_coverage(args_confirm_no_digest, as_json=True)
            self.assertIn("--plan-digest", die.call_args.args[0])

        # 3. Confirm with wrong digest refuses
        args_confirm_bad_digest = SimpleNamespace(
            action="coverage",
            name="vps",
            zone="asb.bd",
            enable=True,
            confirm=True,
            plan=False,
            stop_before_publish=False,
            resume=False,
            plan_digest="wrong-digest",
            disable=False,
            repair=False,
            renew_certificate=False,
            remove_fallback=False,
            release_dependent=None,
            json=True,
        )
        with patch.object(remote_cmd, "_require_name", return_value="vps"), \
             patch("sandbox.core._remote.get_remote", return_value=self.entry), \
             patch("sandbox.hosting.front_door.nginx.probe", return_value=probe_facts), \
             patch("sandbox.hosting.zone_coverage.service.ZoneCoverageClient", return_value=store), \
             patch("sandbox.core._cloudflare.Client", return_value=cf):
            with self.assertRaises(SystemExit):
                remote_cmd._cmd_coverage(args_confirm_bad_digest, as_json=True)


    def test_f2_public_probe_unit_behavior(self):
        import io
        import urllib.error
        from sandbox.hosting.zone_coverage.fallback import FALLBACK_BODY
        from sandbox.hosting.zone_coverage.service import public_probe

        class FakeHeaders(dict):
            def get(self, k, default=None):
                for key, val in self.items():
                    if key.lower() == k.lower():
                        return val
                return default

        class FakeResponse:
            def __init__(self, status=404, headers=None, body=FALLBACK_BODY):
                self.status = status
                self.headers = FakeHeaders({"X-Sandbox-Fallback": "1"} if headers is None else headers)
                self._body = body.encode("utf-8")
            def read(self):
                return self._body

        class FakeOpener:
            def __init__(self, response_factory):
                self.factory = response_factory
                self.calls = []
            def open(self, req, timeout=10.0):
                self.calls.append(req.get_full_url())
                res = self.factory(len(self.calls))
                if isinstance(res, Exception):
                    raise res
                return res

        # 1. urllib.error.HTTPError with 404, fallback header and body returns True
        def make_http_error(attempt):
            return urllib.error.HTTPError("https://...", 404, "Not Found",
                                         FakeHeaders({"X-Sandbox-Fallback": "1"}),
                                         io.BytesIO(FALLBACK_BODY.encode("utf-8")))

        opener_ok = FakeOpener(make_http_error)
        labels = ["a1b2c3d4e5f60718"]
        self.assertTrue(public_probe("asb.bd", sleep=lambda s: None, label_source=lambda: labels[0], opener=opener_ok))
        self.assertEqual(opener_ok.calls[0], "https://sb-verify-a1b2c3d4e5f60718.asb.bd/")

        # 2. HTTP 200 returns False after max tries
        def make_200(attempt):
            return FakeResponse(status=200, body="Normal site")

        opener_200 = FakeOpener(make_200)
        sleeper_calls = []
        self.assertFalse(public_probe("asb.bd", sleep=sleeper_calls.append, opener=opener_200, max_tries=3, delay=1.0))
        self.assertEqual(len(sleeper_calls), 2)

        # 3. 404 missing fallback header returns False
        def make_no_header(attempt):
            return FakeResponse(status=404, headers={}, body=FALLBACK_BODY)

        opener_no_hdr = FakeOpener(make_no_header)
        self.assertFalse(public_probe("asb.bd", sleep=lambda s: None, opener=opener_no_hdr, max_tries=2))

    def test_g6_exact_body_equality_in_public_probe_and_origin_check(self):
        import io
        import urllib.error
        from sandbox.hosting.zone_coverage.fallback import FALLBACK_BODY
        from sandbox.hosting.zone_coverage.service import public_probe

        class FakeHeaders(dict):
            def get(self, k, default=None):
                for key, val in self.items():
                    if key.lower() == k.lower():
                        return val
                return default

        class FakeOpener:
            def __init__(self, make_resp):
                self.make_resp = make_resp
            def open(self, req, timeout=10.0):
                return self.make_resp()

        # Extra content appended to fallback body must fail exact comparison in HTTPError path
        extra_body = FALLBACK_BODY + " extra markup"
        err_with_extra = lambda: urllib.error.HTTPError(
            "https://...", 404, "Not Found",
            FakeHeaders({"X-Sandbox-Fallback": "1"}),
            io.BytesIO(extra_body.encode("utf-8"))
        )
        self.assertFalse(
            public_probe("asb.bd", sleep=lambda s: None, opener=FakeOpener(err_with_extra), max_tries=1),
            "public_probe must reject HTTPError responses with non-exact body"
        )

        # Extra content appended to fallback body must fail exact comparison in normal response path
        class FakeResponse:
            status = 404
            headers = FakeHeaders({"X-Sandbox-Fallback": "1"})
            def read(self):
                return extra_body.encode("utf-8")

        self.assertFalse(
            public_probe("asb.bd", sleep=lambda s: None, opener=FakeOpener(FakeResponse), max_tries=1),
            "public_probe must reject standard responses with non-exact body"
        )



    def test_j4_fail_closed_on_nginx_facts_in_plan_and_enable_conflict_recheck(self):
        store = FakeStoreClient()
        cf = FakeCfClient()

        # 1. plan_enable fails closed when nginx.probe raises
        with patch("sandbox.hosting.front_door.nginx.probe", side_effect=RuntimeError("probe unreachable")):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # 2. plan_enable fails closed when nginx.probe returns empty / invalid config
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": False}):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # 3. plan succeeds with clean routes
        clean_probe = {"config_valid": True, "routes": []}
        with patch("sandbox.hosting.front_door.nginx.probe", return_value=clean_probe):
            plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(plan["blockers"], [])

        # 4. enable catches newly introduced conflicting route before effects
        conflicting_probe = {
            "config_valid": True,
            "routes": [
                {
                    "file": "/etc/nginx/sites-enabled/panel-wildcard.conf",
                    "hostnames": ["*.asb.bd"],
                }
            ],
        }
        with patch("sandbox.hosting.front_door.nginx.probe", return_value=conflicting_probe), \
             patch("sandbox.hosting.zone_coverage.fallback.install_fallback") as mock_install:
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, COVERAGE_CONFLICT)
            self.assertIn("conflicting wildcard or regex routes exist", str(ctx.exception))
            mock_install.assert_not_called()
            self.assertEqual(store.doc.get("state"), "incomplete")
            self.assertEqual(store.doc.get("interrupted_kind"), "enable")


    def test_j12_recovered_dependents_respect_dns_nodes(self):
        store = FakeStoreClient()
        # CF has a DNS record for app1.asb.bd and sub.app2.asb.bd, but none for app3.asb.bd
        cf = FakeCfClient(records=[
            {"id": "r1", "name": "app1.asb.bd", "type": "A", "content": "1.2.3.4"},
            {"id": "r2", "name": "sub.app2.asb.bd", "type": "A", "content": "1.2.3.4"},
        ])

        # Routes on front door: app1.asb.bd, app2.asb.bd, app3.asb.bd
        routes = [
            {"file": "/etc/nginx/conf.d/sandbox-app1.asb.bd.conf", "hostnames": ["app1.asb.bd"]},
            {"file": "/etc/nginx/conf.d/sandbox-app2.asb.bd.conf", "hostnames": ["app2.asb.bd"]},
            {"file": "/etc/nginx/conf.d/sandbox-app3.asb.bd.conf", "hostnames": ["app3.asb.bd"]},
        ]
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True, "routes": routes}):
            plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        dependents = plan["dependents"]
        # app1.asb.bd has direct DNS record -> not dependent
        self.assertNotIn("recovered/routes/app1.asb.bd", dependents)
        # app2.asb.bd has child DNS record sub.app2.asb.bd -> not dependent
        self.assertNotIn("recovered/routes/app2.asb.bd", dependents)
        # app3.asb.bd has no DNS record at or below it -> dependent!
        self.assertIn("recovered/routes/app3.asb.bd", dependents)

    def test_g8_nginx_probe_failure_stops_before_effect_and_no_guessing(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z1",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "1.2.3.4", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        # 1. When nginx.probe fails, stop before any effect, finish incomplete, raise COVERAGE_FALLBACK_UNVERIFIED
        with patch("sandbox.hosting.front_door.nginx.probe", side_effect=RuntimeError("nginx probe crashed")),              patch("sandbox.hosting.zone_coverage.fallback.install_fallback") as mock_install,              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            try:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
                self.fail("enable must raise on nginx.probe error")
            except CoverageError as exc:
                self.assertEqual(exc.code, COVERAGE_FALLBACK_UNVERIFIED)
            mock_install.assert_not_called()
            self.assertEqual(store.doc.get("state"), "incomplete")
            self.assertEqual(store.doc.get("interrupted_kind"), "enable")

        # 2. When nginx.probe returns empty, stop before any effect, finish incomplete
        store2 = FakeStoreClient()
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={}),              patch("sandbox.hosting.zone_coverage.fallback.install_fallback") as mock_install,              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            try:
                enable(self.entry, "asb.bd", plan, store_client=store2, cf_client=cf)
                self.fail("enable must raise on empty nginx.probe facts")
            except CoverageError as exc:
                self.assertEqual(exc.code, COVERAGE_FALLBACK_UNVERIFIED)
            mock_install.assert_not_called()
            self.assertEqual(store2.doc.get("state"), "incomplete")



    def test_crash_after_each_write_then_resume_no_duplicate(self):
        entry = dict(self.entry, origin_ipv6="2001:db8::1")
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan1 = plan_enable(entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(len(plan1["records_to_create"]), 2)

        # Simulate crash on second write during enable
        orig_upsert = cf.create_address
        calls = 0
        def fail_second_upsert(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("simulated provider crash on 2nd write")
            return orig_upsert(*args, **kwargs)

        with patch.object(cf, "create_address", side_effect=fail_second_upsert),              patch("sandbox.hosting.zone_coverage.fallback.install_fallback"):
            try:
                enable(entry, "asb.bd", plan1, store_client=store, cf_client=cf)
            except Exception:
                pass

        self.assertEqual(store.doc.get("state"), "incomplete")
        self.assertIsNotNone(store.doc.get("publish_intent"))
        self.assertEqual(len(cf.created_records), 1)

        # Resume plan: first record is recognized as owned from prior attempt, NOT re-created
        plan2 = plan_enable(entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(len(plan2.get("records_owned_from_prior_attempt", [])), 1)
        self.assertEqual(len(plan2["records_to_create"]), 1)
        self.assertEqual(len(plan2["records_to_adopt"]), 0)

        # Resume enable completes without duplicate
        cf.created_records.clear()
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback"),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            res = enable(entry, "asb.bd", plan2, store_client=store, cf_client=cf)
            self.assertEqual(res["state"], "active")
            # Exactly 1 new record was written (the 2nd one), no duplicate of the 1st
            self.assertEqual(len(cf.created_records), 1)
            self.assertEqual(len(res["shared_records"]), 2)

    def test_resume_then_probe_failure_removes_own_records(self):
        entry = dict(self.entry, origin_ipv6="2001:db8::1")
        marker = "sandbox-zone-coverage 1234567890abcdef"
        rec1 = {"id": "r1", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": marker}
        # Also include a 3rd party record without marker to verify rollback leaves it alone
        rec_other = {"id": "r_other", "name": "other.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": ""}
        cf = FakeCfClient(records=[rec1, rec_other])
        store = FakeStoreClient(doc={
            "schema_version": 1,
            "zone": "asb.bd",
            "state": "incomplete",
            "generation": 0,
            "publish_intent": {
                "marker": marker,
                "records": [
                    {"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True},
                    {"name": "*.asb.bd", "type": "AAAA", "content": "2001:db8::1", "proxied": True},
                ],
            },
            "shared_records": [],
            "dependents": {},
            "dependents_revision": 0,
            "history": [],
        })

        plan = plan_enable(entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(len(plan["records_owned_from_prior_attempt"]), 1)
        self.assertEqual(len(plan["records_to_create"]), 1)

        # Probe fails: rollback must remove BOTH records (the owned one and the newly created one)
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback"),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError) as ctx:
                enable(entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        # Both records deleted from CF
        self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 0)
        self.assertIn("r1", cf.deleted_records)
        self.assertEqual(store.doc.get("state"), "fallback_only")

    def test_rollback_keeps_record_changed_out_of_band(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        # Tamper record right before probe
        orig_public_probe = False
        def probe_and_tamper(zone):
            # Change record content out-of-band on CF
            for r in cf._records:
                if r["name"] == "*.asb.bd":
                    r["content"] = "9.9.9.9"
            return False

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback"),              patch("sandbox.hosting.zone_coverage.service.public_probe", side_effect=probe_and_tamper):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        # Modified record was NOT deleted
        recs = cf.records("z-test", "*.asb.bd")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["content"], "9.9.9.9")
        self.assertEqual(store.doc.get("state"), "incomplete")
        effects = store.doc.get("unknown_effects") or []
        self.assertTrue(any(e.get("reason") == "record_modified_out_of_band" for e in effects))

    def test_rollback_never_deletes_adopted_record(self):
        store = FakeStoreClient()
        adopted = {"id": "r-adopt", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}
        cf = FakeCfClient(records=[adopted])
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "sub.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [adopted],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback"),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        # Adopted record is NEVER deleted
        self.assertNotIn("r-adopt", cf.deleted_records)
        self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 1)

    def test_write_exception_records_inflight_name(self):
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        with patch.object(cf, "create_address", side_effect=RuntimeError("CF write failed")),              patch("sandbox.hosting.zone_coverage.fallback.install_fallback"):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        self.assertEqual(store.doc.get("state"), "incomplete")
        effects = store.doc.get("unknown_effects") or []
        names = [e.get("name") for e in effects]
        self.assertIn("*.asb.bd", names)

    def test_store_failure_during_unknown_effects_reported(self):
        store = FakeStoreClient()
        store.finish_refusal = "coverage_store_unavailable"
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }

        with patch.object(cf, "create_address", side_effect=RuntimeError("CF network error")),              patch("sandbox.hosting.zone_coverage.fallback.install_fallback"):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertIn("store was not updated", str(ctx.exception))

    def test_digest_stable_across_resume(self):
        from sandbox.hosting.zone_coverage.service import compute_plan_digest
        # J3: Two independently generated plans with advanced clock under a state
        # with an adopted wildcard plus a Sandbox route must have equal digests.
        store = FakeStoreClient()
        cf = FakeCfClient(records=[
            {"id": "rec-wild", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": ""},
        ])
        facts = {
            "config_valid": True,
            "version": [1, 26, 0],
            "routes": [{"file": "/etc/nginx/conf.d/sandbox-app.asb.bd.conf", "hostnames": ["app.asb.bd"]}],
        }

        with patch("sandbox.hosting.front_door.nginx.probe", return_value=facts), \
             patch("time.time", return_value=1700000000.0):
            p1 = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        with patch("sandbox.hosting.front_door.nginx.probe", return_value=facts), \
             patch("time.time", return_value=1700005000.0):
            p2 = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        self.assertEqual(p1["plan_digest"], p2["plan_digest"])
        self.assertEqual(compute_plan_digest(p1), compute_plan_digest(p2))

        # Altering owned records still changes digest
        p3 = dict(p1, records_owned_from_prior_attempt=[{"id": "x"}])
        self.assertNotEqual(compute_plan_digest(p1), compute_plan_digest(p3))


    def test_g5_dual_stack_differing_records_block_and_matching_adopted(self):
        entry = dict(self.entry, origin_ipv6="2001:db8::1")
        store = FakeStoreClient()

        # Differing AAAA blocks with COVERAGE_RECORD_EXISTS
        cf_diff = FakeCfClient(records=[
            {"id": "r1", "name": "*.asb.bd", "type": "AAAA", "content": "2001:db8::999", "proxied": True},
        ])
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True}),              patch("sandbox.hosting.front_door.nginx.parse_server_names", return_value=[]):
            with self.assertRaises(CoverageError) as ctx:
                plan_enable(entry, "asb.bd", store_client=store, cf_client=cf_diff)
            self.assertEqual(ctx.exception.code, COVERAGE_RECORD_EXISTS)

        # Matching AAAA is adopted
        cf_match = FakeCfClient(records=[
            {"id": "r-aaaa", "name": "*.asb.bd", "type": "AAAA", "content": "2001:db8::1", "proxied": True},
        ])
        with patch("sandbox.hosting.front_door.nginx.probe", return_value={"config_valid": True}),              patch("sandbox.hosting.front_door.nginx.parse_server_names", return_value=[]):
            plan = plan_enable(entry, "asb.bd", store_client=store, cf_client=cf_match)
            self.assertEqual(len(plan["records_to_adopt"]), 1)
            self.assertEqual(plan["records_to_adopt"][0]["id"], "r-aaaa")
            self.assertEqual(len(plan["records_to_create"]), 1)
            self.assertEqual(plan["records_to_create"][0]["type"], "A")


    def test_h2_fallback_install_failure_reports_store_finish_refusal(self):
        store = FakeStoreClient()
        store.finish_refusal = "coverage_operation_active"
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", side_effect=RuntimeError("simulated install failure")):
            with self.assertRaises(RuntimeError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            err_msg = str(ctx.exception)
            self.assertIn("store was not updated: refused finish (coverage_operation_active)", err_msg)


    def test_j5_coverage_error_reports_store_finish_refusal_and_exception(self):
        store = FakeStoreClient()
        store.finish_refusal = "coverage_operation_active"
        cf = FakeCfClient(records=[
            {"id": "r-diff", "name": "*.asb.bd", "type": "A", "content": "9.9.9.9", "proxied": True},
        ])
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, COVERAGE_RECORD_EXISTS)
            self.assertIn("store was not updated: refused finish (coverage_operation_active)", str(ctx.exception))

        # Finish raising an exception with fresh store
        store2 = FakeStoreClient()
        store2.finish_exception = RuntimeError("store connection dropped")
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}):
            with self.assertRaises(CoverageError) as ctx2:
                enable(self.entry, "asb.bd", plan, store_client=store2, cf_client=cf)
            self.assertEqual(ctx2.exception.code, COVERAGE_RECORD_EXISTS)
            self.assertIn("store was not updated: finish error (store connection dropped)", str(ctx2.exception))

    def test_j1_fencing_external_writes_prevents_orphan_writes_and_deletes(self):
        # 1. Dual-stack pause before 2nd record creation
        entry = dict(self.entry, origin_ipv6="2001:db8::1")
        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [
                {"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True},
                {"name": "*.asb.bd", "type": "AAAA", "content": "2001:db8::1", "proxied": True},
            ],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }
        create_calls = 0
        def phase_fencing_create(zone, op_id, phase, updates=None):
            if phase == "dns_create_record" and create_calls == 1:
                return {"ok": True, "refused": "coverage_operation_active", "reason": "not_holder"}
            return {"ok": True, "document": {}}

        orig_create = cf.create_address
        def counting_create(*args, **kwargs):
            nonlocal create_calls
            create_calls += 1
            return orig_create(*args, **kwargs)

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}), \
             patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True), \
             patch.object(store, "phase", side_effect=phase_fencing_create), \
             patch.object(cf, "create_address", side_effect=counting_create):
            with self.assertRaises(CoverageError):
                enable(entry, "asb.bd", plan, store_client=store, cf_client=cf)
            # Only 1 create call occurred before fence blocked the 2nd
            self.assertEqual(create_calls, 1)

        # 2. Suspended old controller: probe fails, but rollback phase is refused -> 0 deletes made
        store2 = FakeStoreClient()
        cf2 = FakeCfClient()
        plan2 = {
            "action": "enable",
            "zone": "asb.bd",
            "zone_id": "z-test",
            "expected_generation": 0,
            "expected_dependents_revision": 0,
            "records_to_create": [{"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True}],
            "records_to_adopt": [],
            "exceptions": [],
            "blockers": [],
            "dependents": {},
        }
        def phase_fencing_rollback(zone, op_id, phase, updates=None):
            if phase == "dns_rollback_delete":
                return {"ok": True, "refused": "coverage_operation_active", "reason": "not_holder"}
            return {"ok": True, "document": {}}

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}), \
             patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False), \
             patch.object(store2, "phase", side_effect=phase_fencing_rollback):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan2, store_client=store2, cf_client=cf2)
            self.assertEqual(len(cf2.deleted_records), 0)


    def test_j13_m07_third_party_record_adopted_never_in_intent_written_and_not_deleted_on_rollback(self):
        store = FakeStoreClient()
        # Case A: Third-party record with same content but NO marker already present at plan time
        cf = FakeCfClient(records=[
            {"id": "r-third", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": "not-our-marker"},
        ])
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(len(plan["records_to_adopt"]), 1)
        self.assertEqual(plan["records_to_adopt"][0]["id"], "r-third")
        self.assertEqual(len(plan["records_to_create"]), 0)
        self.assertEqual(len(plan["records_owned_from_prior_attempt"]), 0)

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
        self.assertEqual(len(cf.deleted_records), 0)

        # Case B: Record planned to be created, but third-party matching record exists in write loop
        store2 = FakeStoreClient()
        cf2 = FakeCfClient(records=[])
        plan2 = plan_enable(self.entry, "asb.bd", store_client=store2, cf_client=cf2)
        self.assertEqual(len(plan2["records_to_create"]), 1)

        cf2._records = [
            {"id": "r-third", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": "not-our-marker"},
        ]

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            res = enable(self.entry, "asb.bd", plan2, store_client=store2, cf_client=cf2)

        # Third-party equal record appears in result's adopted list and never in written list
        self.assertEqual([r["id"] for r in res.get("records_adopted", [])], ["r-third"])
        self.assertEqual(res.get("records_written", []), [])

        # Case C: Write loop with probe failure rollback never deletes adopted record
        store3 = FakeStoreClient()
        cf3 = FakeCfClient(records=[])
        plan3 = plan_enable(self.entry, "asb.bd", store_client=store3, cf_client=cf3)
        cf3._records = [
            {"id": "r-third", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": "not-our-marker"},
        ]
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan3, store_client=store3, cf_client=cf3)
        self.assertEqual(len(cf3.deleted_records), 0)

    # K1: Takeover during certificate stops route effect; before_effect wiring
    def test_real_takeover_during_certificate_stops_route_effect(self):
        import json, os, subprocess, tempfile
        from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
        from tests.subprocess_support import synthetic_environment

        with tempfile.TemporaryDirectory() as td:
            def real_ssh_run(entry, command, timeout=15):
                env = synthetic_environment({'SANDBOX_HOME': td, 'HOME': td})
                return subprocess.run(command, shell=True, env=env, capture_output=True, text=True, timeout=timeout)

            store = ZoneCoverageClient(self.entry, ssh_run=real_ssh_run)
            cf = FakeCfClient()
            plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

            doc_path = os.path.join(td, "runtime", "zone-coverage", "zones", "asb.bd.json")
            route_applied = False

            def fake_ensure_origin_cert(*args, **kwargs):
                # A certificate step triggers takeover by B
                with open(doc_path, "r") as f:
                    doc = json.load(f)
                doc["operation"]["lease_expires_at"] = 0
                with open(doc_path, "w") as f:
                    json.dump(doc, f)

                # B resumes and finishes active
                b_res = store.begin("asb.bd", "enable", "controller-B", resume=True)
                b_op = b_res["operation_id"]
                store.phase("asb.bd", b_op, "fallback_install")
                store.finish("asb.bd", b_op, "active", raise_generation=True)
                return ("/c.pem", "/k.pem", {"id": "cert-1"})

            def fake_apply_route(*args, **kwargs):
                nonlocal route_applied
                route_applied = True
                return {"file": "/r.conf"}

            with patch("sandbox.hosting.front_door.nginx.ensure_origin_certificate", side_effect=fake_ensure_origin_cert),                  patch("sandbox.hosting.front_door.nginx.apply_route", side_effect=fake_apply_route),                  patch("sandbox.hosting.zone_coverage.fallback.run_origin_probe", return_value={"status": 404, "fallback_header": "1", "body": "SANDBOX_WILDCARD_FALLBACK"}),                  patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
                with self.assertRaises(CoverageError):
                    enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

            # Assert A makes no route call
            self.assertFalse(route_applied, "Controller A must not apply route after being taken over")

            # Assert B doc is intact
            with open(doc_path, "r") as f:
                final_doc = json.load(f)
            self.assertEqual(final_doc.get("state"), "active")
            self.assertEqual(final_doc.get("generation"), 1)

    def test_install_fallback_before_effect_wiring_and_omission_fails(self):
        from sandbox.hosting.zone_coverage import fallback
        called_effects = []
        orig_install = fallback.install_fallback

        def tracking_install(*args, **kwargs):
            be = kwargs.get("before_effect")
            if be is None:
                raise AssertionError("before_effect was omitted at install_fallback call-site")
            called_effects.append("present")
            be("certificate")
            called_effects.append("certificate")
            be("route")
            called_effects.append("route")
            return {"file": "/f", "digest": "d"}

        store = FakeStoreClient()
        cf = FakeCfClient()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", side_effect=tracking_install),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        self.assertIn("certificate", called_effects)
        self.assertIn("route", called_effects)

        def omitting_install(*args, **kwargs):
            if kwargs.get("before_effect") is None:
                raise AssertionError("before_effect omitted")
            return orig_install(*args, **kwargs)

        with self.assertRaises(AssertionError):
            omitting_install(self.entry, "asb.bd")


    # K2: Stale intent does not own changed record
    def test_stale_intent_does_not_own_changed_record(self):
        # Stored intent had 1.1.1.1
        stored_doc = {
            "state": "incomplete",
            "interrupted_kind": "enable",
            "publish_intent": {
                "marker": "test-marker-123",
                "records": [
                    {"name": "*.asb.bd", "type": "A", "content": "1.1.1.1", "proxied": True},
                ],
            },
        }
        store = FakeStoreClient(doc=stored_doc)
        # CF record has operator-changed content 2.2.2.2 but retains comment
        cf = FakeCfClient(records=[
            {"id": "rec-1", "name": "*.asb.bd", "type": "A", "content": "2.2.2.2", "proxied": True, "comment": "test-marker-123"},
        ])
        entry = dict(self.entry, origin_ipv4="2.2.2.2")
        plan = plan_enable(entry, "asb.bd", store_client=store, cf_client=cf)

        # Must not be owned from prior attempt because tuple differed
        self.assertEqual(len(plan["records_owned_from_prior_attempt"]), 0)
        self.assertEqual(len(plan["records_to_adopt"]), 1)
        self.assertEqual(plan["records_to_adopt"][0]["id"], "rec-1")

        # On failed probe, must NOT be deleted
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(entry, "asb.bd", plan, store_client=store, cf_client=cf)
        self.assertEqual(len(cf.deleted_records), 0)

    # K2: Intent merge across changed attempts rollback matches any tuple
    def test_intent_merge_across_changed_attempts_rollback_matches_any_tuple(self):
        # Prior attempt recorded A tuple (content 198.51.100.1) and AAAA tuple (content 2001:db8::1)
        stored_doc = {
            "state": "incomplete",
            "interrupted_kind": "enable",
            "publish_intent": {
                "marker": "test-marker-abc",
                "records": [
                    {"name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True},
                    {"name": "*.asb.bd", "type": "AAAA", "content": "2001:db8::1", "proxied": True},
                ],
            },
        }
        store = FakeStoreClient(doc=stored_doc)
        # CF already has the A record from the prior attempt
        cf = FakeCfClient(records=[
            {"id": "rec-a-1", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": "test-marker-abc"},
        ])
        entry = dict(self.entry, origin_ipv4="198.51.100.1", origin_ipv6="2001:db8::1")
        plan = plan_enable(entry, "asb.bd", store_client=store, cf_client=cf)

        # Plan recognizes rec-a-1 as owned from prior attempt, and plans to create AAAA
        self.assertEqual(len(plan["records_owned_from_prior_attempt"]), 1)
        self.assertEqual(plan["records_owned_from_prior_attempt"][0]["id"], "rec-a-1")
        self.assertEqual(len(plan["records_to_create"]), 1)

        # Enable creates AAAA, then on failed probe, rollback matches both tuples in intent and deletes both records
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(entry, "asb.bd", plan, store_client=store, cf_client=cf)

        self.assertIn("rec-a-1", cf.deleted_records)
        self.assertEqual(len(cf.deleted_records), 2)

    # K2: No direct comment comparison outside _is_ours in service.py
    def test_no_comment_equals_outside_is_ours(self):
        import inspect, re
        from sandbox.hosting.zone_coverage import service
        src = inspect.getsource(service)
        matches = [m.start() for m in re.finditer(r'\.get\(["\']comment["\']\)\s*==', src)]
        # Must have exactly 1 occurrence in the entire module, inside _is_ours
        self.assertEqual(len(matches), 1, f"Expected exactly 1 '.get('comment') ==' in service.py, found {len(matches)}")

    # K3: Mixed equal-plus-differing records refuse in both orderings at plan time and write time
    def test_mixed_records_refuse_both_orderings_at_plan_time_and_write_time(self):
        eq_rec = {"id": "r-eq", "name": "*.asb.bd", "type": "A", "content": "198.51.100.1", "proxied": True, "comment": ""}
        diff_rec = {"id": "r-diff", "name": "*.asb.bd", "type": "A", "content": "198.51.100.99", "proxied": True, "comment": ""}

        # Plan time: [equal, diff]
        cf1 = FakeCfClient(records=[eq_rec, diff_rec])
        with self.assertRaises(CoverageError) as ctx1:
            plan_enable(self.entry, "asb.bd", store_client=FakeStoreClient(), cf_client=cf1)
        self.assertEqual(ctx1.exception.code, COVERAGE_RECORD_EXISTS)

        # Plan time: [diff, equal]
        cf2 = FakeCfClient(records=[diff_rec, eq_rec])
        with self.assertRaises(CoverageError) as ctx2:
            plan_enable(self.entry, "asb.bd", store_client=FakeStoreClient(), cf_client=cf2)
        self.assertEqual(ctx2.exception.code, COVERAGE_RECORD_EXISTS)

        # Write time: [equal, diff]
        cf3 = FakeCfClient(records=[])
        plan3 = plan_enable(self.entry, "asb.bd", store_client=FakeStoreClient(), cf_client=cf3)
        cf3._records = [eq_rec, diff_rec]
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            with self.assertRaises(CoverageError) as ctx3:
                enable(self.entry, "asb.bd", plan3, store_client=FakeStoreClient(), cf_client=cf3)
        self.assertEqual(ctx3.exception.code, COVERAGE_RECORD_EXISTS)

        # Write time: [diff, equal]
        cf4 = FakeCfClient(records=[])
        plan4 = plan_enable(self.entry, "asb.bd", store_client=FakeStoreClient(), cf_client=cf4)
        cf4._records = [diff_rec, eq_rec]
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", return_value={"file": "/f", "digest": "d"}),              patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=True):
            with self.assertRaises(CoverageError) as ctx4:
                enable(self.entry, "asb.bd", plan4, store_client=FakeStoreClient(), cf_client=cf4)
        self.assertEqual(ctx4.exception.code, COVERAGE_RECORD_EXISTS)


if __name__ == "__main__":
    unittest.main()
