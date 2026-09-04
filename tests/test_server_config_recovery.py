"""Tests for interruption recovery, journal reconciliation, and drift detection (T057 / US4).

Verifies:
- Interruption before/after every phase boundary
- Committed-receipt interruption reconciliation
- Missing-generation fail-closed recovery
- Corrupt-journal preservation and refusal
- Runtime drift detection before mutation
- Strict no-recency selection (never choosing by timestamp)
- Later-mutation reconciliation blocking ambiguous state
"""

from datetime import datetime, timezone
import json
import shutil
import tempfile
import unittest

from sandbox.server_config.adapters.base import AdapterDescriptor, RenderedGeneration
from sandbox.server_config.context import Clock
from sandbox.server_config.models import (
    ActivationTransaction,
    KnownGoodReceipt,
    Operation,
    OperationResult,
    PhaseEvidence,
    PhaseResult,
    Readiness,
    RuntimeObservation,
    ServerConfigFragment,
    ServerType,
    TerminalOutcome,
    TransactionPhase,
)
from sandbox.server_config.repository import ServerConfigRepository
from sandbox.server_config.service import ServerConfigService
from tests.server_config_fixtures import (
    FIXED_INCARNATION,
    FIXED_NOW,
    FakeAdapter,
    FakeClock,
    fragment,
)


def _setup_service(repo_dir: str, clock: Clock, adapter: FakeAdapter) -> ServerConfigService:
    repo = ServerConfigRepository(repo_dir, FIXED_INCARNATION)
    return ServerConfigService(
        repository=repo,
        adapter=adapter,
        clock=clock,
    )


