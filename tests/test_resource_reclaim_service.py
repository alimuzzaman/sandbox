"""Tiered reclamation: planning, execution, manifest, resume, idempotency.

The probe tests run the real shipped program in a subprocess against a
temporary SANDBOX_HOME, so the host-side protections are exercised as shipped
rather than re-implemented in a mock.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from sandbox.resources.context import PlanStore
from sandbox.resources.models import StorageTarget
from sandbox.resources.reclaim_service import ReclaimService
from sandbox.resources.remote import LocalProbeAdapter


DAY = 86400
TARGET = StorageTarget("remote", "fixture", "a" * 24)


def entry(name, **overrides):
    base = {
        "name": name,
        "path": f"/deploy/{name}",
        "size_bytes": 1024,
        "size_state": "measured",
        "mtime": time.time() - 11 * DAY,
        "is_workspace": "-workspace-" in name,
        "is_symlink": False,
        "containers": [],
        "registry": False,
        "active_job": False,
        "indexed": False,
        "hosted": False,
        "protections": [],
    }
    base.update(overrides)
    return base


class FakeProvider:
    """Records what the service asked the host to do; changes nothing."""

    def __init__(self, block=None, capacity=None, outcomes=None):
        self.block = block if block is not None else {
            "deployment_root": "/deploy",
            "runtime_root": "/runtime",
            "entries": [entry("a-workspace-1"), entry("live-workspace-2",
                                                      mtime=time.time())],
            "volumes": [{"name": "lenzora-postgres-data", "size_bytes": 99,
                         "mounted_running": False}],
            "scratch": [],
            "leases": {},
            "hosted_sites": [],
            "index_names": [],
            "workspace_ids": {},
            "status": "complete",
            "truncated": False,
            "unmeasured_count": 0,
        }
        self.capacity = capacity or {
            "total_bytes": 100, "used_bytes": 90, "available_bytes": 10,
            "reserved_bytes": 0,
        }
        self.outcomes = outcomes
        self.calls = []
        self.leases = {}

    def target(self):
        return TARGET

    def inventory(self, *, budget_seconds, directory_cache):
        self.calls.append(("inventory", budget_seconds, directory_cache))
        return {"capacity": self.capacity, "reclaim": self.block}

    def reclaim(self, candidates, *, run_id, trigger="manual",
                workspace_ids=None, budget_seconds=900):
        self.calls.append(("reclaim", run_id, trigger, list(candidates)))
        outcomes = self.outcomes if self.outcomes is not None else [
            {"seq": item["seq"], "locator": item["locator"],
             "status": "removed", "reason": "removed", "bytes": item["bytes"],
             "elevated": False, "verified_absent": True}
            for item in candidates
        ]
        return {
            "ok": True, "run_id": run_id,
            "manifest_path": f"/runtime/resources/deletions/{run_id}.jsonl",
            "outcomes": outcomes,
            "reconciled": {"registry_removed": 0, "index_removed": 0,
                           "index_pending": 0, "leases_removed": 0,
                           "status": "complete"},
            "capacity_before": self.capacity,
            "capacity_after": self.capacity,
            "budget_exhausted": False,
        }

    def lease(self, op, *, name=None, expires_at=None):
        self.calls.append(("lease", op, name, expires_at))
        if op == "release":
            self.leases[name] = {"name": name, "released": True}
        elif op == "set":
            self.leases[name] = {"name": name, "expires_at": expires_at,
                                 "released": False}
        return {"ok": True, "op": op, "leases": dict(self.leases)}


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = PlanStore(self.root / "plans")

    def service(self, provider):
        return ReclaimService(provider, self.store, target=TARGET)


class TestPlanning(ServiceCase):
    def test_plan_has_no_side_effects_on_the_host(self):
        provider = FakeProvider()
        payload = self.service(provider).plan("safe")
        self.assertTrue(payload["ok"])
        self.assertEqual(
            [call[0] for call in provider.calls], ["inventory"])

    def test_plan_names_path_size_mtime_class_and_reason(self):
        payload = self.service(FakeProvider()).plan("safe")
        candidate = payload["data"]["candidates"][0]
        for key in ("locator", "bytes", "modified_at", "class", "tier",
                    "reason"):
            self.assertIn(key, candidate)
        self.assertEqual(candidate["reason"], "orphan_workspace")

    def test_plan_lists_what_it_skipped_and_why(self):
        payload = self.service(FakeProvider()).plan("safe")
        reasons = {item["reason"] for item in payload["data"]["skipped"]}
        self.assertIn("volume_not_workspace_scoped", reasons)
        self.assertIn("recent_activity", reasons)

    def test_plan_reports_every_tier_total(self):
        payload = self.service(FakeProvider()).plan("safe")
        self.assertEqual(
            set(payload["data"]["tier_totals"]), {"safe", "tmp", "all"})

    def test_plan_is_stored_and_reloadable_by_id(self):
        service = self.service(FakeProvider())
        plan_id = service.plan("safe")["data"]["plan_id"]
        stored = self.store.load(plan_id)
        self.assertEqual(stored.scope, "safe")
        self.assertTrue(stored.metadata["candidates"])

    def test_unknown_tier_is_refused(self):
        payload = self.service(FakeProvider()).plan("everything")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_tier")

    def test_missing_host_evidence_is_reported_not_guessed(self):
        provider = FakeProvider(block=None)
        provider.block = None
        payload = self.service(provider).plan("safe")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"],
                         "reclaim_inventory_unavailable")

    def test_partial_inventory_is_surfaced_in_the_plan(self):
        provider = FakeProvider()
        provider.block["truncated"] = True
        provider.block["unmeasured_count"] = 7
        payload = self.service(provider).plan("safe")
        self.assertTrue(payload["data"]["truncated"])
        self.assertEqual(payload["data"]["unmeasured_count"], 7)


class TestExecution(ServiceCase):
    def test_cleanup_refuses_without_confirmation(self):
        payload = self.service(FakeProvider()).cleanup(tier="safe")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "confirmation_required")

    def test_cleanup_requires_a_tier_or_a_plan(self):
        payload = self.service(FakeProvider()).cleanup(confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_tier")

    def test_cleanup_executes_the_reviewed_candidate_set(self):
        provider = FakeProvider()
        service = self.service(provider)
        plan_id = service.plan("safe")["data"]["plan_id"]
        payload = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")
        sent = next(call for call in provider.calls if call[0] == "reclaim")[3]
        self.assertEqual([item["locator"] for item in sent],
                         ["/deploy/a-workspace-1"])
        self.assertIn("expected_mtime", sent[0])

    def test_remote_plan_cleanup_resolves_authoritative_target_before_begin(self):
        fallback = StorageTarget("remote", "fixture", "b" * 24)

        class RemoteLike(FakeProvider):
            def target(self):
                return fallback

            def authoritative_target(self):
                return TARGET

        provider = RemoteLike()
        service = ReclaimService(provider, self.store)
        plan_id = service.plan("safe")["data"]["plan_id"]
        payload = ReclaimService(provider, self.store).cleanup(
            plan_id=plan_id, confirm=True,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")

    def test_partial_removal_is_never_counted_as_reclaimed(self):
        provider = FakeProvider(outcomes=[{
            "seq": 1, "locator": "/deploy/a-workspace-1", "status": "failed",
            "reason": "partial_removal_detected", "bytes": 0,
            "elevated": True, "verified_absent": False,
        }])
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["data"]["observed_reclaimed_bytes"], 0)
        self.assertEqual(payload["data"]["outcomes"][0]["status"], "failed")

    def test_a_second_run_of_the_same_tier_is_a_no_op(self):
        provider = FakeProvider(outcomes=[{
            "seq": 1, "locator": "/deploy/a-workspace-1",
            "status": "already_absent", "reason": "already_absent",
            "bytes": 0, "elevated": False, "verified_absent": True,
        }])
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["observed_reclaimed_bytes"], 0)

    def test_an_interrupted_plan_is_resumable(self):
        provider = FakeProvider()
        service = self.service(provider)
        plan_id = service.plan("safe")["data"]["plan_id"]
        self.store.begin(plan_id, TARGET)          # simulate a killed run
        payload = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["resumed"])

    def test_a_completed_plan_cannot_be_replayed(self):
        provider = FakeProvider()
        service = self.service(provider)
        plan_id = service.plan("safe")["data"]["plan_id"]
        service.cleanup(plan_id=plan_id, confirm=True)
        again = service.cleanup(plan_id=plan_id, confirm=True)
        self.assertFalse(again["ok"])
        self.assertEqual(again["error"]["code"], "plan_already_used")

    def test_a_refused_host_run_is_indeterminate_not_success(self):
        class Refusing(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                return {"ok": False, "reason": "manifest_unavailable"}

        payload = self.service(Refusing()).cleanup(tier="safe", confirm=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "reclaim_refused")

    def test_an_exhausted_budget_is_reported_as_indeterminate(self):
        class Bounded(FakeProvider):
            def reclaim(self, candidates, **kwargs):
                payload = FakeProvider.reclaim(self, candidates, **kwargs)
                payload["budget_exhausted"] = True
                return payload

        payload = self.service(Bounded()).cleanup(tier="safe", confirm=True)
        self.assertEqual(payload["status"], "indeterminate")
        self.assertTrue(payload["data"]["budget_exhausted"])

    def test_run_receipt_is_recorded(self):
        provider = FakeProvider()
        payload = self.service(provider).cleanup(tier="safe", confirm=True)
        receipts = list((self.root / "plans").glob("*.run.json"))
        self.assertEqual(len(receipts), 1)
        record = json.loads(receipts[0].read_text())
        self.assertEqual(record["run_id"], payload["data"]["run_id"])


class TestRetention(ServiceCase):
    def test_release_marks_the_workspace(self):
        provider = FakeProvider()
        payload = self.service(provider).release("a-workspace-1")
        self.assertTrue(payload["ok"])
        self.assertTrue(provider.leases["a-workspace-1"]["released"])

    def test_release_refuses_a_path_bearing_name(self):
        provider = FakeProvider()
        payload = self.service(provider).release("../etc")
        self.assertFalse(payload["ok"])
        self.assertEqual(provider.calls, [])

    def test_ttl_sets_an_expiry(self):
        provider = FakeProvider()
        payload = self.service(provider).set_ttl("a-workspace-1", "14d")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["ttl_seconds"], 14 * DAY)
        self.assertIsNotNone(provider.leases["a-workspace-1"]["expires_at"])

    def test_ttl_refuses_an_invalid_duration(self):
        provider = FakeProvider()
        payload = self.service(provider).set_ttl("a-workspace-1", "soon")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_duration")

    def test_reap_dry_run_changes_nothing(self):
        provider = FakeProvider()
        payload = self.service(provider).reap(dry_run=True)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["dry_run"])
        self.assertEqual([call[0] for call in provider.calls], ["inventory"])

    def test_reap_without_confirmation_is_refused(self):
        payload = self.service(FakeProvider()).reap(dry_run=False)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "confirmation_required")

    def test_reap_excludes_disposable_scratch(self):
        provider = FakeProvider()
        provider.block["scratch"] = [{
            "name": ".drive-volume-fallbacks-1",
            "path": "/runtime/.drive-volume-fallbacks-1",
            "size_bytes": 512, "mtime": time.time() - 30 * DAY,
        }]
        payload = self.service(provider).reap(dry_run=True)
        kinds = {item["kind"] for item in payload["data"]["candidates"]}
        self.assertNotIn("runtime", kinds)


class ProbeCase(unittest.TestCase):
    """Exercise the shipped probe program itself, in a subprocess."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        (self.home / "runtime").mkdir(parents=True)
        (self.home / "deploy-src").mkdir(parents=True)
        self.probe = LocalProbeAdapter(home=str(self.home))

    def workspace(self, name):
        path = self.home / "deploy-src" / name
        (path / "node_modules").mkdir(parents=True)
        (path / "node_modules" / "a.txt").write_text("x" * 32)
        return path

    def manifest_records(self, run_id):
        path = (self.home / "runtime" / "resources" / "deletions"
                / f"{run_id}.jsonl")
        return [json.loads(line) for line in
                path.read_text().splitlines() if line.strip()]

    def test_lease_round_trip(self):
        listing = self.probe.lease("list")
        self.assertTrue(listing["ok"])
        self.assertEqual(listing["leases"], {})
        self.probe.lease("set", name="x-workspace-1",
                         expires_at="2030-01-01T00:00:00Z")
        self.probe.lease("release", name="x-workspace-1")
        listing = self.probe.lease("list")
        self.assertTrue(listing["leases"]["x-workspace-1"]["released"])
        stored = (self.home / "runtime" / "resources" / "leases"
                  / "x-workspace-1.json")
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)

    def test_lease_refuses_a_path_bearing_name(self):
        response = self.probe.lease("release", name="../../etc/passwd")
        self.assertFalse(response["ok"])
        self.assertEqual(response["reason"], "invalid_lease_name")

    def test_reclaim_writes_the_intent_before_the_outcome(self):
        path = self.workspace("gone-workspace-1")
        run_id = "b" * 32
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }], run_id=run_id, budget_seconds=60)
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["outcomes"][0]["status"], "removed")
        self.assertFalse(path.exists())
        records = self.manifest_records(run_id)
        phases = [item["phase"] for item in records]
        self.assertEqual(phases, ["run_start", "intent", "outcome"])
        self.assertEqual(records[1]["bytes"], 32)
        self.assertEqual(records[1]["class"], "ORPHAN")
        self.assertTrue(records[2]["verified_absent"])

    def test_reclaim_is_idempotent_and_resumable(self):
        path = self.workspace("gone-workspace-2")
        candidate = {
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }
        first = self.probe.reclaim([candidate], run_id="c" * 32,
                                   budget_seconds=60)
        second = self.probe.reclaim([candidate], run_id="c" * 32,
                                    budget_seconds=60)
        self.assertEqual(first["outcomes"][0]["status"], "removed")
        self.assertEqual(second["outcomes"][0]["status"], "already_absent")
        # The manifest is append-only: the resumed run adds to it.
        self.assertGreater(len(self.manifest_records("c" * 32)), 3)

    def test_reclaim_refuses_a_path_outside_the_managed_roots(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(outside), "bytes": 1,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }], run_id="d" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"], "path_escape")
        self.assertTrue(outside.exists())

    def test_reclaim_refuses_the_hosts_subtree(self):
        hosts = self.home / "deploy-src" / "hosts" / "site"
        hosts.mkdir(parents=True)
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(hosts), "bytes": 1,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
        }], run_id="e" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"], "hosted_site")
        self.assertTrue(hosts.exists())

    def test_reclaim_refuses_the_managed_root_itself(self):
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree",
            "locator": str(self.home / "deploy-src"), "bytes": 1,
            "class": "BASE", "tier": "all", "reason": "one_shot_base_expired",
        }], run_id="f" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"], "managed_root")
        self.assertTrue((self.home / "deploy-src").exists())

    def test_reclaim_refuses_a_volume_that_is_not_workspace_scoped(self):
        for name in ("lenzora-postgres-data", "wordpress-uploads",
                     "lenzora-storage",
                     "sandbox-amarsonar-bangla-public_wordpress-db",
                     "sandbox-lenzora_app-node-modules"):
            response = self.probe.reclaim([{
                "seq": 1, "kind": "volume", "locator": name, "bytes": 1,
                "class": "VOLUME", "tier": "safe",
                "reason": "workspace_scoped_volume",
            }], run_id="0" * 32, budget_seconds=60)
            self.assertEqual(response["outcomes"][0]["reason"],
                             "volume_not_workspace_scoped", name)

    def test_reclaim_skips_a_candidate_that_changed_since_planning(self):
        path = self.workspace("moving-workspace-1")
        response = self.probe.reclaim([{
            "seq": 1, "kind": "worktree", "locator": str(path), "bytes": 32,
            "class": "ORPHAN", "tier": "safe", "reason": "orphan_workspace",
            "expected_mtime": 1.0,
        }], run_id="9" * 32, budget_seconds=60)
        self.assertEqual(response["outcomes"][0]["reason"],
                         "candidate_modified_since_plan")
        self.assertTrue(path.exists())

    def test_reclaim_refuses_a_run_without_a_valid_identifier(self):
        response = self.probe.reclaim([], run_id="nope", budget_seconds=30)
        self.assertFalse(response["ok"])
        self.assertEqual(response["reason"], "invalid_run_id")

    def test_observe_emits_the_reclaim_inventory(self):
        self.workspace("seen-workspace-1")
        payload = self.probe.observe_reclaim(budget_seconds=25)
        block = payload.get("reclaim")
        self.assertIsInstance(block, dict)
        names = {item["name"] for item in block["entries"]}
        self.assertIn("seen-workspace-1", names)
        record = next(item for item in block["entries"]
                      if item["name"] == "seen-workspace-1")
        self.assertTrue(record["is_workspace"])
        self.assertIsNotNone(record["mtime"])


if __name__ == "__main__":
    unittest.main()
