"""One scheduled routine run on a seeded host (spec 057, SC-002/003/006/007).

Inventory is seeded (no Docker needed); removal goes through the real shipped
probe in a subprocess against a temporary SANDBOX_HOME, so the guard, the
manifest-before-removal order and the host-side protections run as shipped.
A stub ``docker`` on PATH records volume removals instead of touching the
machine's engine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

from sandbox.resources.cleanup_routine import run as routine_run
from sandbox.resources.cleanup_routine.store import RoutineStore
from sandbox.resources.host_guard import try_reclaim_guard
from sandbox.resources.models import StorageTarget
from sandbox.resources.plans import PlanStore
from sandbox.resources.reclaim_service import ReclaimService
from sandbox.resources.remote import LocalProbeAdapter

DAY = 86400
REV = "c" * 24
TARGET = StorageTarget("local", "local", "d" * 24)
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$(dirname "$0")/docker.log"
exit 0
"""


class SeededHost:
    """Reclaim provider: seeded inventory, real probe for removal."""

    def __init__(self, block, probe):
        self.block = block
        self.probe = probe
        self.inventory_calls = 0
        self.reclaim_override = None

    def target(self):
        return TARGET

    def inventory(self, *, budget_seconds, directory_cache):
        self.inventory_calls += 1
        return {
            "capacity": {"total_bytes": 100, "used_bytes": 50,
                         "available_bytes": 50, "reserved_bytes": 0},
            "reclaim": self.block,
        }

    def reclaim(self, candidates, **kwargs):
        if self.reclaim_override is not None:
            return self.reclaim_override(candidates, **kwargs)
        return self.probe.reclaim(candidates, **kwargs)

    def lease(self, op, **kwargs):
        return {"ok": True, "leases": {}}


class RunCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.runtime = self.home / "runtime"
        self.deploy = self.home / "deploy-src"
        self.runtime.mkdir()
        self.deploy.mkdir()
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(stat.S_IRWXU)
        self.docker_log = bin_dir / "docker.log"
        path_patch = patch.dict(os.environ, {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        })
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.store = RoutineStore(self.runtime / "resources" / "cleanup-routine")
        self.store.write_config({
            "schema": 1, "enabled": True, "cadence": "daily",
            "timeout": "30min", "randomized_delay": "5min",
            "exclusions": ["lenzora*"], "enabled_revision": REV,
            "enabled_at": "2026-10-01T00:00:00Z", "disabled_at": None,
            "unit": "sandbox-cleanup-routine",
        })
        self.paths = {}
        old = time.time() - 11 * DAY
        for name in ("old-workspace-1", "stopped-workspace-2",
                     "lenzora-workspace-3", "live-workspace-4", "hosted-site",
                     "keep-workspace-5"):
            path = self.deploy / name
            (path / "node_modules").mkdir(parents=True)
            (path / "node_modules" / "a.txt").write_text("x" * 32)
            for item in (path / "node_modules" / "a.txt", path / "node_modules", path):
                os.utime(item, (old, old))
            self.paths[name] = path
        self.block = {
            "deployment_root": str(self.deploy),
            "runtime_root": str(self.runtime),
            "entries": [
                self.entry("old-workspace-1", old),
                self.entry("stopped-workspace-2", old, containers=[
                    {"id": "c2", "name": "c2", "running": False}]),
                self.entry("lenzora-workspace-3", old),
                self.entry("live-workspace-4", old, containers=[
                    {"id": "c4", "name": "c4", "running": True}]),
                self.entry("hosted-site", old, hosted=True),
                self.entry("keep-workspace-5", old),
            ],
            "volumes": [
                {"name": "sandbox-old-workspace-1_node-modules",
                 "size_bytes": 64, "mounted_running": False},
                {"name": "sandbox-lenzora-workspace-3_node-modules",
                 "size_bytes": 64, "mounted_running": False},
                {"name": "sandbox-live-workspace-4_node-modules",
                 "size_bytes": 64, "mounted_running": True},
                {"name": "lenzora-postgres-data", "size_bytes": 99,
                 "mounted_running": False},
            ],
            "scratch": [], "leases": {}, "hosted_sites": [], "index_names": [],
            "workspace_ids": {}, "status": "complete", "truncated": False,
            "unmeasured_count": 0, "engine_complete": True,
        }
        self.host = SeededHost(self.block, LocalProbeAdapter(home=str(self.home)))
        self.host_config = {"resources": {"reclaim_exclude": ["keep-*"]}}

    def entry(self, name, mtime, **overrides):
        value = {
            "name": name, "path": str(self.deploy / name), "size_bytes": 32,
            "size_state": "measured", "mtime": mtime,
            "is_workspace": "-workspace-" in name, "is_symlink": False,
            "containers": [], "registry": False, "active_job": False,
            "indexed": False, "hosted": False, "protections": [],
        }
        value.update(overrides)
        return value

    def run_once(self, **overrides):
        kwargs = {
            "runtime": self.runtime,
            "store": self.store,
            "service_factory": lambda: ReclaimService(
                self.host, PlanStore(self.home / "plans"), target=TARGET),
            "host_config": self.host_config,
            "revision": REV,
        }
        kwargs.update(overrides)
        return routine_run.run_routine(**kwargs)

    def manifest(self, run_id):
        path = self.runtime / "resources" / "deletions" / f"{run_id}.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line]


