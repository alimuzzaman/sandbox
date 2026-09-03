"""Unit and concurrency tests for storage and lifecycle repositories."""

import os
import tempfile
import unittest
from pathlib import Path

from sandbox.owned_storage_lifecycle.models import (
    AcceptanceOutcome,
    AcceptancePhase,
    AcceptanceState,
    AuthorityCapability,
    CapabilityPromotion,
    CapabilityReviewDecision,
    CapabilityReviewRequest,
    PromotionPhase,
    ReviewDecision,
    ReviewPhase,
    SupportTier,
)
from sandbox.owned_storage_lifecycle.repository import (
    LifecycleCASError,
    LifecycleConflictError,
    StorageAuthorityLifecycleRepository,
)
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityOwnedObject,
    AuthorityPolicy,
    CanonicalOperationRequest,
    ObjectKind,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    OperationType,
    PolicyMode,
    RelationshipCurrentSelection,
)
from sandbox.owned_storage.repository import (
    StorageAuthorityRepository,
    StorageRepositoryConflictError,
)


class TestStorageAuthorityLifecycleRepository(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.tmp_dir.name) / "lifecycle.json"
        self.repo = StorageAuthorityLifecycleRepository(self.repo_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_initialize_and_permissions(self):
        state = self.repo.load_state()
        self.assertEqual(state["generation"], 0)
        self.assertTrue(self.repo_path.exists())
        # Check permissions 0600 on POSIX
        mode = os.stat(self.repo_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_save_and_load_capability(self):
        cap = AuthorityCapability(
            capability_id="owned-storage-authority-v1",
            remote_identity="remote_test",
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            support_tier=SupportTier.IMPLEMENTED_UNPROVEN,
            adoptable=False,
            service_revision="rev_1",
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
        gen = self.repo.save_capability(cap, expected_generation=0)
        self.assertEqual(gen, 1)

        loaded_cap = self.repo.get_capability("remote_test")
        self.assertIsNotNone(loaded_cap)
        self.assertEqual(loaded_cap.remote_identity, "remote_test")
        self.assertEqual(loaded_cap.support_tier, SupportTier.IMPLEMENTED_UNPROVEN)

    def test_cas_generation_mismatch_fails(self):
        cap = AuthorityCapability(
            capability_id="owned-storage-authority-v1",
            remote_identity="remote_test",
            platform_mode="ubuntu-24.04-systemd-255-private-root-v1",
            support_tier=SupportTier.IMPLEMENTED_UNPROVEN,
            adoptable=False,
            service_revision="rev_1",
            owner_identity_digest="sha256:owner",
            root_identity_digest="sha256:root",
            primitive_states={},
            evidence_id=None,
            ordinary_evidence_id=None,
            acceptance_state=None,
            observed_at="2026-09-04T00:00:00Z",
            expires_at="2026-09-04T00:15:00Z",
            reason_code="implemented_unproven",
        )
        self.repo.save_capability(cap, expected_generation=0)

        # Attempt save with stale generation 0 instead of 1
        with self.assertRaises(LifecycleCASError):
            self.repo.save_capability(cap, expected_generation=0)

    def test_review_request_and_replay(self):
        req = CapabilityReviewRequest(
            review_request_id="rev_req_1",
            request_digest="sha256:digest_1",
            evidence_candidate_id="cand_1",
            candidate_close_generation=1,
            evidence_digest="sha256:ev",
            cleanup_evidence_digest="sha256:clean",
            source_revision="sha256:src",
            service_revision="sha256:srv",
            contract_revision="sha256:ctr",
            controller_identity_digest="sha256:ctrl",
            remote_identity="rem_1",
            project_identity="proj_1",
            fixture_identity="fix_1",
            reviewer_identity_digest="sha256:rev",
            requested_decision=ReviewDecision.ACCEPTED,
            proposed_review_decision_id="dec_1",
            proposed_promotion_id="prom_1",
            proposed_authority_binding_id="bind_1",
            expected_binding_digest="sha256:bind",
            lifecycle_generation=0,
            phase=ReviewPhase.RESERVED,
        )
        gen = self.repo.record_review_request(req, expected_generation=0)
        self.assertEqual(gen, 1)

        # Exact replay succeeds
        replayed = self.repo.record_review_request(req, expected_generation=1)
        self.assertEqual(replayed, 1)

        # Conflicting request with same ID but different digest raises ConflictError
        conflict_req = CapabilityReviewRequest(
            **{**req.__dict__, "request_digest": "sha256:different"}
        )
        with self.assertRaises(LifecycleConflictError):
            self.repo.record_review_request(conflict_req, expected_generation=1)


class TestStorageAuthorityRepository(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_sqlite_pragmas(self):
        with self.repo.connect() as conn:
            fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
            self.assertEqual(fk, 1)
            sync = conn.execute("PRAGMA synchronous;").fetchone()[0]
            # synchronous=FULL is 2
            self.assertEqual(sync, 2)

    def test_reserve_and_replay_canonical_operation(self):
        op = CanonicalOperationRequest(
            operation_id="op_pub_1",
            operation_type=OperationType.PUBLISH,
            request_id="req_pub_1",
            request_digest="sha256:pub_digest",
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
            canonical_evidence_digest="sha256:ev",
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
        created, existing = self.repo.reserve_operation(op)
        self.assertTrue(created)
        self.assertEqual(existing.operation_id, "op_pub_1")

        # Exact replay
        created2, existing2 = self.repo.reserve_operation(op)
        self.assertFalse(created2)
        self.assertEqual(existing2.operation_id, "op_pub_1")

        # Conflict replay: same request_id, different digest
        conflicting_op = CanonicalOperationRequest(
            **{**op.__dict__, "request_digest": "sha256:different"}
        )
        with self.assertRaises(StorageRepositoryConflictError):
            self.repo.reserve_operation(conflicting_op)

    def test_object_and_current_selection_lifecycle(self):
        op = CanonicalOperationRequest(
            operation_id="op_pub_2",
            operation_type=OperationType.PUBLISH,
            request_id="req_pub_2",
            request_digest="sha256:digest_2",
            authorization_id="auth_2",
            controller_epoch="epoch_1",
            sequence=2,
            caller_identity_digest="sha256:caller",
            remote_identity="rem_1",
            project_identity="proj_1",
            relationship_id="rel_1",
            workspace_id="ws_1",
            job_id=None,
            target_object_id=None,
            canonical_evidence_digest="sha256:ev",
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
        self.repo.reserve_operation(op)

        obj = AuthorityOwnedObject(
            object_id="obj_gen_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity="rem_1",
            project_identity="proj_1",
            relationship_id="rel_1",
            workspace_id="ws_1",
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_pub_2",
            lifecycle=ObjectLifecycle.ACCEPTED,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id="prom_1",
            evidence_id="ev_1",
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret",
            content_evidence={"file_count": 5, "byte_count": 500},
            filesystem_identity={"inode": 12345, "device": 1},
            known_bytes=500,
            created_at="2026-09-04T00:00:00Z",
            accepted_at="2026-09-04T00:01:00Z",
            removed_at=None,
        )
        self.repo.save_object(obj)

        fetched = self.repo.get_object("obj_gen_1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.object_id, "obj_gen_1")
        self.assertEqual(fetched.lifecycle, ObjectLifecycle.ACCEPTED)

        # Set current selection
        sel = RelationshipCurrentSelection(
            relationship_id="rel_1",
            object_id="obj_gen_1",
            generation_id="gen_1",
            selection_generation=1,
            operation_id="op_pub_2",
            changed_at="2026-09-04T00:01:00Z",
        )
        self.repo.set_current_selection(sel)

        curr = self.repo.get_current_selection("rel_1")
        self.assertIsNotNone(curr)
        self.assertEqual(curr.object_id, "obj_gen_1")
        self.assertEqual(curr.selection_generation, 1)


if __name__ == "__main__":
    unittest.main()
