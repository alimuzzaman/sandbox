"""State store for controller auto-update (spec 072, contracts/state-file.md)."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.controller_update import state  # noqa: E402
from sandbox.controller_update.models import SkipReason  # noqa: E402

A = "a" * 40
B = "b" * 40


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "runtime" / "controller-update" / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_is_empty_state(self):
        loaded = state.load(self.path)
        self.assertEqual(loaded.hints, ())
        self.assertEqual(loaded.refusals, ())

    def test_record_hint_round_trips_and_replaces_per_remote(self):
        self.assertTrue(state.record_hint("demo", A, "install", path=self.path, now=10.0))
        self.assertTrue(state.record_hint("other", B, "probe", path=self.path, now=11.0))
        self.assertTrue(state.record_hint("demo", B, "probe", path=self.path, now=12.0))
        hints = {hint.remote: hint for hint in state.load(self.path).hints}
        self.assertEqual(set(hints), {"demo", "other"})
        self.assertEqual(hints["demo"].revision, B)
        self.assertEqual(hints["demo"].source, "probe")
        self.assertEqual(hints["demo"].recorded_at, 12.0)

    def test_file_and_directory_are_private(self):
        state.record_hint("demo", A, "install", path=self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_invalid_inputs_are_not_recorded(self):
        self.assertFalse(state.record_hint("demo", "a" * 39, "install", path=self.path))
        self.assertFalse(state.record_hint("demo", "A" * 40, "install", path=self.path))
        self.assertFalse(state.record_hint("Bad Name", A, "install", path=self.path))
        self.assertFalse(state.record_hint("demo", A, "server", path=self.path))
        self.assertEqual(state.load(self.path).hints, ())

    def test_malformed_file_and_entries_are_ignored(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(state.load(self.path).hints, ())
        self.path.write_text(json.dumps({
            "schema_version": 1,
            "hints": {
                "good": {"revision": A, "source": "probe", "recorded_at": 1.0},
                "short": {"revision": "abc", "source": "probe", "recorded_at": 1.0},
                "Bad Name": {"revision": A, "source": "probe", "recorded_at": 1.0},
                "nosource": {"revision": A, "recorded_at": 1.0},
                "list": [A],
            },
            "refusals": [{"revision": A, "upstream": "origin/main", "reason": "bogus", "until": 9e12},
                         "junk"],
        }), encoding="utf-8")
        loaded = state.load(self.path)
        self.assertEqual([hint.remote for hint in loaded.hints], ["good"])
        self.assertEqual(loaded.refusals, ())
        # A writer replaces the malformed parts instead of failing.
        self.assertTrue(state.record_hint("next", B, "install", path=self.path))
        self.assertEqual({h.remote for h in state.load(self.path).hints}, {"good", "next"})

    def test_wrong_schema_version_is_empty_state(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"schema_version": 2, "hints": {
            "demo": {"revision": A, "source": "probe", "recorded_at": 1.0}}}), encoding="utf-8")
        self.assertEqual(state.load(self.path).hints, ())

    def test_refusal_matches_revision_and_upstream_until_expiry(self):
        self.assertTrue(state.record_refusal(A, "origin/main", SkipReason.NOT_ON_UPSTREAM, 100.0,
                                             path=self.path, now=1000.0))
        loaded = state.load(self.path, now=1050.0)
        self.assertTrue(state.refused(loaded, A, "origin/main", now=1050.0))
        self.assertFalse(state.refused(loaded, A, "origin/latest", now=1050.0))
        self.assertFalse(state.refused(loaded, B, "origin/main", now=1050.0))
        self.assertFalse(state.refused(loaded, A, "origin/main", now=1101.0))
        # Expired refusals are not returned and are dropped on the next write.
        self.assertEqual(state.load(self.path, now=1101.0).refusals, ())
        state.record_hint("demo", B, "probe", path=self.path, now=1102.0)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["refusals"], [])

    def test_writers_never_raise_when_the_directory_is_unusable(self):
        blocker = Path(self._tmp.name) / "blocked"
        blocker.write_text("file, not a directory", encoding="utf-8")
        path = blocker / "state.json"
        self.assertFalse(state.record_hint("demo", A, "install", path=path))
        self.assertFalse(state.record_refusal(A, "origin/main", SkipReason.NOT_DESCENDANT, 1.0, path=path))
        self.assertEqual(state.load(path).hints, ())

    def test_no_temporary_files_left_behind(self):
        state.record_hint("demo", A, "install", path=self.path)
        state.record_hint("demo", B, "install", path=self.path)
        leftovers = [p.name for p in self.path.parent.iterdir()
                     if p.name not in {"state.json", "state.json.lock"}]
        self.assertEqual(leftovers, [])

    def test_default_path_is_under_the_runtime_directory(self):
        from sandbox.core import _paths
        self.assertEqual(state.default_path(),
                         _paths.RUNTIME_DIR / "controller-update" / "state.json")
        self.assertEqual(state.default_path(Path("/x/runtime")),
                         Path("/x/runtime/controller-update/state.json"))


if __name__ == "__main__":
    unittest.main()
