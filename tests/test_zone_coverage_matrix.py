from __future__ import annotations

import fcntl
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sandbox.hosting.zone_coverage import (
    COVERAGE_FALLBACK_UNVERIFIED,
    COVERAGE_PLAN_STALE,
    COVERAGE_RECORD_EXISTS,
    COVERAGE_STORE_UNAVAILABLE,
    CoverageError,
)
from sandbox.hosting.zone_coverage.client import ZoneCoverageClient
from sandbox.hosting.zone_coverage.models import normalize_zone
from sandbox.hosting.zone_coverage.program import PROGRAM, LEASE_SECONDS, MAX_DOC_BYTES
from sandbox.hosting.zone_coverage.service import (
    compute_plan_digest,
    enable,
    plan_enable,
)


class Crash(BaseException):
    """Simulated unhandled crash during operation."""
    pass


class StatefulFakeProvider:
    """Stateful fake provider per spec 070 H1.

    Holds records including comment, implements zone, records, zone_records,
    create_address, delete_record, current_ssl_mode.
    """

    def __init__(self, zone_id: str = "z-test", zone_name: str = "asb.bd", ssl_mode: str = "strict", records: list[dict] | None = None):
        self.zone_id = zone_id
        self.zone_name = zone_name
        self.ssl_mode = ssl_mode
        self._records = list(records or [])
        self.calls: list[tuple] = []
        self._next_id = 1

    def zone(self, name: str) -> dict:
        self.calls.append(("zone", name))
        return {"id": self.zone_id, "name": name}

    def zones(self, name: str | None = None) -> list[dict]:
        self.calls.append(("zones", name))
        return [{"id": self.zone_id, "name": name or self.zone_name}]

    def current_ssl_mode(self, zone_id: str) -> str:
        self.calls.append(("current_ssl_mode", zone_id))
        return self.ssl_mode

    def _request(self, method: str, path: str) -> dict:
        self.calls.append(("_request", method, path))
        if "/settings/ssl" in path:
            return {"success": True, "result": {"value": self.ssl_mode}}
        return {"success": True, "result": []}

    def records(self, zone_id: str, hostname: str | None = None, name: str | None = None) -> list[dict]:
        target = hostname or name
        self.calls.append(("records", zone_id, target))
        if target:
            return [r for r in self._records if r.get("name") == target]
        return list(self._records)

    def zone_records(self, zone_id: str) -> list[dict]:
        self.calls.append(("zone_records", zone_id))
        return list(self._records)

    def create_address(self, zone_id: str, hostname: str, address: str, proxied: bool = True, comment: str = "") -> dict:
        self.calls.append(("create_address", zone_id, hostname, address, proxied, comment))
        rec_type = "AAAA" if ":" in address else "A"
        rec = {
            "id": f"rec-{self._next_id}",
            "name": hostname,
            "type": rec_type,
            "content": address,
            "proxied": bool(proxied),
            "comment": comment,
            "ttl": 1 if proxied else 60,
        }
        self._next_id += 1
        self._records.append(rec)
        return rec

    def delete_record(self, zone_id: str, record_id: str) -> dict:
        self.calls.append(("delete_record", zone_id, record_id))
        self._records = [r for r in self._records if r.get("id") != record_id]
        return {"id": record_id}