class ReclaimingRun(RunCase):
    def test_reclaims_only_eligible_items_with_manifest_first(self):
        payload = self.run_once()
        record = payload["run"]
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(record["outcome"], "reclaimed")
        self.assertEqual(record["runtime_revision"], REV)
        self.assertEqual(record["manifest"], f"deletions/{record['run_id']}.jsonl")
        self.assertEqual(record["removed"], 2)
        self.assertEqual(record["bytes_reclaimed"], 96)

        records = self.manifest(record["run_id"])
        self.assertEqual(records[0]["phase"], "run_start")
        self.assertEqual(records[0]["trigger"], "scheduled_routine")
        intents = [item for item in records if item["phase"] == "intent"]
        self.assertEqual(
            {item["path"] for item in intents},
            {str(self.paths["old-workspace-1"]), "sandbox-old-workspace-1_node-modules"},
        )
        for item in records:
            if item["phase"] == "outcome":
                position = records.index(item)
                self.assertTrue(any(
                    earlier["phase"] == "intent" and earlier["seq"] == item["seq"]
                    for earlier in records[:position]))

        forbidden = ("stopped-workspace-2", "lenzora-workspace-3",
                     "live-workspace-4", "hosted-site", "keep-workspace-5",
                     "lenzora-postgres-data")
        rendered = json.dumps(intents)
        for name in forbidden:
            self.assertNotIn(name, rendered)
        for name in forbidden[:-1]:
            self.assertTrue(self.paths[name].exists(), name)
        self.assertFalse(self.paths["old-workspace-1"].exists())
        removals = self.docker_log.read_text().splitlines()
        self.assertEqual(removals, ["volume rm sandbox-old-workspace-1_node-modules"])
        self.assertGreaterEqual(record["skipped_reasons"].get("excluded_by_request", 0), 3)

    def test_nothing_eligible_is_nothing_to_do_without_manifest(self):
        self.block["entries"] = [item for item in self.block["entries"]
                                 if item["name"] != "old-workspace-1"]
        self.block["volumes"] = [item for item in self.block["volumes"]
                                 if "old-workspace-1" not in item["name"]]
        record = self.run_once()["run"]
        self.assertEqual(record["outcome"], "nothing_to_do")
        self.assertIsNone(record["manifest"])
        self.assertFalse((self.runtime / "resources" / "deletions").exists())

    def test_incomplete_inventory_is_refused_and_removes_nothing(self):
        self.block["status"] = "partial"
        payload = self.run_once()
        record = payload["run"]
        self.assertFalse(payload["ok"])
        self.assertEqual(record["outcome"], "refused")
        self.assertEqual(record["reason"], "inventory_incomplete")
        self.assertIsNone(record["manifest"])
        self.assertTrue(self.paths["old-workspace-1"].exists())
        self.assertFalse(self.docker_log.exists())

    def test_guard_held_is_skipped_busy_before_inventory(self):
        with try_reclaim_guard(runtime=self.runtime) as acquired:
            self.assertTrue(acquired)
            record = self.run_once()["run"]
        self.assertEqual(record["outcome"], "skipped_busy")
        self.assertEqual(record["reason"], "host_reclaim_busy")
        self.assertIsNone(record["manifest"])
        self.assertEqual(self.host.inventory_calls, 0)
        self.assertTrue(self.paths["old-workspace-1"].exists())

    def test_apply_transaction_is_skipped_busy(self):
        with patch.object(routine_run, "apply_transaction_active", return_value=True):
            record = self.run_once()["run"]
        self.assertEqual(record["outcome"], "skipped_busy")
        self.assertEqual(record["reason"], "apply_transaction_active")
        self.assertEqual(self.host.inventory_calls, 0)

    def test_busy_reported_by_the_probe_is_skipped_busy(self):
        self.host.reclaim_override = lambda candidates, **kwargs: {
            "ok": False, "reason": "host_reclaim_busy", "detail": "guard"}
        record = self.run_once()["run"]
        self.assertEqual(record["outcome"], "skipped_busy")
        self.assertEqual(record["reason"], "host_reclaim_busy")
        self.assertIsNone(record["manifest"])

    def test_bound_reached_is_timed_out_with_pre_bound_removals(self):
        def bounded(candidates, *, run_id, **kwargs):
            first = candidates[0]
            return {
                "ok": True, "run_id": run_id, "manifest_path": "x",
                "outcomes": [{"seq": first["seq"], "locator": first["locator"],
                              "status": "removed", "reason": "removed",
                              "bytes": first["bytes"]}],
                "budget_exhausted": True, "reconciled": {},
                "capacity_before": None, "capacity_after": None,
            }

        self.host.reclaim_override = bounded
        record = self.run_once()["run"]
        self.assertEqual(record["outcome"], "timed_out")
        self.assertEqual(record["removed"], 1)
        self.assertEqual(record["manifest"], f"deletions/{record['run_id']}.jsonl")

    def test_no_time_left_after_planning_is_timed_out_without_removal(self):
        ticks = iter([0.0, 0.0, 10_000.0, 10_000.0, 10_000.0])
        record = self.run_once(monotonic=lambda: next(ticks, 10_000.0))["run"]
        self.assertEqual(record["outcome"], "timed_out")
        self.assertIsNone(record["manifest"])
        self.assertTrue(self.paths["old-workspace-1"].exists())

    def test_host_exclusions_merge_and_operator_config_is_not_read(self):
        seen = {}

        class Recording(ReclaimService):
            def plan(self, tier, **kwargs):
                seen.update(kwargs, tier=tier)
                return super().plan(tier, **kwargs)

        self.run_once(service_factory=lambda: Recording(
            self.host, PlanStore(self.home / "plans"), target=TARGET))
        self.assertEqual(seen["tier"], "safe")
        self.assertEqual(tuple(seen["exclude_names"]), ("lenzora*", "keep-*"))
        self.assertEqual(tuple(seen["exclude_kinds"]), ("runtime",))

    def test_disabled_routine_does_not_run(self):
        config = self.store.read_config()
        self.store.write_config({**config, "enabled": False})
        payload = self.run_once()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "routine_disabled")
        self.assertEqual(self.store.runs(), [])

    def test_stale_open_record_is_finalized_by_the_next_run(self):
        self.store.write_run({
            "schema": 1, "run_id": "e" * 32,
            "started_at": "2026-10-07T00:00:00Z", "ended_at": None,
            "outcome": "running", "reason": None, "bytes_reclaimed": 0,
            "removed": 0, "skipped": 0, "skipped_reasons": {},
            "runtime_revision": REV, "manifest": None, "timeout_seconds": 1800,
        })
        self.run_once()
        stale = next(item for item in self.store.runs() if item["run_id"] == "e" * 32)
        self.assertEqual(stale["outcome"], "timed_out")

    def test_history_never_exceeds_30(self):
        for index in range(31):
            self.store.write_run({
                "schema": 1, "run_id": f"{index:032x}",
                "started_at": (NOW - timedelta(days=40 - index)).isoformat().replace("+00:00", "Z"),
                "ended_at": None, "outcome": "nothing_to_do", "reason": None,
                "bytes_reclaimed": 0, "removed": 0, "skipped": 0,
                "skipped_reasons": {}, "runtime_revision": REV, "manifest": None,
            })
        self.run_once()
        self.assertEqual(len(self.store.runs()), 30)


if __name__ == "__main__":
    unittest.main()
