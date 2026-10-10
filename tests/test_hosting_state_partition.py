"""Spec 060 per-target controller state (T007).

One document per target; a corrupt file affects only its own target; records
round-trip byte-identically against what ``hosts.json`` holds today.
"""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.hosting.recovery.models import (  # noqa: E402
    RecoveryAction, RecoveryRequest, TargetIdentity,
)
from sandbox.hosting.recovery.repository import RecoveryRepository  # noqa: E402
from sandbox.hosting.state_partition import layout  # noqa: E402

KEY = "london/shop/production"
OTHER = "london/blog/staging"


class PartitionStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = Path(self._tmp.name) / "runtime"
        self.store = layout.PartitionStore(self.runtime)

    def test_per_file_read_write_is_private_and_keyed_by_digest(self):
        self.assertIsNone(self.store.read(KEY))
        self.store.write(KEY, {"generation": 3, "loopback_port": 18100})
        path = self.store.path_for(KEY)
        self.assertEqual(path.name, layout.digest16(KEY) + ".json")
        self.assertEqual(path.parent, self.runtime / "host-targets")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        document = json.loads(path.read_text())
        self.assertEqual(document, {"version": 2, "state_key": KEY,
                                    "record": {"generation": 3, "loopback_port": 18100}})
        self.assertEqual(self.store.read(KEY)["generation"], 3)
        self.assertIsNone(self.store.read(OTHER))
        self.assertTrue(self.store.delete(KEY))
        self.assertFalse(self.store.delete(KEY))
        self.assertIsNone(self.store.read(KEY))

    def test_corrupt_file_affects_only_its_target(self):
        self.store.write(KEY, {"generation": 1})
        self.store.write(OTHER, {"generation": 2})
        self.store.path_for(KEY).write_text("{truncated")
        with self.assertRaises(layout.TargetStateError) as caught:
            self.store.read(KEY)
        self.assertEqual(caught.exception.state_key, KEY)
        self.assertEqual(self.store.read(OTHER), {"generation": 2})
        rows = self.store.iterate()
        self.assertEqual(len(rows), 2)
        readable = [(key, record) for key, record, error in rows if error is None]
        errors = [error for _key, _record, error in rows if error is not None]
        self.assertEqual(readable, [(OTHER, {"generation": 2})])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].file, layout.digest16(KEY) + ".json")

    def test_misplaced_or_foreign_documents_are_rejected(self):
        self.store.write(KEY, {"generation": 1})
        # A document copied under another target's name is not trusted.
        os.replace(self.store.path_for(KEY), self.store.path_for(OTHER))
        with self.assertRaises(layout.TargetStateError):
            self.store.read(OTHER)
        self.assertIsNone(self.store.read(KEY))
        for document in ({"version": 1, "state_key": KEY, "record": {}},
                         {"version": 2, "state_key": KEY, "record": []}, [1]):
            with self.subTest(document=document):
                self.store.path_for(KEY).write_text(json.dumps(document))
                with self.assertRaises(layout.TargetStateError):
                    self.store.read(KEY)

    def test_symlinked_file_is_refused(self):
        self.store.write(OTHER, {"generation": 1})
        os.symlink(self.store.path_for(OTHER), self.store.path_for(KEY))
        with self.assertRaises(layout.TargetStateError):
            self.store.read(KEY)

    def test_unrelated_files_are_ignored_by_iteration(self):
        self.store.write(KEY, {"generation": 1})
        (self.store.root / "notes.txt").write_text("x")
        (self.store.root / ".tmp-abc.json").write_text("{")
        self.assertEqual([row[0] for row in self.store.iterate()], [KEY])

    def test_target_write_lock_serializes_and_times_out(self):
        with self.store.target_write_lock(KEY):
            with self.assertRaises(TimeoutError):
                with layout.PartitionStore(self.runtime).target_write_lock(
                        KEY, timeout_seconds=0.1):
                    pass
            with self.store.target_write_lock(OTHER, timeout_seconds=0.1):
                pass

    def test_conversion_record_tracks_each_remote(self):
        self.assertIsNone(self.store.remote_state("london"))
        self.assertFalse(self.store.converted("london"))
        self.store.mark("london", layout.IN_PROGRESS, step="copy_records")
        self.store.mark("london", layout.IN_PROGRESS, step="copy_records")
        entry = self.store.conversion()["remotes"]["london"]
        self.assertEqual(entry["steps_done"], ["copy_records"])
        self.assertIsNone(entry["finished_at"])
        self.store.mark("london", layout.CONVERTED, step="mark_converted")
        self.assertTrue(self.store.converted("london"))
        self.assertIsNone(self.store.remote_state("paris"))
        with self.assertRaises(layout.TargetStateError):
            self.store.mark("../x", layout.CONVERTED)
        self.store.conversion_path.write_text("{")
        with self.assertRaises(layout.TargetStateError):
            self.store.converted("london")

    def test_records_round_trip_byte_identical_with_hosts_json(self):
        """Parity: a record written by today's 054 repository survives a move."""
        legacy_root = Path(self._tmp.name) / "legacy"
        repository = RecoveryRepository(legacy_root / "hosts.json", legacy_root / "locks")
        target = TargetIdentity("london", "shop", "production")
        request = RecoveryRequest(RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
                                  "apply-1", target, 0)
        with repository.target_lock(target.key):
            state = repository.load()
            state["hosts"].setdefault(target.key, {}).update(
                {"loopback_port": 18123, "domain": "shop.example.test",
                 "images": {"schema_version": 1, "planes": {"requested": None}}})
            repository.begin(state, target.key, request)
        legacy = repository.load()["hosts"][target.key]
        self.assertIn("active_operation", legacy)
        self.store.write(target.key, legacy)
        moved = self.store.read(target.key)
        self.assertEqual(moved, legacy)
        self.assertEqual(json.dumps(moved, sort_keys=True), json.dumps(legacy, sort_keys=True))


