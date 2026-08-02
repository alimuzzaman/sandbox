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

    def test_network_reservations_are_locked_unique_and_idempotent(self):
        repository = self.repository()
        machine_ids = [f"sb-{index:012x}" for index in range(12)]
        reservations = {}
        guard = threading.Lock()

        def reserve(machine_id):
            value = repository.reserve_network(machine_id)
            with guard:
                reservations[machine_id] = value

        threads = [threading.Thread(target=reserve, args=(machine_id,))
                   for machine_id in machine_ids]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(reservations), len(machine_ids))
        self.assertEqual(len({value["subnet"] for value in reservations.values()}),
                         len(machine_ids))
        first = machine_ids[0]
        self.assertEqual(repository.reserve_network(first), reservations[first])
        self.assertEqual(repository.release_network(first, reservations[first]), "removed")

    def test_drifted_network_reservation_is_preserved_for_recovery(self):
        repository = self.repository()
        machine_id = "sb-0123456789ab"
        reserved = repository.reserve_network(machine_id)
        observed = {**reserved, "subnet": "10.203.255.252/30"}
        self.assertEqual(repository.release_network(machine_id, observed), "drifted")
        self.assertIn(f"networks:{machine_id}", repository.snapshot()["recovery"])


if __name__ == "__main__": unittest.main()