class TestZoneCoverageMatrix(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="sb-test-matrix-")
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

        self.entry = {
            "name": "test-remote",
            "front_door": "nginx",
            "origin_ipv4": "198.51.100.1",
            "origin_ipv6": "2001:db8::1",
        }

        # Mock front_door.nginx.probe and fallback.install_fallback
        self._probe_patcher = patch(
            "sandbox.hosting.front_door.nginx.probe",
            return_value={"config_valid": True, "version": [1, 26, 0]},
        )
        self._probe_patcher.start()
        self.addCleanup(self._probe_patcher.stop)

        self._install_patcher = patch(
            "sandbox.hosting.zone_coverage.fallback.install_fallback",
            return_value={"file": "/etc/nginx/conf.d/sandbox-coverage-asb.bd.conf"},
        )
        self._install_patcher.start()
        self.addCleanup(self._install_patcher.stop)

        self._public_probe_patcher = patch(
            "sandbox.hosting.zone_coverage.service.public_probe",
            return_value=True,
        )
        self._public_probe_patcher.start()
        self.addCleanup(self._public_probe_patcher.stop)

    def _make_store_client(self) -> ZoneCoverageClient:
        def real_ssh_run(entry, command, timeout=15):
            env = dict(os.environ, SANDBOX_HOME=self.td, HOME=self.td)
            return subprocess.run(command, shell=True, env=env, capture_output=True, text=True, timeout=timeout)
        return ZoneCoverageClient(self.entry, ssh_run=real_ssh_run)

    def _expire_lease(self, zone: str = "asb.bd"):
        doc_path = os.path.join(self.td, "runtime", "zone-coverage", "zones", f"{zone}.json")
        with open(doc_path, "r") as f:
            data = json.load(f)
        if "operation" in data and data["operation"]:
            data["operation"]["lease_expires_at"] = time.time() - 10
            with open(doc_path, "w") as f:
                json.dump(data, f)

    def _read_doc(self, zone: str = "asb.bd") -> dict | None:
        doc_path = os.path.join(self.td, "runtime", "zone-coverage", "zones", f"{zone}.json")
        if not os.path.exists(doc_path):
            return None
        with open(doc_path, "r") as f:
            return json.load(f)

    # 1. Dual stack happy path
    def test_01_dual_stack_happy_path(self):
        store = self._make_store_client()
        cf = StatefulFakeProvider()

        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(len(plan["records_to_create"]), 2)
        types = {r["type"] for r in plan["records_to_create"]}
        self.assertEqual(types, {"A", "AAAA"})

        intent_checked = False
        orig_create = cf.create_address

        def intercept_create(*args, **kwargs):
            nonlocal intent_checked
            if not intent_checked:
                doc = self._read_doc("asb.bd")
                self.assertIsNotNone(doc.get("publish_intent"))
                pi = doc["publish_intent"]
                self.assertTrue(isinstance(pi, dict))
                self.assertTrue(pi["marker"].startswith("sandbox-zone-coverage "))
                self.assertEqual(len(pi["records"]), 2)
                intent_checked = True
            return orig_create(*args, **kwargs)

        with patch.object(cf, "create_address", side_effect=intercept_create):
            res = enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        self.assertEqual(res["state"], "active")
        self.assertTrue(intent_checked)
        final_doc = self._read_doc("asb.bd")
        self.assertIsNone(final_doc.get("publish_intent"))
        self.assertEqual(final_doc.get("state"), "active")
        self.assertEqual(final_doc.get("generation"), 1)
        self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 2)

    # 2. Crash matrix
    def test_02_crash_matrix(self):
        for point in range(1, 8):
            with self.subTest(crash_point=point):
                shutil.rmtree(self.td, ignore_errors=True)
                os.makedirs(self.td, exist_ok=True)
                store = self._make_store_client()
                cf = StatefulFakeProvider()

                plan1 = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
                self.assertEqual(len(plan1["records_to_create"]), 2)

                orig_phase = store.phase
                orig_create = cf.create_address
                phase_counts = {}
                create_count = 0

                def counting_phase(zone, op_id, ph, updates=None):
                    res = orig_phase(zone, op_id, ph, updates=updates)
                    phase_counts[ph] = phase_counts.get(ph, 0) + 1
                    if point == 1 and ph == "fallback_install":
                        raise Crash("crash after fallback_install")
                    if point == 2 and ph == "fallback_verified":
                        raise Crash("crash after fallback_verified")
                    if point == 3 and ph == "dns_publish":
                        raise Crash("crash after dns_publish")
                    if point == 4 and ph == "publish_intent":
                        raise Crash("crash after publish_intent")
                    if point == 7 and ph == "dns_published":
                        raise Crash("crash after dns_published")
                    return res

                def counting_create(*args, **kwargs):
                    nonlocal create_count
                    rec = orig_create(*args, **kwargs)
                    create_count += 1
                    if point == 5 and create_count == 1:
                        raise Crash("crash after 1st write")
                    if point == 6 and create_count == 2:
                        raise Crash("crash after 2nd write")
                    return rec

                with patch.object(store, "phase", side_effect=counting_phase), \
                     patch.object(cf, "create_address", side_effect=counting_create):
                    with self.assertRaises(Crash):
                        enable(self.entry, "asb.bd", plan1, store_client=store, cf_client=cf)

                # Expire the lease
                self._expire_lease("asb.bd")

                # Re-plan independently twice
                plan2 = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
                plan2_indep = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
                self.assertTrue(plan2["resumable"])
                # Assert plan digest matches between two independently generated plans
                self.assertEqual(plan2["plan_digest"], plan2_indep["plan_digest"])
                self.assertEqual(compute_plan_digest(plan2), plan2["plan_digest"])

                # Count create_address calls during resume
                creates_before_resume = len([c for c in cf.calls if c[0] == "create_address"])

                # Resume enable
                res2 = enable(self.entry, "asb.bd", plan2, store_client=store, cf_client=cf, resume=True)
                self.assertEqual(res2["state"], "active")

                # Assert exactly one record per address
                records = cf.records("z-test", "*.asb.bd")
                self.assertEqual(len(records), 2)
                self.assertEqual({r["type"] for r in records}, {"A", "AAAA"})

                # Assert no duplicate create calls for already created records
                creates_after_resume = len([c for c in cf.calls if c[0] == "create_address"])
                new_creates = creates_after_resume - creates_before_resume
                self.assertEqual(new_creates, 2 - len(plan2["records_owned_from_prior_attempt"]))

    # 3. Crash after each provider write, resume, probe fails
    def test_03_crash_after_each_provider_write_resume_probe_fails(self):
        for crash_writes in (1, 2):
            with self.subTest(crash_writes=crash_writes):
                shutil.rmtree(self.td, ignore_errors=True)
                os.makedirs(self.td, exist_ok=True)
                store = self._make_store_client()
                cf = StatefulFakeProvider()

                plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
                orig_create = cf.create_address
                count = 0

                def fail_at_count(*args, **kwargs):
                    nonlocal count
                    rec = orig_create(*args, **kwargs)
                    count += 1
                    if count == crash_writes:
                        raise Crash(f"crash after {crash_writes} writes")
                    return rec

                with patch.object(cf, "create_address", side_effect=fail_at_count):
                    with self.assertRaises(Crash):
                        enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

                # Expire lease
                self._expire_lease("asb.bd")

                # Re-plan and resume, with probe failing
                plan_resume = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
                with patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
                    with self.assertRaises(CoverageError) as ctx:
                        enable(self.entry, "asb.bd", plan_resume, store_client=store, cf_client=cf, resume=True)
                    self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

                # Our records are gone from CF, state is fallback_only
                self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 0)
                doc = self._read_doc("asb.bd")
                self.assertEqual(doc.get("state"), "fallback_only")

    # 4. Rollback keeps: changed proxied, changed content, changed comment
    def test_04_rollback_keeps_modified_records(self):
        tamper_cases = [
            ("proxied", False),
            ("content", "9.9.9.9"),
            ("comment", "different-comment"),
        ]
        for field, new_val in tamper_cases:
            with self.subTest(field=field, new_val=new_val):
                shutil.rmtree(self.td, ignore_errors=True)
                os.makedirs(self.td, exist_ok=True)
                store = self._make_store_client()
                cf = StatefulFakeProvider()
                plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

                def tamper_and_fail_probe(zone):
                    for r in cf._records:
                        r[field] = new_val
                    return False

                with patch("sandbox.hosting.zone_coverage.service.public_probe", side_effect=tamper_and_fail_probe):
                    with self.assertRaises(CoverageError):
                        enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

                # Record was KEPT on CF and not deleted
                recs = cf.records("z-test", "*.asb.bd")
                self.assertGreaterEqual(len(recs), 1)
                self.assertEqual(recs[0][field], new_val)

                # State is incomplete with unknown_effects
                doc = self._read_doc("asb.bd")
                self.assertEqual(doc.get("state"), "incomplete")
                effects = doc.get("unknown_effects") or []
                self.assertTrue(any(e.get("reason") == "record_modified_out_of_band" for e in effects))

    # 5. Rollback never deletes adopted or unmarked records (before and after crash)
    def test_05_rollback_never_deletes_adopted_or_unmarked(self):
        # Case A: Adopted before planning
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        adopted_rec = cf.create_address("z-test", "*.asb.bd", "198.51.100.1", proxied=True, comment="incumbent-site")

        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertEqual(len(plan["records_to_adopt"]), 1)
        self.assertEqual(len(plan["records_to_create"]), 1)

        with patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        # Adopted record is still present
        self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 1)
        self.assertEqual(cf.records("z-test", "*.asb.bd")[0]["id"], adopted_rec["id"])

        # Case B: Equal without marker inserted after planning
        shutil.rmtree(self.td, ignore_errors=True)
        os.makedirs(self.td, exist_ok=True)
        store = self._make_store_client()
        cf = StatefulFakeProvider()

        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        unmarked = cf.create_address("z-test", "*.asb.bd", "198.51.100.1", proxied=True, comment="")

        with patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        # Unmarked record is still present, never deleted
        recs = [r for r in cf.records("z-test", "*.asb.bd") if r["id"] == unmarked["id"]]
        self.assertEqual(len(recs), 1)

    # 6. Second write raises after taking effect
    def test_06_second_write_raises_after_taking_effect(self):
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        orig_create = cf.create_address
        calls = 0

        def succeed_then_raise(*args, **kwargs):
            nonlocal calls
            rec = orig_create(*args, **kwargs)
            calls += 1
            if calls == 2:
                raise RuntimeError("network dropped after write completed on provider")
            return rec

        with patch.object(cf, "create_address", side_effect=succeed_then_raise):
            with self.assertRaises(CoverageError):
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        doc = self._read_doc("asb.bd")
        self.assertEqual(doc.get("state"), "incomplete")
        self.assertIsNotNone(doc.get("publish_intent"))
        effects = doc.get("unknown_effects") or []
        effect_names = [e.get("name") for e in effects]
        self.assertIn("*.asb.bd", effect_names)

        self._expire_lease("asb.bd")
        plan_retry = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        creates_before = len([c for c in cf.calls if c[0] == "create_address"])
        res = enable(self.entry, "asb.bd", plan_retry, store_client=store, cf_client=cf)
        self.assertEqual(res["state"], "active")
        creates_after = len([c for c in cf.calls if c[0] == "create_address"])
        self.assertEqual(creates_before, creates_after, "Retry must not call create_address when records are present")
        self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 2)

    # 7. Error paths report store refusals and raising
    def test_07_error_paths_store_refused_and_raising(self):
        # A: Install failure + finish refused
        shutil.rmtree(self.td, ignore_errors=True)
        os.makedirs(self.td, exist_ok=True)
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", side_effect=RuntimeError("install boom")), \
             patch.object(store, "finish", return_value={"ok": True, "refused": "coverage_operation_active"}):
            with self.assertRaises(RuntimeError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertIn("store was not updated: refused finish (coverage_operation_active)", str(ctx.exception))

        # B: Install failure + finish raising
        shutil.rmtree(self.td, ignore_errors=True)
        os.makedirs(self.td, exist_ok=True)
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        with patch("sandbox.hosting.zone_coverage.fallback.install_fallback", side_effect=RuntimeError("install boom")), \
             patch.object(store, "finish", side_effect=RuntimeError("ssh connection died")):
            with self.assertRaises(RuntimeError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertIn("store was not updated: finish raised RuntimeError", str(ctx.exception))

        # C: Write failure + phase refused
        shutil.rmtree(self.td, ignore_errors=True)
        os.makedirs(self.td, exist_ok=True)
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        orig_phase = store.phase
        def fail_phase_on_dns_fail(zone, op_id, ph, updates=None):
            if ph == "dns_publish_failed":
                return {"ok": True, "refused": "coverage_operation_active"}
            return orig_phase(zone, op_id, ph, updates=updates)
        with (
            patch.object(cf, "create_address", side_effect=RuntimeError("cf write boom")),
            patch.object(store, "phase", side_effect=fail_phase_on_dns_fail),
        ):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertIn("store was not updated: refused phase (coverage_operation_active)", str(ctx.exception))

        # D: Probe failure with rollback + finish refused
        shutil.rmtree(self.td, ignore_errors=True)
        os.makedirs(self.td, exist_ok=True)
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        with patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False), \
             patch.object(store, "finish", return_value={"ok": True, "refused": "coverage_operation_active"}):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertIn("store was not updated: refused finish (coverage_operation_active)", str(ctx.exception))

        # In none of these does result say active or clean!
        doc = self._read_doc("asb.bd")
        if doc:
            self.assertNotEqual(doc.get("state"), "active")

    # 8. Takeover: old operation phase and finish refuse
    def test_08_takeover_old_operation_refused(self):
        store = self._make_store_client()
        b1 = store.begin("asb.bd", "enable", "controller-1")
        op1 = b1["operation_id"]

        self._expire_lease("asb.bd")

        b2 = store.begin("asb.bd", "enable", "controller-2", resume=True)
        op2 = b2["operation_id"]
        self.assertNotEqual(op1, op2)

        p1 = store.phase("asb.bd", op1, "dns_publish")
        self.assertEqual(p1.get("refused"), "coverage_operation_active")

        f1 = store.finish("asb.bd", op1, "active")
        self.assertEqual(f1.get("refused"), "coverage_operation_active")

    # 9. Probe failure with delete failure lists in unknown_effects; store finish refusal stated
    def test_09_probe_failure_delete_failed_and_store_finish_refused(self):
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        with patch("sandbox.hosting.zone_coverage.service.public_probe", return_value=False), \
             patch.object(cf, "delete_record", side_effect=RuntimeError("delete 500 error")):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertEqual(ctx.exception.code, COVERAGE_FALLBACK_UNVERIFIED)

        doc = self._read_doc("asb.bd")
        self.assertEqual(doc.get("state"), "incomplete")
        effects = doc.get("unknown_effects") or []
        self.assertTrue(any("delete_failed" in e.get("reason", "") for e in effects))

    # 10. Transition matrix through real program
    def test_10_transition_matrix(self):
        store = self._make_store_client()
        zones_dir = os.path.join(self.td, "runtime", "zone-coverage", "zones")
        os.makedirs(zones_dir, exist_ok=True)
        doc_path = os.path.join(zones_dir, "matrix.bd.json")

        all_states = ["absent", "retired", "fallback_only", "disabled", "active", "incomplete_enable", "incomplete_disable"]
        all_kinds = ["enable", "disable", "repair", "remove-fallback"]

        valid_finishes = {
            "enable": {"active", "fallback_only", "incomplete"},
            "disable": {"disabled", "incomplete"},
            "repair": {"active", "incomplete"},
            "remove-fallback": {"retired", "incomplete"},
        }
        invalid_finishes = {
            "enable": "disabled",
            "disable": "active",
            "repair": "disabled",
            "remove-fallback": "active",
        }

        for state in all_states:
            for kind in all_kinds:
                with self.subTest(state=state, kind=kind):
                    if state == "absent":
                        if os.path.exists(doc_path):
                            os.remove(doc_path)
                    else:
                        st = "incomplete" if state.startswith("incomplete") else state
                        interrupted = "enable" if state == "incomplete_enable" else ("disable" if state == "incomplete_disable" else None)
                        doc = {
                            "schema_version": 1,
                            "zone": "matrix.bd",
                            "state": st,
                            "generation": 1,
                            "dependents_revision": 0,
                            "dependents": {},
                            "history": [],
                            "interrupted_kind": interrupted,
                        }
                        with open(doc_path, "w") as f:
                            json.dump(doc, f)

                    res = store.begin("matrix.bd", kind, "tester")
                    refused = res.get("refused")

                    should_allow = False
                    if state == "absent":
                        should_allow = (kind == "enable")
                    elif state == "retired":
                        should_allow = (kind == "enable")
                    elif state in ("fallback_only", "disabled"):
                        should_allow = (kind in ("enable", "remove-fallback"))
                    elif state == "active":
                        should_allow = (kind in ("repair", "disable"))
                    elif state == "incomplete_enable":
                        should_allow = (kind in ("enable", "repair"))
                    elif state == "incomplete_disable":
                        should_allow = (kind == "disable")

                    if not should_allow:
                        self.assertEqual(refused, "coverage_kind_mismatch")
                    else:
                        self.assertIsNone(refused)
                        op_id = res["operation_id"]

                        # Phase advance
                        ph_res = store.phase("matrix.bd", op_id, "preflight")
                        self.assertTrue(ph_res.get("ok"))

                        # Finish transition validation: invalid finish target refused
                        bad_target = invalid_finishes[kind]
                        f_bad = store.finish("matrix.bd", op_id, bad_target)
                        self.assertEqual(f_bad.get("refused"), "coverage_kind_mismatch")

                        # Finish transition validation: valid finish target succeeds
                        good_target = next(iter(valid_finishes[kind]))
                        f_good = store.finish("matrix.bd", op_id, good_target)
                        self.assertTrue(f_good.get("ok"))
                        self.assertEqual(f_good["document"]["state"], good_target)

    # 11. Store reads own document in every state after finish
    def test_11_store_reads_own_doc_after_each_finish(self):
        store = self._make_store_client()
        zones_dir = os.path.join(self.td, "runtime", "zone-coverage", "zones")
        os.makedirs(zones_dir, exist_ok=True)
        doc_path = os.path.join(zones_dir, "asb.bd.json")

        cases = [
            ("enable", "fallback_only"),
            ("enable", "active"),
            ("enable", "incomplete"),
            ("disable", "disabled"),
            ("disable", "incomplete"),
            ("remove-fallback", "retired"),
        ]

        for kind, finish_state in cases:
            with self.subTest(kind=kind, finish_state=finish_state):
                op_state = "enabling" if kind == "enable" else ("disabling" if kind == "disable" else "disabled")
                doc = {
                    "schema_version": 1,
                    "zone": "asb.bd",
                    "state": op_state,
                    "generation": 1,
                    "dependents_revision": 0,
                    "dependents": {},
                    "history": [],
                    "operation": {
                        "id": f"op-test-{kind}-{finish_state}",
                        "kind": kind,
                        "phase": "begin",
                        "controller": "tester",
                        "started_at": int(time.time()),
                        "lease_expires_at": int(time.time()) + 900,
                    },
                    "publish_intent": {"marker": "m", "records": []},
                }
                with open(doc_path, "w") as f:
                    json.dump(doc, f)

                f = store.finish("asb.bd", f"op-test-{kind}-{finish_state}", finish_state, raise_generation=True)
                self.assertTrue(f.get("ok"))

                r = store.read("asb.bd")
                self.assertTrue(r.get("ok"))
                read_doc = r["document"]
                self.assertEqual(read_doc["state"], finish_state)
                self.assertIsNone(read_doc.get("operation"))
                if finish_state != "incomplete":
                    self.assertIsNone(read_doc.get("publish_intent"))
                self.assertEqual(read_doc["history"][-1]["result"], finish_state)
                self.assertEqual(read_doc["history"][-1]["kind"], kind)

    # 12. Corrupt, schema 999 and wrong-zone documents stay byte-identical
    def test_12_corrupt_schema999_wrong_zone_byte_identical(self):
        zones_dir = os.path.join(self.td, "runtime", "zone-coverage", "zones")
        os.makedirs(zones_dir, exist_ok=True)
        store = self._make_store_client()

        cases = [
            ("corrupt.bd", b"{this is not valid json}"),
            ("schema999.bd", json.dumps({"schema_version": 999, "zone": "schema999.bd"}).encode()),
            ("wrongzone.bd", json.dumps({"schema_version": 1, "zone": "other.bd", "state": "active", "generation": 0, "dependents_revision": 0, "dependents": {}, "history": []}).encode()),
        ]

        mutating_actions = [
            lambda z: store.begin(z, "enable", "tester"),
            lambda z: store.phase(z, "op-1", "dns_publish"),
            lambda z: store.finish(z, "op-1", "active"),
            lambda z: store.dependent_bind(z, "routes/owner/app1", "app1.bd", "bound"),
            lambda z: store.dependent_release(z, "routes/owner/app1"),
        ]

        for z, raw_bytes in cases:
            p = os.path.join(zones_dir, f"{z}.json")
            with open(p, "wb") as f:
                f.write(raw_bytes)

            for act in mutating_actions:
                act(z)
                with open(p, "rb") as f:
                    cur_bytes = f.read()
                self.assertEqual(cur_bytes, raw_bytes, f"Action modified corrupt document {z}")

    # 13. Stale generation; stale dependents revision; plan stale on confirm; dependent-bind
    def test_13_stale_generation_and_dependents(self):
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        # Stale generation in begin
        b_bad_gen = store.begin("asb.bd", "enable", "tester", expected_generation=999)
        self.assertEqual(b_bad_gen.get("refused"), "coverage_generation_stale")

        # Stale dependents revision in begin
        b_bad_dep = store.begin("asb.bd", "enable", "tester", expected_dependents_revision=999)
        self.assertEqual(b_bad_dep.get("refused"), "coverage_dependents_stale")

        # Dependent bound between plan and confirm
        enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)

        # Now active, bind a dependent
        bind_res = store.dependent_bind("asb.bd", "routes/owner/app1", "app1.asb.bd", "bound")
        self.assertTrue(bind_res.get("ok"))

        # Old plan now stale because generation and dependents revision increased
        with self.assertRaises(CoverageError) as ctx:
            enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
        self.assertEqual(ctx.exception.code, COVERAGE_PLAN_STALE)

    # 14. Two real processes contending for the lock and lock wait timeout
    def test_14_two_processes_contending_and_lock_wait(self):
        store = self._make_store_client()
        # Initialize an active doc
        b = store.begin("asb.bd", "enable", "tester")
        store.finish("asb.bd", b["operation_id"], "active")

        results = []

        def worker(name):
            try:
                res = store.dependent_bind("asb.bd", f"routes/owner/{name}", f"{name}.asb.bd", "bound")
                results.append(res)
            except Exception as e:
                results.append(e)

        th1 = threading.Thread(target=worker, args=("worker1",))
        th2 = threading.Thread(target=worker, args=("worker2",))
        th1.start()
        th2.start()
        th1.join(timeout=10)
        th2.join(timeout=10)

        self.assertEqual(len(results), 2)
        doc = self._read_doc("asb.bd")
        self.assertIsNotNone(doc)
        self.assertEqual(doc.get("state"), "active")
        self.assertIn("routes/owner/worker1", doc.get("dependents", {}))
        self.assertIn("routes/owner/worker2", doc.get("dependents", {}))

        # Held lock refuses within wait bound
        lock_path = os.path.join(self.td, "runtime", "zone-coverage", "coverage.lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            t0 = time.time()
            res = store.read("asb.bd")
            elapsed = time.time() - t0
            self.assertEqual(res.get("refused"), "coverage_store_unavailable")
            self.assertEqual(res.get("reason"), "lock_wait_timeout")
            self.assertGreaterEqual(elapsed, 9.5)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    # 15. Size bounds and hostile zone/state_key values
    def test_15_size_bounds_and_hostile_keys(self):
        store = self._make_store_client()

        hostile_zones = ["../x", "a/b", "$(id)", "a b", "", "x" * 200]
        for hz in hostile_zones:
            with self.assertRaises(CoverageError):
                store.read(hz)
            with self.assertRaises(CoverageError):
                store.begin(hz, "enable", "tester")

        hostile_keys = ["../x", "a/b", "$(id)", "", "k" * 300]
        for hk in hostile_keys:
            with self.assertRaises(CoverageError):
                store.dependent_bind("asb.bd", hk, "h.asb.bd", "bound")

    # 16. Plan writes nothing to store or provider
    def test_16_plan_writes_nothing(self):
        store = self._make_store_client()
        cf = StatefulFakeProvider()

        plan1 = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        self.assertIsNone(self._read_doc("asb.bd"))
        self.assertEqual(len(cf.calls), 3)  # zone, current_ssl_mode, records
        self.assertFalse(any(c[0] in ("create_address", "delete_record") for c in cf.calls))

        b = store.begin("asb.bd", "enable", "tester")
        store.finish("asb.bd", b["operation_id"], "active")
        doc_before = self._read_doc("asb.bd")
        cf.calls.clear()

        plan2 = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)
        doc_after = self._read_doc("asb.bd")
        self.assertEqual(doc_before, doc_after)
        self.assertFalse(any(c[0] in ("create_address", "delete_record") for c in cf.calls))

    # 17. Provider exception with SECRET-MARKER never leaks
    def test_17_secret_marker_never_leaks(self):
        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        secret = "SECRET-MARKER-998877"
        with patch.object(cf, "create_address", side_effect=RuntimeError(f"internal {secret} details")):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            err_str = str(ctx.exception)
            self.assertNotIn(secret, err_str)

        doc = self._read_doc("asb.bd")
        doc_str = json.dumps(doc)
        self.assertNotIn(secret, doc_str)

    # 18. Lease arithmetic: probe duration vs lease seconds
    def test_18_lease_arithmetic(self):
        max_probe_seconds = 10 * 10 + 9 * 15
        self.assertLess(max_probe_seconds, LEASE_SECONDS, f"Max probe {max_probe_seconds}s must be less than LEASE_SECONDS {LEASE_SECONDS}s")

        store = self._make_store_client()
        cf = StatefulFakeProvider()
        plan = plan_enable(self.entry, "asb.bd", store_client=store, cf_client=cf)

        orig_phase = store.phase

        def refuse_edge_verified(zone, op_id, ph, updates=None):
            if ph == "edge_verified":
                return {"ok": True, "refused": "coverage_operation_active"}
            return orig_phase(zone, op_id, ph, updates=updates)

        with patch.object(store, "phase", side_effect=refuse_edge_verified):
            with self.assertRaises(CoverageError) as ctx:
                enable(self.entry, "asb.bd", plan, store_client=store, cf_client=cf)
            self.assertIn("coverage_operation_active", str(ctx.exception))

        self.assertEqual(len(cf.records("z-test", "*.asb.bd")), 2)
        doc = self._read_doc("asb.bd")
        self.assertIsNotNone(doc.get("publish_intent"))


if __name__ == "__main__":
    unittest.main()
