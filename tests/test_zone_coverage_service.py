from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sandbox.hosting.front_door import FrontDoorError, ZONE_COVERAGE
from sandbox.hosting.zone_coverage import (
    COVERAGE_CONFLICT,
    COVERAGE_OPERATION_ACTIVE,
    COVERAGE_PLAN_STALE,
    COVERAGE_RECORD_EXISTS,
    COVERAGE_SSL_NOT_STRICT,
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

    def read(self, zone: str):
        if self.doc is None:
            return {"ok": True, "action": "read", "state": "absent", "document": None}
        return {"ok": True, "action": "read", "state": self.doc.get("state"), "document": self.doc}

    def begin(self, zone: str, kind: str, controller: str, *, expected_generation=None, expected_dependents_revision=None, resume=False, zone_id=""):
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
            "dependents": {},
            "dependents_revision": 0,
            "history": [],
        }
        return {"ok": True, "action": "begin", "operation_id": op_id, "document": self.doc}

    def phase(self, zone: str, operation_id: str, phase: str, *, updates=None):
        if updates:
            self.doc.update(updates)
        self.doc["operation"]["phase"] = phase
        return {"ok": True, "action": "phase", "document": self.doc}

    def finish(self, zone: str, operation_id: str, state: str, *, interrupted_kind=None, raise_generation=False):
        self.doc["state"] = state
        self.doc["operation"] = None
        self.doc["interrupted_kind"] = interrupted_kind
        if raise_generation:
            self.doc["generation"] = self.doc.get("generation", 0) + 1
        return {"ok": True, "action": "finish", "document": self.doc}


class FakeCfClient:
    def __init__(self, zone_id="z-test", ssl_mode="strict", records=None):
        self.zone_id = zone_id
        self.ssl_mode = ssl_mode
        self._records = records or []

    def zone(self, name: str):
        return {"id": self.zone_id, "name": name}

    def _request(self, method: str, path: str):
        if "/settings/ssl" in path:
            return {"success": True, "result": {"value": self.ssl_mode}}
        return {"success": True, "result": []}

    def records(self, zone_id: str, hostname: str):
        return [r for r in self._records if r.get("name") == hostname]

    def upsert_address(self, zone_id: str, hostname: str, address: str, proxied: bool = True):
        rec = {"id": f"rec-{len(self._records)+1}", "name": hostname, "type": "A", "content": address, "proxied": proxied}
        self._records.append(rec)
        return rec


class TestZoneCoverageService(unittest.TestCase):
    def setUp(self):
        self.entry = {
            "name": "test-remote",
            "front_door": "nginx",
            "origin_ipv4": "198.51.100.1",
        }

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
             patch("sandbox.hosting.zone_coverage.fallback.run_origin_probe", return_value={"status": 404}):
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


if __name__ == "__main__":
    unittest.main()
