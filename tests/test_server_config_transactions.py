"""Tests for durable activation transaction lifecycle and state machine (T056 / US4).

Verifies the transition graph:
REQUESTED -> PREPARED -> VALIDATED -> ACTIVATING -> RELOADING -> OBSERVING_READY -> COMMITTED
and rollback:
ACTIVATING/RELOADING/OBSERVING_READY -> RESTORING_PRIOR -> RECOVERY_RELOADING -> RECOVERY_OBSERVING_READY
along with exact prior/candidate binding and terminal outcomes.
"""

from datetime import datetime, timezone
import unittest

from sandbox.server_config.models import (
    ActivationTransaction,
    Operation,
    PhaseEvidence,
    ServerType,
    TerminalOutcome,
    TransactionPhase,
)


FIXED_NOW = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 9, 2, 8, 3, tzinfo=timezone.utc)
INCARNATION = "inc_" + "1" * 32
PRIOR_SET = "sha256:" + "1" * 64
PRIOR_GEN = "sha256:" + "2" * 64
CANDIDATE_SET = "sha256:" + "3" * 64
CANDIDATE_GEN = "sha256:" + "4" * 64
PRECONDITION = "sha256:" + "5" * 64


def make_transaction(**overrides) -> ActivationTransaction:
    values = {
        "transaction_id": "txn_001",
        "operation": Operation.APPLY,
        "fragment_name": "page-cache",
        "instance_incarnation_id": INCARNATION,
        "server_type": ServerType.NGINX,
        "prior_set_id": PRIOR_SET,
        "prior_generation_id": PRIOR_GEN,
        "candidate_set_id": CANDIDATE_SET,
        "candidate_generation_id": CANDIDATE_GEN,
        "runtime_precondition_digest": PRECONDITION,
        "deadline_at": DEADLINE,
    }
    values.update(overrides)
    return ActivationTransaction.requested(**values)


