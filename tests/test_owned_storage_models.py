"""Unit tests for owned storage and lifecycle models."""

import unittest
from datetime import datetime, timezone

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptancePhase,
    AcceptanceState,
    AuthorityCapability,
    CapabilityAcceptance,
    CapabilityAcceptanceRequest,
    CapabilityPromotion,
    CapabilityReviewDecision,
    CapabilityReviewRequest,
    CapabilityRevocation,
    PromotionPhase,
    ReviewDecision,
    ReviewPhase,
    SupportTier,
)
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityOwnedObject,
    AuthorityPolicy,
    CandidateDecision,
    CanonicalOperationRequest,
    CleanupIntent,
    CleanupPhase,
    CleanupOutcome,
    GenerationBinding,
    LegacyProjection,
    MaterializationBinding,
    MaterializationLease,
    ObjectKind,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    OperationType,
    PolicyMode,
    PreviewCandidate,
    QualificationAdmission,
    QualificationState,
    ReclamationPreview,
    ReferenceSnapshot,
    RelationshipCurrentSelection,
    RetentionKind,
    RetentionPolicyProjection,
)


class TestOwnedStorageModels(unittest.TestCase):
    def test_lifecycle_models_initialization(self):
        cap = AuthorityCapability(
            capability_id="owned-storage-authority-v1",
            remote_identity="remote_test",
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            support_tier=SupportTier.IMPLEMENTED_UNPROVEN,
            adoptable=False,
            service_revision="rev_123",
            owner_identity_digest="sha256:owner",
            root_identity_digest="sha256:root",
            primitive_states={"dedicated_identity": "pass"},
            evidence_id=None,
            ordinary_evidence_id=None,
            acceptance_state=AcceptanceState.PENDING_ORDINARY,
            observed_at="2026-09-04T00:00:00Z",
            expires_at="2026-09-04T00:15:00Z",
            reason_code="implemented_unproven",
        )
        self.assertEqual(cap.capability_id, "owned-storage-authority-v1")
        self.assertFalse(cap.adoptable)
        self.assertEqual(cap.support_tier, SupportTier.IMPLEMENTED_UNPROVEN)

    def test_authority_binding_phases(self):
        binding = AuthorityAdoptionBinding(
            authority_binding_id="bind_1",
            binding_generation=1,
            remote_identity="rem_1",
            project_identity="proj_1",
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            fixture_identity="fix_1",
            review_decision_id="dec_1",
            promotion_id="prom_1",
            evidence_candidate_id="cand_1",
            evidence_digest="sha256:ev",
            source_revision="sha256:src",
            service_revision="sha256:srv",
            controller_revision="sha256:ctrl",
            contract_revision="sha256:ctr",
            lifecycle_request_id="req_1",
            request_digest="sha256:req",
            lifecycle_generation=1,
            binding_digest="sha256:bind",
            expires_at="2026-09-04T01:00:00Z",
            revocation_generation=None,
            phase=AdoptionBindingPhase.PREPARED,
        )
        self.assertEqual(binding.phase, AdoptionBindingPhase.PREPARED)
        self.assertIsNone(binding.revocation_generation)

    def test_canonical_operation_request(self):
        op = CanonicalOperationRequest(
            operation_id="op_1",
            operation_type=OperationType.PUBLISH,
            request_id="req_pub_1",
            request_digest="sha256:req_digest",
            authorization_id="auth_1",
            controller_epoch="epoch_1",
            sequence=1,
            caller_identity_digest="sha256:caller",
            remote_identity="rem_1",
            project_identity="proj_1",
            relationship_id="rel_1",
            workspace_id="ws_1",
            job_id=None,
            target_object_id=None,
            canonical_evidence_digest="sha256:evidence",
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id="prom_1",
            authority_binding_id="bind_1",
            phase=OperationPhase.RESERVED,
            outcome=None,
            reason_code=None,
            created_at="2026-09-04T00:00:00Z",
            updated_at="2026-09-04T00:00:00Z",
        )
        self.assertEqual(op.operation_type, OperationType.PUBLISH)
        self.assertEqual(op.phase, OperationPhase.RESERVED)

    def test_preview_and_cleanup_models(self):
        candidate = PreviewCandidate(
            object_id="obj_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            lifecycle=ObjectLifecycle.ELIGIBLE,
            decision=CandidateDecision.ELIGIBLE,
            reason_code="eligible",
            estimated_bytes=1024,
            object_evidence_digest="sha256:obj",
            reference_snapshot_digest="sha256:snap",
        )
        self.assertEqual(candidate.decision, CandidateDecision.ELIGIBLE)
        self.assertEqual(candidate.estimated_bytes, 1024)

        preview = ReclamationPreview(
            preview_id="prev_1",
            remote_identity="rem_1",
            project_identity="proj_1",
            inventory_generation=1,
            policy_generation=1,
            candidate_digest="sha256:cand",
            candidates=[candidate],
            estimated_reclaimable_bytes=1024,
            complete=True,
            created_at="2026-09-04T00:00:00Z",
            expires_at="2026-09-04T00:15:00Z",
        )
        self.assertEqual(preview.estimated_reclaimable_bytes, 1024)
        self.assertTrue(preview.complete)


if __name__ == "__main__":
    unittest.main()