class CompositeStateTests(unittest.TestCase):
    """``load_host_state``/``save_host_state`` and the 054 repository route a
    converted remote's targets to their own files (FR-025)."""

    def setUp(self):
        from sandbox.core import _hosting
        self.hosting = _hosting
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.runtime = Path(self._tmp.name) / "runtime"
        self.runtime.mkdir()
        self.path = self.runtime / "hosts.json"
        self.store = layout.PartitionStore(self.runtime)
        self.hosting.save_host_state({"version": 1, "hosts": {
            KEY: {"loopback_port": 18101}, OTHER: {"loopback_port": 18102},
            "paris/shop/production": {"loopback_port": 18103}}}, self.path)

    def convert_london(self):
        legacy = json.loads(self.path.read_text())["hosts"]
        for key in (KEY, OTHER):
            self.store.write(key, legacy[key])
        self.store.mark("london", layout.CONVERTED)

    def test_without_conversion_nothing_changes(self):
        state = self.hosting.load_host_state(self.path)
        state["hosts"][KEY]["generation"] = 1
        self.hosting.save_host_state(state, self.path)
        self.assertFalse(self.store.root.exists())
        self.assertEqual(json.loads(self.path.read_text())["hosts"][KEY]["generation"], 1)

    def test_converted_remote_reads_and_writes_its_own_files(self):
        self.convert_london()
        state = self.hosting.load_host_state(self.path)
        self.assertEqual(set(state["hosts"]), {KEY, OTHER, "paris/shop/production"})
        state["hosts"][KEY]["generation"] = 5
        state["hosts"]["paris/shop/production"]["generation"] = 6
        before = self.store.path_for(OTHER).stat().st_mtime_ns
        self.hosting.save_host_state(state, self.path)
        self.assertEqual(self.store.read(KEY)["generation"], 5)
        self.assertEqual(self.store.path_for(OTHER).stat().st_mtime_ns, before)
        legacy = json.loads(self.path.read_text())["hosts"]
        self.assertEqual(set(legacy), {"paris/shop/production"})
        self.assertEqual(legacy["paris/shop/production"]["generation"], 6)
        state = self.hosting.load_host_state(self.path)
        del state["hosts"][OTHER]
        self.hosting.save_host_state(state, self.path)
        self.assertIsNone(self.store.read(OTHER))

    def test_concurrent_views_of_different_targets_do_not_lose_updates(self):
        self.convert_london()
        first = self.hosting.load_host_state(self.path)
        second = self.hosting.load_host_state(self.path)
        first["hosts"][KEY]["generation"] = 1
        second["hosts"][OTHER]["generation"] = 2
        self.hosting.save_host_state(first, self.path)
        self.hosting.save_host_state(second, self.path)
        self.assertEqual(self.store.read(KEY)["generation"], 1)
        self.assertEqual(self.store.read(OTHER)["generation"], 2)

    def test_corrupt_target_is_isolated_and_never_overwritten(self):
        self.convert_london()
        self.store.path_for(KEY).write_text("{")
        state = self.hosting.load_host_state(self.path)
        self.assertNotIn(KEY, state["hosts"])
        state["hosts"][OTHER]["generation"] = 3
        self.hosting.save_host_state(state, self.path)
        self.assertEqual(self.store.read(OTHER)["generation"], 3)
        state = self.hosting.load_host_state(self.path)
        state["hosts"][KEY] = {"generation": 0}
        with self.assertRaises(self.hosting.HostingError):
            self.hosting.save_host_state(state, self.path)
        self.assertEqual(self.store.path_for(KEY).read_text(), "{")
        with self.assertRaises(self.hosting.HostingError):
            self.hosting.allocate_loopback_port(self.hosting.load_host_state(self.path),
                                                "london/new/production")

    def test_recovery_repository_follows_the_partition(self):
        self.convert_london()
        repository = RecoveryRepository(self.path, self.runtime / "locks")
        target = TargetIdentity("london", "shop", "production")
        request = RecoveryRequest(RecoveryAction.OBSERVE_RECONCILE, "recover-1", "a" * 32,
                                  "apply-1", target, 0)
        with repository.target_lock(target.key):
            state = repository.load()
            repository.begin(state, target.key, request)
        self.assertIn("active_operation", self.store.read(KEY))
        self.assertNotIn(KEY, json.loads(self.path.read_text())["hosts"])
        self.store.path_for(KEY).write_text("{")
        with self.assertRaises(ValueError):
            repository.begin(repository.load(), target.key, request)


if __name__ == "__main__":
    unittest.main()