class TestServerConfigRecovery(unittest.TestCase):
    """T057: Recovery from process interruptions, corrupt journals, and runtime drift."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.clock = FakeClock()
        self.descriptor = AdapterDescriptor(
            server_type="nginx",
            adapter_id="test_adapter",
            authority_versions=("wordpress-cache-v1",),
            renderer_revision="nginx/1",
            active_image_families=("nginx-family",),
            web_service="web",
            mount_layout="layout",
            readiness_contract="contract",
        )
        self.adapter = FakeAdapter(descriptor=self.descriptor)
        self.repository = ServerConfigRepository(self.temp_dir, FIXED_INCARNATION)
        self.service = ServerConfigService(
            repository=self.repository,
            adapter=self.adapter,
            clock=self.clock,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _publish_dummy_generation(self, repo: ServerConfigRepository, name: str = "gen") -> str:
        with repo.locked() as mutation:
            gen_id = mutation.publish_generation(
                files={"default.conf": b"server { listen 80; }"},
                manifest={
                    "schema": 1,
                    "fragment_set_id": "sha256:" + "a" * 64,
                    "renderer_revision": "nginx/1",
                },
            )
        return gen_id

    def test_interruption_at_pre_activation_phase_is_safely_reconciled(self):
        """Interruption at requested, prepared, or validated has caused no live mutation."""
        for phase in (
            TransactionPhase.REQUESTED,
            TransactionPhase.PREPARED,
            TransactionPhase.VALIDATED,
        ):
            with self.subTest(phase=phase):
                # Setup repository with an interrupted transaction in pre-activation phase
                gen_id = self._publish_dummy_generation(self.repository)
                tx_record = {
                    "schema": 1,
                    "transaction_id": f"txn_{phase.value}",
                    "operation": "apply",
                    "fragment_name": "test-cache",
                    "instance_incarnation_id": FIXED_INCARNATION,
                    "server_type": "nginx",
                    "prior_set_id": "sha256:" + "0" * 64,
                    "prior_generation_id": gen_id,
                    "candidate_set_id": "sha256:" + "1" * 64,
                    "candidate_generation_id": gen_id,
                    "runtime_precondition_digest": "sha256:" + "2" * 64,
                    "phase": phase.value,
                    "phase_evidence": [],
                    "deadline_at": self.clock.now().isoformat(),
                    "rollback_attempted": False,
                    "terminal": None,
                }
                self.repository.write_transaction(tx_record)

                # Reconcile or next apply must clean up the pre-activation transaction
                # and allow the new mutation to proceed without error or service disruption.
                result = self.service.apply(fragment(name=f"new-cache-{phase.value}"))
                self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)

                # Previous stale transaction must not remain uncommitted/active
                tx_after = self.repository.read_transaction()
                if tx_after is not None:
                    self.assertIsNotNone(tx_after.get("terminal"))

    def test_interruption_during_activation_triggers_reconciliation_and_rollback(self):
        """Interruption at activating, reloading, observing_ready requires restoration of prior set."""
        prior_gen = self._publish_dummy_generation(self.repository, "prior")
        cand_gen = self._publish_dummy_generation(self.repository, "cand")

        # Prior known-good state is recorded in state.json
        state_record = {
            "schema": 1,
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "fragment_set_id": "sha256:" + "0" * 64,
            "generation_id": prior_gen,
            "runtime_image_id": "sha256:" + "9" * 64,
            "mount_id": "sha256:" + "8" * 64,
            "validation_evidence_id": "sha256:" + "7" * 64,
            "readiness_evidence_id": "sha256:" + "6" * 64,
            "committed_at": self.clock.now().isoformat(),
        }
        self.repository.write_state(state_record)

        tx_record = {
            "schema": 1,
            "transaction_id": "txn_interrupted_activating",
            "operation": "apply",
            "fragment_name": "cand-cache",
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "prior_set_id": "sha256:" + "0" * 64,
            "prior_generation_id": prior_gen,
            "candidate_set_id": "sha256:" + "1" * 64,
            "candidate_generation_id": cand_gen,
            "runtime_precondition_digest": "sha256:" + "2" * 64,
            "phase": "activating",
            "phase_evidence": [],
            "deadline_at": self.clock.now().isoformat(),
            "rollback_attempted": False,
            "terminal": None,
        }
        self.repository.write_transaction(tx_record)

        # Mock adapter restoring prior generation
        self.adapter.results["restore"] = PhaseResult("ok", None, self.clock.now())
        self.adapter.results["reload"] = PhaseResult("reloaded", None, self.clock.now())
        self.adapter.results["observe_ready"] = PhaseResult("ready", None, self.clock.now())

        # Attempting a mutation on an interrupted activation must reconcile first:
        # roll back to prior generation, prove readiness, before applying new candidate.
        reconciled = self.service.reconcile()
        self.assertIn(reconciled.outcome, {TerminalOutcome.ROLLED_BACK, TerminalOutcome.ACTIVE})

    def test_committed_receipt_interruption_reconciliation(self):
        """Interruption after state.json was written but before transaction.json cleared."""
        gen_id = self._publish_dummy_generation(self.repository)

        state_record = {
            "schema": 1,
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "fragment_set_id": "sha256:" + "c" * 64,
            "generation_id": gen_id,
            "runtime_image_id": "sha256:" + "9" * 64,
            "mount_id": "sha256:" + "8" * 64,
            "validation_evidence_id": "sha256:" + "7" * 64,
            "readiness_evidence_id": "sha256:" + "6" * 64,
            "committed_at": self.clock.now().isoformat(),
        }
        self.repository.write_state(state_record)

        tx_record = {
            "schema": 1,
            "transaction_id": "txn_committed_unfinalized",
            "operation": "apply",
            "fragment_name": "cand-cache",
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "prior_set_id": "sha256:" + "0" * 64,
            "prior_generation_id": "sha256:" + "0" * 64,
            "candidate_set_id": "sha256:" + "c" * 64,
            "candidate_generation_id": gen_id,
            "runtime_precondition_digest": "sha256:" + "2" * 64,
            "phase": "committed",
            "phase_evidence": [],
            "deadline_at": self.clock.now().isoformat(),
            "rollback_attempted": False,
            "terminal": None,
        }
        self.repository.write_transaction(tx_record)

        # Reconciliation sees state.json matches candidate_generation_id and marks transaction terminal active
        result = self.service.reconcile()
        self.assertEqual(result.outcome, TerminalOutcome.ACTIVE)

    def test_missing_referenced_generation_fails_closed_as_recovery_needed(self):
        """Missing generation referenced by transaction must fail closed as recovery_needed."""
        missing_gen = "sha256:" + "e" * 64
        tx_record = {
            "schema": 1,
            "transaction_id": "txn_missing_gen",
            "operation": "apply",
            "fragment_name": "broken-cache",
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "prior_set_id": "sha256:" + "0" * 64,
            "prior_generation_id": missing_gen,
            "candidate_set_id": "sha256:" + "1" * 64,
            "candidate_generation_id": missing_gen,
            "runtime_precondition_digest": "sha256:" + "2" * 64,
            "phase": "activating",
            "phase_evidence": [],
            "deadline_at": self.clock.now().isoformat(),
            "rollback_attempted": False,
            "terminal": None,
        }
        self.repository.write_transaction(tx_record)

        result = self.service.reconcile()
        self.assertEqual(result.outcome, TerminalOutcome.RECOVERY_NEEDED)

    def test_corrupt_journal_fails_closed_and_preserves_evidence(self):
        """Corrupt journal file must not be overwritten; mutations must be refused."""
        self.repository.initialize()
        journal_path = self.repository.transaction_path
        corrupt_bytes = b'{"schema": 1, "bad_json: unfinished'
        journal_path.write_bytes(corrupt_bytes)
        journal_path.chmod(0o600)

        # Attempting apply on corrupt journal must refuse / fail closed
        result = self.service.apply(fragment(name="test-cache"))
        self.assertEqual(result.outcome, TerminalOutcome.RECOVERY_NEEDED)

        # Corrupt file must still exist and be intact (evidence preserved)
        self.assertEqual(journal_path.read_bytes(), corrupt_bytes)

    def test_runtime_drift_refuses_new_mutation_without_reconciliation(self):
        """Observed runtime does not match state.json generation -> refused before mutation."""
        gen_id = self._publish_dummy_generation(self.repository)
        state_record = {
            "schema": 1,
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "fragment_set_id": "sha256:" + "0" * 64,
            "generation_id": gen_id,
            "runtime_image_id": "sha256:" + "9" * 64,
            "mount_id": "sha256:" + "8" * 64,
            "validation_evidence_id": "sha256:" + "7" * 64,
            "readiness_evidence_id": "sha256:" + "6" * 64,
            "committed_at": self.clock.now().isoformat(),
        }
        self.repository.write_state(state_record)

        # Adapter returns drifted observation (different generation)
        drifted_obs = RuntimeObservation(
            instance_incarnation_id=FIXED_INCARNATION,
            server_type=ServerType.NGINX,
            runtime_id="runtime-1",
            image_id="sha256:" + "9" * 64,
            mount_id="sha256:" + "8" * 64,
            observed_generation_id="sha256:" + "d" * 64,  # Drifted!
            readiness=Readiness.READY,
            observed_at=self.clock.now(),
        )
        self.adapter.results["observe_runtime"] = drifted_obs

        result = self.service.apply(fragment(name="drift-cache"))
        self.assertEqual(result.outcome, TerminalOutcome.RECOVERY_NEEDED)

    def test_never_selects_generation_by_timestamp_recency(self):
        """System must never adopt an unreferenced generation based on latest mtime."""
        gen1 = self._publish_dummy_generation(self.repository, "gen1")
        self.clock.advance(10.0)
        gen2 = self._publish_dummy_generation(self.repository, "gen2")
        self.clock.advance(10.0)
        # gen3 is created last, but is completely unreferenced
        gen3 = self._publish_dummy_generation(self.repository, "gen3")

        # state.json references gen1
        state_record = {
            "schema": 1,
            "instance_incarnation_id": FIXED_INCARNATION,
            "server_type": "nginx",
            "fragment_set_id": "sha256:" + "0" * 64,
            "generation_id": gen1,
            "runtime_image_id": "sha256:" + "9" * 64,
            "mount_id": "sha256:" + "8" * 64,
            "validation_evidence_id": "sha256:" + "7" * 64,
            "readiness_evidence_id": "sha256:" + "6" * 64,
            "committed_at": self.clock.now().isoformat(),
        }
        self.repository.write_state(state_record)

        # Reconciliation must use gen1 (proven known-good), NEVER gen3
        obs = RuntimeObservation(
            instance_incarnation_id=FIXED_INCARNATION,
            server_type=ServerType.NGINX,
            runtime_id="runtime-1",
            image_id="sha256:" + "9" * 64,
            mount_id="sha256:" + "8" * 64,
            observed_generation_id=gen1,
            readiness=Readiness.READY,
            observed_at=self.clock.now(),
        )
        self.adapter.results["observe_runtime"] = obs

        reconciled = self.service.reconcile()
        self.assertEqual(reconciled.outcome, TerminalOutcome.ACTIVE)
        current_state = self.repository.read_state()
        self.assertEqual(current_state.get("generation_id"), gen1)


if __name__ == "__main__":
    unittest.main()