class TestServerConfigTransactions(unittest.TestCase):
    """T056: Durable transaction transitions, bindings, and terminal outcomes."""

    def test_happy_path_full_lifecycle_to_committed_and_active(self):
        tx = make_transaction()
        self.assertEqual(tx.phase, TransactionPhase.REQUESTED)
        self.assertFalse(tx.is_terminal)
        self.assertFalse(tx.rollback_attempted)

        prepared = tx.transition(
            TransactionPhase.PREPARED,
            evidence=PhaseEvidence(TransactionPhase.PREPARED, "prepared", None, FIXED_NOW),
        )
        self.assertEqual(prepared.phase, TransactionPhase.PREPARED)

        validated = prepared.transition(
            TransactionPhase.VALIDATED,
            evidence=PhaseEvidence(TransactionPhase.VALIDATED, "passed", None, FIXED_NOW),
        )
        self.assertEqual(validated.phase, TransactionPhase.VALIDATED)

        activating = validated.transition(
            TransactionPhase.ACTIVATING,
            evidence=PhaseEvidence(TransactionPhase.ACTIVATING, "activating", None, FIXED_NOW),
        )
        self.assertEqual(activating.phase, TransactionPhase.ACTIVATING)

        reloading = activating.transition(
            TransactionPhase.RELOADING,
            evidence=PhaseEvidence(TransactionPhase.RELOADING, "reloading", None, FIXED_NOW),
        )
        self.assertEqual(reloading.phase, TransactionPhase.RELOADING)

        observing = reloading.transition(
            TransactionPhase.OBSERVING_READY,
            evidence=PhaseEvidence(TransactionPhase.OBSERVING_READY, "ready", None, FIXED_NOW),
        )
        self.assertEqual(observing.phase, TransactionPhase.OBSERVING_READY)

        committed = observing.transition(
            TransactionPhase.COMMITTED,
            evidence=PhaseEvidence(TransactionPhase.COMMITTED, "committed", None, FIXED_NOW),
        )
        self.assertEqual(committed.phase, TransactionPhase.COMMITTED)

        finished = committed.finish(TerminalOutcome.ACTIVE)
        self.assertTrue(finished.is_terminal)
        self.assertEqual(finished.terminal, TerminalOutcome.ACTIVE)
        self.assertEqual(len(finished.phase_evidence), 6)

    def test_terminal_outcomes_from_requested_phase(self):
        tx = make_transaction()
        # NO_OP is allowed from REQUESTED
        no_op = tx.finish(TerminalOutcome.NO_OP)
        self.assertEqual(no_op.terminal, TerminalOutcome.NO_OP)

        # REFUSED is allowed from REQUESTED
        refused = tx.finish(TerminalOutcome.REFUSED)
        self.assertEqual(refused.terminal, TerminalOutcome.REFUSED)

        # CONFLICT is allowed from REQUESTED
        conflict = tx.finish(TerminalOutcome.CONFLICT)
        self.assertEqual(conflict.terminal, TerminalOutcome.CONFLICT)

        # ACTIVE is NOT allowed from REQUESTED
        with self.assertRaisesRegex(ValueError, "terminal outcome is invalid"):
            tx.finish(TerminalOutcome.ACTIVE)

        # ROLLED_BACK is NOT allowed from REQUESTED
        with self.assertRaisesRegex(ValueError, "terminal outcome is invalid"):
            tx.finish(TerminalOutcome.ROLLED_BACK)

    def test_terminal_outcomes_from_prepared_and_validated_phases(self):
        tx = make_transaction()
        prepared = tx.transition(TransactionPhase.PREPARED)
        refused = prepared.finish(TerminalOutcome.REFUSED)
        self.assertEqual(refused.terminal, TerminalOutcome.REFUSED)

        with self.assertRaisesRegex(ValueError, "terminal outcome is invalid"):
            prepared.finish(TerminalOutcome.ACTIVE)

        validated = tx.transition(TransactionPhase.PREPARED).transition(TransactionPhase.VALIDATED)
        refused_val = validated.finish(TerminalOutcome.REFUSED)
        self.assertEqual(refused_val.terminal, TerminalOutcome.REFUSED)

        with self.assertRaisesRegex(ValueError, "terminal outcome is invalid"):
            validated.finish(TerminalOutcome.ACTIVE)

    def test_rollback_transitions_and_terminal_outcomes(self):
        tx = make_transaction()
        activating = (
            tx.transition(TransactionPhase.PREPARED)
            .transition(TransactionPhase.VALIDATED)
            .transition(TransactionPhase.ACTIVATING)
        )

        # Rollback from ACTIVATING
        restoring = activating.begin_rollback(code="activation_failed", at=FIXED_NOW)
        self.assertEqual(restoring.phase, TransactionPhase.RESTORING_PRIOR)
        self.assertTrue(restoring.rollback_attempted)

        recovery_reloading = restoring.transition(TransactionPhase.RECOVERY_RELOADING)
        self.assertEqual(recovery_reloading.phase, TransactionPhase.RECOVERY_RELOADING)

        recovery_observing = recovery_reloading.transition(TransactionPhase.RECOVERY_OBSERVING_READY)
        self.assertEqual(recovery_observing.phase, TransactionPhase.RECOVERY_OBSERVING_READY)

        # Successful rollback finishes as ROLLED_BACK
        rolled_back = recovery_observing.finish(TerminalOutcome.ROLLED_BACK)
        self.assertEqual(rolled_back.terminal, TerminalOutcome.ROLLED_BACK)

        # Unproven or failed rollback finishes as RECOVERY_NEEDED
        rec_needed = recovery_observing.finish(TerminalOutcome.RECOVERY_NEEDED)
        self.assertEqual(rec_needed.terminal, TerminalOutcome.RECOVERY_NEEDED)

        # RECOVERY_NEEDED also valid directly from RESTORING_PRIOR or RECOVERY_RELOADING
        rec_from_restoring = restoring.finish(TerminalOutcome.RECOVERY_NEEDED)
        self.assertEqual(rec_from_restoring.terminal, TerminalOutcome.RECOVERY_NEEDED)

        rec_from_reloading = recovery_reloading.finish(TerminalOutcome.RECOVERY_NEEDED)
        self.assertEqual(rec_from_reloading.terminal, TerminalOutcome.RECOVERY_NEEDED)

    def test_rollback_cannot_be_initiated_from_pre_activation_phases(self):
        tx = make_transaction()
        with self.assertRaisesRegex(ValueError, "rollback cannot begin before possible live mutation"):
            tx.begin_rollback(code="fault", at=FIXED_NOW)

        prepared = tx.transition(TransactionPhase.PREPARED)
        with self.assertRaisesRegex(ValueError, "rollback cannot begin before possible live mutation"):
            prepared.begin_rollback(code="fault", at=FIXED_NOW)

        validated = prepared.transition(TransactionPhase.VALIDATED)
        with self.assertRaisesRegex(ValueError, "rollback cannot begin before possible live mutation"):
            validated.begin_rollback(code="fault", at=FIXED_NOW)

    def test_cannot_transition_to_restoring_prior_directly(self):
        tx = make_transaction()
        activating = (
            tx.transition(TransactionPhase.PREPARED)
            .transition(TransactionPhase.VALIDATED)
            .transition(TransactionPhase.ACTIVATING)
        )
        with self.assertRaisesRegex(ValueError, "rollback must begin through begin_rollback"):
            activating.transition(TransactionPhase.RESTORING_PRIOR)

    def test_cannot_rollback_twice(self):
        tx = make_transaction()
        activating = (
            tx.transition(TransactionPhase.PREPARED)
            .transition(TransactionPhase.VALIDATED)
            .transition(TransactionPhase.ACTIVATING)
        )
        restoring = activating.begin_rollback(code="fault", at=FIXED_NOW)
        with self.assertRaisesRegex(ValueError, "rollback already attempted"):
            restoring.begin_rollback(code="fault2", at=FIXED_NOW)

    def test_terminal_transaction_cannot_transition_or_finish_twice(self):
        tx = make_transaction()
        finished = tx.finish(TerminalOutcome.REFUSED)
        with self.assertRaisesRegex(ValueError, "terminal transaction cannot transition"):
            finished.transition(TransactionPhase.PREPARED)

        with self.assertRaisesRegex(ValueError, "terminal transaction cannot finish twice"):
            finished.finish(TerminalOutcome.REFUSED)

        with self.assertRaisesRegex(ValueError, "terminal transaction cannot roll back"):
            finished.begin_rollback(code="fault", at=FIXED_NOW)

    def test_invalid_phase_jumps_are_refused(self):
        tx = make_transaction()
        with self.assertRaisesRegex(ValueError, "invalid transaction transition"):
            tx.transition(TransactionPhase.COMMITTED)

        with self.assertRaisesRegex(ValueError, "invalid transaction transition"):
            tx.transition(TransactionPhase.VALIDATED)

        prepared = tx.transition(TransactionPhase.PREPARED)
        with self.assertRaisesRegex(ValueError, "invalid transaction transition"):
            prepared.transition(TransactionPhase.ACTIVATING)

    def test_evidence_phase_must_match_target_phase(self):
        tx = make_transaction()
        mismatched_evidence = PhaseEvidence(TransactionPhase.COMMITTED, "ok", None, FIXED_NOW)
        with self.assertRaisesRegex(ValueError, "phase evidence does not match transition"):
            tx.transition(TransactionPhase.PREPARED, evidence=mismatched_evidence)

    def test_exact_prior_and_candidate_binding_preserved(self):
        tx = make_transaction()
        self.assertEqual(tx.prior_set_id, PRIOR_SET)
        self.assertEqual(tx.prior_generation_id, PRIOR_GEN)
        self.assertEqual(tx.candidate_set_id, CANDIDATE_SET)
        self.assertEqual(tx.candidate_generation_id, CANDIDATE_GEN)
        self.assertEqual(tx.runtime_precondition_digest, PRECONDITION)
        self.assertEqual(tx.instance_incarnation_id, INCARNATION)

    def test_transaction_record_round_trip_serialization(self):
        tx = make_transaction()
        activating = (
            tx.transition(
                TransactionPhase.PREPARED,
                evidence=PhaseEvidence(TransactionPhase.PREPARED, "ok", None, FIXED_NOW),
            )
            .transition(
                TransactionPhase.VALIDATED,
                evidence=PhaseEvidence(TransactionPhase.VALIDATED, "passed", None, FIXED_NOW),
            )
            .transition(
                TransactionPhase.ACTIVATING,
                evidence=PhaseEvidence(TransactionPhase.ACTIVATING, "active", None, FIXED_NOW),
            )
        )
        record = activating.to_record()
        self.assertIsInstance(record, dict)
        self.assertEqual(record["schema"], 1)
        self.assertEqual(record["transaction_id"], "txn_001")
        self.assertEqual(record["phase"], "activating")
        self.assertEqual(record["prior_generation_id"], PRIOR_GEN)
        self.assertEqual(record["candidate_generation_id"], CANDIDATE_GEN)

        restored = ActivationTransaction.from_record(record)
        self.assertEqual(restored.transaction_id, activating.transaction_id)
        self.assertEqual(restored.phase, activating.phase)
        self.assertEqual(restored.prior_generation_id, activating.prior_generation_id)
        self.assertEqual(restored.candidate_generation_id, activating.candidate_generation_id)
        self.assertEqual(len(restored.phase_evidence), 3)


if __name__ == "__main__":
    unittest.main()
