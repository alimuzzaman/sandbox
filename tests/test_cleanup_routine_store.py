"""Routine config and run-record store (spec 057, FR-016/FR-023)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from sandbox.resources.cleanup_routine.store import RoutineStore

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def run_id(index):
    return f"{index:032x}"


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.runtime = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.runtime, ignore_errors=True)
        self.root = self.runtime / "resources" / "cleanup-routine"
        self.store = RoutineStore(self.root)

    def config(self, **overrides):
        value = {
            "schema": 1, "enabled": True, "cadence": "daily",
            "timeout": "30min", "randomized_delay": "5min",
            "exclusions": [], "enabled_revision": "a" * 24,
            "enabled_at": iso(NOW), "disabled_at": None,
            "unit": "sandbox-cleanup-routine",
        }
        value.update(overrides)
        return value


class ConfigTests(StoreCase):
    def test_absent_config_reads_none(self):
        self.assertIsNone(self.store.read_config())

    def test_config_is_owner_only_under_a_private_directory(self):
        self.store.write_config(self.config())
        path = self.root / "routine.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(self.store.read_config()["cadence"], "daily")

    def test_re_enable_replaces_atomically(self):
        self.store.write_config(self.config())
        self.store.write_config(self.config(cadence="weekly"))
        self.assertEqual(self.store.read_config()["cadence"], "weekly")
        leftovers = [item.name for item in self.root.iterdir()
                     if item.name != "routine.json" and item.name != "runs"]
        self.assertEqual(leftovers, [])

    def test_symlinked_config_is_refused(self):
        self.root.mkdir(parents=True, mode=0o700)
        elsewhere = self.runtime / "elsewhere.json"
        elsewhere.write_text(json.dumps(self.config()))
        (self.root / "routine.json").symlink_to(elsewhere)
        self.assertIsNone(self.store.read_config())
        with self.assertRaises(OSError):
            self.store.write_config(self.config())


class RunRecordTests(StoreCase):
    def record(self, index, started, **overrides):
        value = {
            "schema": 1, "run_id": run_id(index), "started_at": iso(started),
            "ended_at": iso(started + timedelta(minutes=1)),
            "outcome": "nothing_to_do", "reason": None, "bytes_reclaimed": 0,
            "removed": 0, "skipped": 0, "skipped_reasons": {},
            "runtime_revision": "a" * 24, "manifest": None,
        }
        value.update(overrides)
        return value

    def test_records_are_newest_first_and_owner_only(self):
        self.store.write_run(self.record(1, NOW - timedelta(days=2)))
        self.store.write_run(self.record(2, NOW - timedelta(days=1)))
        runs = self.store.runs()
        self.assertEqual([item["run_id"] for item in runs], [run_id(2), run_id(1)])
        path = self.root / "runs" / f"{run_id(1)}.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_keeps_the_newest_30(self):
        for index in range(1, 32):
            self.store.write_run(self.record(index, NOW + timedelta(minutes=index)))
        runs = self.store.runs()
        self.assertEqual(len(runs), 30)
        self.assertNotIn(run_id(1), {item["run_id"] for item in runs})
        self.assertEqual(len(list((self.root / "runs").glob("*.json"))), 30)

    def test_history_limit(self):
        for index in range(1, 6):
            self.store.write_run(self.record(index, NOW + timedelta(minutes=index)))
        self.assertEqual([item["run_id"] for item in self.store.runs(limit=2)],
                         [run_id(5), run_id(4)])

    def test_rejects_an_invalid_run_id(self):
        with self.assertRaises(ValueError):
            self.store.write_run(self.record(1, NOW, run_id="../escape"))

    def test_corrupt_records_are_ignored(self):
        self.store.write_run(self.record(1, NOW))
        (self.root / "runs" / f"{run_id(2)}.json").write_text("{not json")
        self.assertEqual([item["run_id"] for item in self.store.runs()], [run_id(1)])


class StaleFinalizationTests(StoreCase):
    def open_record(self, index, started):
        record = {
            "schema": 1, "run_id": run_id(index), "started_at": iso(started),
            "ended_at": None, "outcome": "running", "reason": None,
            "bytes_reclaimed": 0, "removed": 0, "skipped": 0,
            "skipped_reasons": {}, "runtime_revision": "a" * 24,
            "manifest": None, "timeout_seconds": 1800,
        }
        self.store.write_run(record)
        return record

    def manifest(self, index, lines):
        directory = self.runtime / "resources" / "deletions"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{run_id(index)}.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in lines))

    def test_open_record_past_bound_plus_60s_is_timed_out_from_manifest(self):
        self.open_record(1, NOW - timedelta(minutes=31, seconds=1))
        self.manifest(1, [
            {"phase": "run_start", "trigger": "scheduled_routine"},
            {"phase": "intent", "seq": 1, "bytes": 100},
            {"phase": "outcome", "seq": 1, "status": "removed", "bytes": 100},
            {"phase": "intent", "seq": 2, "bytes": 50},
            {"phase": "outcome", "seq": 2, "status": "skipped",
             "reason": "candidate_modified_since_plan", "bytes": None},
            {"phase": "intent", "seq": 3, "bytes": 70},
        ])
        finalized = self.store.finalize_stale(now=NOW)
        self.assertEqual([item["run_id"] for item in finalized], [run_id(1)])
        record = self.store.runs()[0]
        self.assertEqual(record["outcome"], "timed_out")
        self.assertEqual(record["reason"], "run_bound_exceeded")
        self.assertEqual(record["removed"], 1)
        self.assertEqual(record["bytes_reclaimed"], 100)
        self.assertEqual(record["skipped"], 1)
        self.assertEqual(record["ended_at"], iso(NOW))
        self.assertEqual(record["manifest"], f"deletions/{run_id(1)}.jsonl")

    def test_open_record_within_bound_is_left_open(self):
        self.open_record(1, NOW - timedelta(minutes=30))
        self.assertEqual(self.store.finalize_stale(now=NOW), [])
        self.assertEqual(self.store.runs()[0]["outcome"], "running")

    def test_stale_record_without_manifest_has_no_reference(self):
        self.open_record(1, NOW - timedelta(hours=2))
        self.store.finalize_stale(now=NOW)
        record = self.store.runs()[0]
        self.assertEqual(record["outcome"], "timed_out")
        self.assertIsNone(record["manifest"])
        self.assertEqual(record["removed"], 0)


if __name__ == "__main__":
    unittest.main()
