import tempfile
import unittest
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path

from tests.fixtures.hosting_image_activation import RaceHarness


class ActivationRaceTests(unittest.TestCase):
    def test_custody_transaction_enters_target_then_host_then_stage_and_releases_reverse(self):
        from sandbox.hosting.images.staging_repository import StageRepository
        events = []
        class LoggingStageRepository(StageRepository):
            @contextmanager
            def target_lock(self, target_identity, **kwargs):
                events.append("enter:stage")
                try: yield
                finally: events.append("exit:stage")
        class Target:
            @contextmanager
            def target_mutation_transaction(self, target_identity):
                events.append("enter:target")
                try: yield
                finally: events.append("exit:target")
        class Host:
            @contextmanager
            def atomic_host_state_transaction(self, target_identity):
                events.append("enter:host")
                try: yield
                finally: events.append("exit:host")
            def validate_atomic_host_state_evidence(self, evidence): return True
            def validate_durable_terminal_authority(self, evidence): return True
        with tempfile.TemporaryDirectory() as directory:
            repository = LoggingStageRepository(Path(directory) / "stage")
            with repository.proof_custody_transaction(
                    "target-a", target_mutation_port=Target(), host_state_port=Host()):
                events.append("body")
        self.assertEqual(events, ["enter:target", "enter:host", "enter:stage", "body",
                                  "exit:stage", "exit:host", "exit:target"])

    def test_every_registered_pair_has_one_owner_and_zero_loser_effects(self):
        for first, second in combinations(RaceHarness.CAPABILITIES, 2):
            with self.subTest(first=first, second=second):
                harness = RaceHarness()
                with harness.acquire(first):
                    with self.assertRaises(TimeoutError):
                        with harness.acquire(second): harness.effects.append(second)
                    harness.effects.append(first)
                self.assertEqual(harness.effects, [first])

    def test_real_shared_ports_make_every_registered_loser_effect_free(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = RecoveryRepository(root / "hosts.json", root / "locks")
            for first, second in combinations(RaceHarness.CAPABILITIES, 2):
                effects = []
                with self.subTest(first=first, second=second):
                    with repository.target_mutation_port(first).target_mutation_transaction("target-a"):
                        with self.assertRaises(TimeoutError):
                            with repository.target_mutation_port(
                                    second, timeout_seconds=0.01).target_mutation_transaction("target-a"):
                                effects.append(second)
                        effects.append(first)
                    self.assertEqual(effects, [first])

    def test_registry_is_explicit_and_unknown_bypass_fails_before_lock(self):
        import sandbox.core._hosting as hosting
        self.assertEqual(set(hosting.TARGET_MUTATION_CAPABILITIES), set(RaceHarness.CAPABILITIES))
        with self.assertRaises(hosting.HostingError): hosting.target_mutation_capability("unknown")

    def test_shared_outer_repository_preserves_unknown_siblings(self):
        from sandbox.hosting.recovery.repository import RecoveryRepository
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repository = RecoveryRepository(root / "hosts.json", root / "locks")
            state = {"version": 2, "hosts": {"target-a": {"generation": 0,
                "foreign": {"byte_semantic": [1, 2, 3]}}}}
            repository._write(state)
            port = repository.activation_host_state_port()
            with repository.target_mutation_port("activate").target_mutation_transaction("target-a"):
                with port.atomic_host_state_transaction("target-a"):
                    port.update_activation_nested("target-a", 0, lambda current: {
                        "schema_version": 1, "generation": 0, "current": None, "previous": None,
                        "active": None, "results": {}, "tombstones": {},
                        "recovery_provisional": None, "recovery_results": {},
                        "reserved_terminal_bytes": 0})
            self.assertEqual(repository.load()["hosts"]["target-a"]["foreign"],
                             {"byte_semantic": [1, 2, 3]})


if __name__ == "__main__": unittest.main()
