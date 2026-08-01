from pathlib import Path
import tempfile
import threading
import unittest


class TestNativeOwnership(unittest.TestCase):
    def repository(self):
        from sandbox.runtimes.managed.repository import NativeRepository
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        return NativeRepository(Path(temporary.name) / "state.json")

    def test_atomic_locked_updates_do_not_lose_instances(self):
        repository = self.repository()
        threads = [threading.Thread(target=repository.put_owned,
                   args=("backends", f"instance-{index}",
                         {"owner": f"owner-{index}", "last_applied": "a" * 64}))
                   for index in range(12)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(repository.snapshot()["backends"]), 12)

    def test_foreign_collision_and_drift_are_preserved(self):
        from sandbox.isolation.models import canonical_digest
        repository = self.repository(); observed = {"machine": "sb-demo", "image": "a"}
        repository.put_owned("backends", "sb-demo", {
            "owner": "owner", **observed, "last_applied": canonical_digest(observed),
        })
        with self.assertRaises(ValueError):
            repository.put_owned("backends", "sb-demo", {"owner": "foreign"})
        self.assertEqual(repository.remove_if_unchanged(
            "backends", "sb-demo", {"machine": "sb-demo", "image": "changed"}), "drifted")
        self.assertIn("backends:sb-demo", repository.snapshot()["recovery"])

    def test_unchanged_and_repeated_removal_are_idempotent(self):
        from sandbox.isolation.models import canonical_digest
        repository = self.repository(); observed = {"machine": "sb-demo"}
        repository.put_owned("backends", "sb-demo", {
            "owner": "owner", "machine": "sb-demo",
            "last_applied": canonical_digest(observed),
        })
        self.assertEqual(repository.remove_if_unchanged("backends", "sb-demo", observed), "removed")
        self.assertEqual(repository.remove_if_unchanged("backends", "sb-demo", observed), "absent")

    def test_version_zero_state_migrates_without_losing_recovery(self):
        import json
        repository = self.repository()
        repository.path.write_text(json.dumps({
            "version": 0, "backends": {}, "recovery": {"old": {"retry_state": "pending"}},
        }))
        result = repository.snapshot()
        self.assertEqual(result["version"], 1)
        self.assertIn("old", result["recovery"])


if __name__ == "__main__": unittest.main()
