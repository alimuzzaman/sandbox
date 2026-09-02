import unittest

from sandbox.hosting.recovery.models import (
    ActivationRecoveryObservation, ActivationTransitionProjection, canonical_digest,
)
from sandbox.hosting.recovery.service import ActivationTransitionObserver
from tests.fixtures.hosting_image_activation import DIGEST_A, DIGEST_B


class ActivationRecoveryTests(unittest.TestCase):
    def projection(self, *, phase="runtime_proven", operation="activate", entered=True):
        services = ({"service": "web", "declared_image": "repo/image@" + DIGEST_A,
            "repository_digest": "repo/image@" + DIGEST_A, "local_image_id": DIGEST_A,
            "config_digest": DIGEST_A, "platform": {"os": "linux", "architecture": "amd64"},
            "runtime_identity": "container-new", "topology_identity": "topology-a",
            "compose_project": "widget", "compose_config_hash": DIGEST_B,
            "healthy": True},)
        prior_services = ({**services[0], "runtime_identity": "container-prior"},)
        return ActivationTransitionProjection(DIGEST_A, DIGEST_B, operation, phase, entered, 3,
            DIGEST_A, DIGEST_B, {"machine_identity": "target-a",
                "target_identity": "registered-a", "daemon_identity": "runtime-a"},
            services, prior_services)

    def observation(self, generation):
        return {"target_epoch_start": "target-a", "target_epoch_end": "target-a",
                "target_identity_start": "registered-a",
                "target_identity_end": "registered-a",
                "runtime_epoch_start": "runtime-a", "runtime_epoch_end": "runtime-a",
                "generation_digest": generation, "services": list(self.projection().new_services)}

    def test_feature_048_observer_is_read_only_and_classifies_exact_values(self):
        calls = []
        observer = ActivationTransitionObserver(lambda _projection: (
            calls.append("read") or self.observation(DIGEST_A)))
        result = observer.observe(self.projection())
        self.assertEqual(result.classification, "exact_new"); self.assertEqual(calls, ["read"])
        self.assertFalse(hasattr(observer, "repository"))

    def test_changed_epochs_are_ambiguous_and_never_authorize(self):
        observed = self.observation(DIGEST_A); observed["runtime_epoch_end"] = "runtime-b"
        from sandbox.hosting.recovery.policy import classify_activation_transition
        result = classify_activation_transition(self.projection(), observed)
        self.assertEqual(result.classification, "ambiguous")
        observed = self.observation(DIGEST_A)
        observed["target_identity_end"] = "registered-b"
        self.assertEqual(classify_activation_transition(
            self.projection(), observed).classification, "ambiguous")

    def test_identical_runtime_projections_are_ambiguous_even_with_candidate_digest(self):
        from sandbox.hosting.recovery.policy import classify_activation_transition
        projection = self.projection()
        projection = ActivationTransitionProjection(
            projection.transaction_digest, projection.request_digest, projection.operation,
            projection.phase, projection.effect_entered, projection.expected_generation,
            projection.new_generation_digest, projection.prior_generation_digest,
            projection.target, projection.new_services, projection.new_services)
        self.assertEqual(classify_activation_transition(
            projection, self.observation(DIGEST_A)).classification, "ambiguous")

    def test_first_generation_has_no_prior_projection_but_exact_new_remains_safe(self):
        from sandbox.hosting.recovery.policy import classify_activation_transition
        projection = self.projection()
        projection = ActivationTransitionProjection(
            projection.transaction_digest, projection.request_digest, projection.operation,
            projection.phase, projection.effect_entered, 0,
            projection.new_generation_digest, None, projection.target,
            projection.new_services, ())
        self.assertEqual(classify_activation_transition(
            projection, self.observation(DIGEST_A)).classification, "exact_new")
        prior_shaped = self.observation(DIGEST_B)
        self.assertEqual(classify_activation_transition(
            projection, prior_shaped).classification, "neither")

    def test_phase_aware_projection_allows_missing_candidate_without_promotion(self):
        from sandbox.hosting.images.activation.repository import recovery_decision
        from sandbox.hosting.recovery.policy import classify_activation_transition
        base = self.projection(phase="accepted", entered=False)
        early = ActivationTransitionProjection(
            base.transaction_digest, base.request_digest, base.operation, base.phase,
            base.effect_entered, base.expected_generation, None,
            base.prior_generation_digest, base.target, (), base.prior_services)
        observed = self.observation(base.prior_generation_digest)
        observed["services"] = list(base.prior_services)
        classified = classify_activation_transition(early, observed)
        self.assertEqual(classified.classification, "exact_prior")
        self.assertEqual(recovery_decision(
            {"operation": "activate", "phase": "accepted", "effect_entered": False},
            classified.classification), ("recovery_no_effect", False, True))

        genesis = ActivationTransitionProjection(
            base.transaction_digest, base.request_digest, base.operation, "preflight",
            False, 0, None, None, base.target, (), ())
        empty = {**observed, "generation_digest": None, "services": []}
        self.assertEqual(classify_activation_transition(
            genesis, empty).classification, "exact_prior")

        pending = ActivationTransitionProjection(
            base.transaction_digest, base.request_digest, base.operation,
            "runtime_pending", True, 0, None, None, base.target, (), ())
        unknown = {**empty, "services": list(base.new_services)}
        self.assertEqual(recovery_decision(
            {"operation": "activate", "phase": "runtime_pending", "effect_entered": True},
            classify_activation_transition(pending, unknown).classification),
            ("effect_unknown", False, False))

    def test_partial_or_substituted_service_projection_never_classifies_exact(self):
        from sandbox.hosting.recovery.policy import classify_activation_transition
        observed = self.observation(DIGEST_A)
        observed["services"][0] = {**observed["services"][0], "healthy": False}
        self.assertEqual(classify_activation_transition(
            self.projection(), observed).classification, "neither")
        observed = self.observation(DIGEST_A); observed["services"] = []
        self.assertEqual(classify_activation_transition(
            self.projection(), observed).classification, "ambiguous")
        observed = self.observation(DIGEST_A)
        observed["services"][0] = {
            **observed["services"][0], "compose_project": "foreign"}
        self.assertEqual(classify_activation_transition(
            self.projection(), observed).classification, "neither")

    def test_full_operation_phase_class_matrix_has_only_receipt_complete_exact_new_promotion(self):
        from sandbox.hosting.images.activation.repository import recovery_decision
        for operation in ("activate", "rollback"):
            for phase in ("accepted", "preflight", "init_pending", "runtime_pending",
                          "runtime_proven", "edge_pending", "committed", "refused",
                          "failed", "cancelled", "uncertain"):
                for classification in ("exact_new", "exact_prior", "neither", "ambiguous"):
                    transaction = {"operation": operation, "phase": phase, "effect_entered": False,
                        "init_receipts": [], "running_observation": {"observation_digest": DIGEST_A},
                        "edge_result": {"terminal": True}}
                    _, promote, _ = recovery_decision(transaction, classification)
                    with self.subTest(operation=operation, phase=phase, classification=classification):
                        if promote: self.assertIn(phase, {"runtime_proven", "edge_pending"})
                        if classification != "exact_new": self.assertFalse(promote)

    def test_failed_apply_recovery_types_are_distinct(self):
        from sandbox.hosting.recovery.models import RecoveryRequest
        self.assertNotEqual(RecoveryRequest, ActivationRecoveryObservation)


if __name__ == "__main__": unittest.main()
