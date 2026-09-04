"""Integration tests for application-level owned storage service and policy routing."""

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from sandbox.application.owned_storage_service import (
    OwnedStorageApplicationError,
    OwnedStorageApplicationService,
)
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityPolicy,
    PolicyMode,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import OwnedStorageService


class TestOwnedStorageApplication(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.authority_service = OwnedStorageService(self.storage_root, self.repo)

        self.remote_id = "rem_local"
        self.project_id = "proj_test"
        self.app_service = OwnedStorageApplicationService(
            authority_service=self.authority_service,
            repository=self.repo,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_legacy_policy_refuses_owned_storage_publish(self):
        # By default policy is legacy or absent
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.publish(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                request_id="req_app_1",
                relationship_id="rel_1",
                workspace_id="ws_1",
                generation_id="gen_1",
                manifest_digest="sha256:manifest",
                archive_manifest_digest="sha256:archive",
                file_count=1,
                byte_count=10,
                stream=io.BytesIO(b"data"),
            )
        self.assertIn("policy_not_future", str(ctx.exception).lower())

    def test_future_policy_with_active_binding_succeeds(self):
        # Enable future policy and active binding
        self.repo.save_policy(
            AuthorityPolicy(
                policy_id="pol_1",
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                mode=PolicyMode.FUTURE,
                effective_generation=1,
                changed_by="caller_auth",
                request_id="req_pol_1",
                request_digest="sha256:pol",
                admission_basis={"binding_id": "bind_1"},
                changed_at="2026-09-04T00:00:00Z",
            )
        )
        self.repo.save_adoption_binding(
            AuthorityAdoptionBinding(
                authority_binding_id="bind_1",
                binding_generation=1,
                remote_identity=self.remote_id,
                project_identity=self.project_id,
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
                expires_at="2026-09-04T12:00:00Z",
                phase=AdoptionBindingPhase.ACTIVE,
            )
        )

        empty_digest = f"sha256:{hashlib.sha256(b'').hexdigest()}"
        res = self.app_service.publish(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id="req_app_2",
            relationship_id="rel_1",
            workspace_id="ws_1",
            generation_id="gen_1",
            manifest_digest="sha256:manifest",
            archive_manifest_digest=empty_digest,
            file_count=0,
            byte_count=0,
            stream=io.BytesIO(b""),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "accepted")

    def _setup_future_policy_and_binding(self):
        self.repo.save_policy(
            AuthorityPolicy(
                policy_id="pol_1",
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                mode=PolicyMode.FUTURE,
                effective_generation=1,
                changed_by="caller_auth",
                request_id="req_pol_1",
                request_digest="sha256:pol",
                admission_basis={"binding_id": "bind_1"},
                changed_at="2026-09-04T00:00:00Z",
            )
        )
        self.repo.save_adoption_binding(
            AuthorityAdoptionBinding(
                authority_binding_id="bind_1",
                binding_generation=1,
                remote_identity=self.remote_id,
                project_identity=self.project_id,
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
                expires_at="2026-09-04T12:00:00Z",
                phase=AdoptionBindingPhase.ACTIVE,
            )
        )

    def test_preview_projections_and_sum_known_eligible_bytes(self):
        from sandbox.owned_storage.models import (
            AuthorityOwnedObject,
            CandidateDecision,
            LeaseState,
            MaterializationLease,
            ObjectKind,
            ObjectLifecycle,
            RelationshipCurrentSelection,
        )

        self._setup_future_policy_and_binding()

        # obj1: superseded generation, known bytes = 1000 -> ELIGIBLE
        # make sure its directory exists so reclaim can also touch it
        obj1_dir = self.storage_root / "objects" / self.project_id / "rel_1" / "gen_1"
        obj1_dir.mkdir(parents=True, exist_ok=True)
        (obj1_dir / "file.txt").write_bytes(b"x" * 1000)
        stat1 = self.authority_service.adapter.stat_identity(obj1_dir)

        obj1 = AuthorityOwnedObject(
            object_id="obj_gen_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id="rel_1",
            workspace_id="ws_1",
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_1",
            lifecycle=ObjectLifecycle.SUPERSEDED,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret1",
            content_evidence={"generation_id": "gen_1"},
            filesystem_identity=stat1,
            known_bytes=1000,
            created_at="2026-09-04T01:00:00Z",
            accepted_at="2026-09-04T01:01:00Z",
        )
        self.repo.save_object(obj1)

        # obj2: current generation, known bytes = 2000 -> PROTECTED
        obj2 = AuthorityOwnedObject(
            object_id="obj_gen_2",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id="rel_1",
            workspace_id="ws_1",
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_2",
            lifecycle=ObjectLifecycle.ACCEPTED,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret2",
            content_evidence={"generation_id": "gen_2"},
            filesystem_identity={"device": 1, "inode": 2},
            known_bytes=2000,
            created_at="2026-09-04T02:00:00Z",
            accepted_at="2026-09-04T02:01:00Z",
        )
        self.repo.save_object(obj2)
        # Mark obj2 as current selection
        self.repo.set_current_selection(
            RelationshipCurrentSelection(
                relationship_id="rel_1",
                object_id="obj_gen_2",
                generation_id="gen_2",
                selection_generation=2,
                operation_id="op_2",
                changed_at="2026-09-04T02:01:00Z",
            )
        )

        # obj3: ci materialization, terminal unreferenced, known_bytes=None -> ELIGIBLE, but None bytes
        obj3 = AuthorityOwnedObject(
            object_id="obj_mat_3",
            object_kind=ObjectKind.CI_MATERIALIZATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id=None,
            workspace_id="ws_3",
            job_id="job_3",
            parent_object_id="obj_gen_1",
            created_by_operation_id="op_3",
            lifecycle=ObjectLifecycle.SUPERSEDED,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret3",
            content_evidence={},
            filesystem_identity={"device": 1, "inode": 3},
            known_bytes=None,
            created_at="2026-09-04T03:00:00Z",
            accepted_at="2026-09-04T03:01:00Z",
        )
        self.repo.save_object(obj3)

        # obj4: ci materialization with active lease, known_bytes=5000 -> PROTECTED
        obj4 = AuthorityOwnedObject(
            object_id="obj_mat_4",
            object_kind=ObjectKind.CI_MATERIALIZATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id=None,
            workspace_id="ws_4",
            job_id="job_4",
            parent_object_id="obj_gen_2",
            created_by_operation_id="op_4",
            lifecycle=ObjectLifecycle.ACTIVE,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret4",
            content_evidence={},
            filesystem_identity={"device": 1, "inode": 4},
            known_bytes=5000,
            created_at="2026-09-04T04:00:00Z",
            accepted_at="2026-09-04T04:01:00Z",
        )
        self.repo.save_object(obj4)
        self.repo.save_lease(
            MaterializationLease(
                lease_id="lease_4",
                object_id="obj_mat_4",
                job_id="job_4",
                workspace_id="ws_4",
                lifecycle_generation=1,
                mount_identity_digest="sha256:mount4",
                state=LeaseState.ACTIVE,
                opened_at="2026-09-04T04:00:00Z",
                heartbeat_at="2026-09-04T04:01:00Z",
                expires_at="2026-09-04T05:00:00Z",
            )
        )

        preview = self.app_service.generate_preview(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
        )

        self.assertTrue(preview["ok"])
        self.assertIn("preview_id", preview)
        self.assertTrue(preview["complete"])
        # Only obj1 (1000) is eligible with known bytes. obj3 is eligible but known_bytes is None.
        # obj2 (2000) and obj4 (5000) are protected.
        self.assertEqual(preview["estimated_reclaimable_bytes"], 1000)

        candidates_by_id = {c["object_id"]: c for c in preview["candidates"]}
        self.assertEqual(candidates_by_id["obj_gen_1"]["decision"], "eligible")
        self.assertEqual(candidates_by_id["obj_gen_2"]["decision"], "protected")
        self.assertEqual(candidates_by_id["obj_gen_2"]["reason_code"], "reference_active")
        self.assertEqual(candidates_by_id["obj_mat_3"]["decision"], "eligible")
        self.assertIsNone(candidates_by_id["obj_mat_3"]["estimated_bytes"])
        self.assertEqual(candidates_by_id["obj_mat_4"]["decision"], "protected")
        self.assertEqual(candidates_by_id["obj_mat_4"]["reason_code"], "workspace_lease_active")

    def test_preview_bounded_pagination_and_max_limit_500(self):
        from sandbox.owned_storage.models import AuthorityOwnedObject, ObjectKind, ObjectLifecycle

        self._setup_future_policy_and_binding()

        for i in range(550):
            obj = AuthorityOwnedObject(
                object_id=f"obj_bulk_{i:04d}",
                object_kind=ObjectKind.SYNC_GENERATION,
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                relationship_id=None,
                workspace_id=None,
                job_id=None,
                parent_object_id=None,
                created_by_operation_id=f"op_{i}",
                lifecycle=ObjectLifecycle.SUPERSEDED,
                policy_id="pol_1",
                policy_generation=1,
                qualification_admission_id=None,
                evidence_candidate_id=None,
                promotion_id=None,
                evidence_id=None,
                authority_binding_id="bind_1",
                retention_policy_digest="sha256:bulk",
                content_evidence={},
                filesystem_identity={},
                known_bytes=10,
                created_at=f"2026-09-04T00:00:{i%60:02d}Z",
            )
            self.repo.save_object(obj)

        # Query status with limit 1000 -> should be clamped to 500
        page1 = self.app_service.get_status(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            limit=1000,
        )
        self.assertTrue(page1["ok"])
        self.assertEqual(len(page1["objects"]), 500)
        self.assertIsNotNone(page1["cursor"])

        # Query next page
        page2 = self.app_service.get_status(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            limit=500,
            cursor=page1["cursor"],
        )
        self.assertTrue(page2["ok"])
        self.assertEqual(len(page2["objects"]), 50)
        self.assertIsNone(page2["cursor"])

    def test_preview_15_minute_expiry_and_reclaim_rejection(self):
        import datetime
        from sandbox.owned_storage.models import (
            AuthorityOwnedObject,
            CandidateDecision,
            ObjectKind,
            ObjectLifecycle,
            PreviewCandidate,
            ReclamationPreview,
        )

        self._setup_future_policy_and_binding()

        obj = AuthorityOwnedObject(
            object_id="obj_exp_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id=None,
            workspace_id=None,
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_exp",
            lifecycle=ObjectLifecycle.SUPERSEDED,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:exp",
            content_evidence={},
            filesystem_identity={},
            known_bytes=100,
            created_at="2026-09-04T00:00:00Z",
        )
        self.repo.save_object(obj)

        # Generate preview via service and inspect expires_at
        preview = self.app_service.generate_preview(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
        )
        created_dt = datetime.datetime.fromisoformat(preview["created_at"])
        expires_dt = datetime.datetime.fromisoformat(preview["expires_at"])
        # Must be at most 15 minutes
        self.assertLessEqual((expires_dt - created_dt).total_seconds(), 900.0)

        # Now save an expired preview directly
        expired_preview = ReclamationPreview(
            preview_id="prev_expired_1",
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            inventory_generation=1,
            policy_generation=1,
            candidate_digest="sha256:cand",
            candidates=[
                PreviewCandidate(
                    object_id="obj_exp_1",
                    object_kind=ObjectKind.SYNC_GENERATION,
                    lifecycle=ObjectLifecycle.SUPERSEDED,
                    decision=CandidateDecision.ELIGIBLE,
                    reason_code="superseded_unreferenced",
                    estimated_bytes=100,
                    object_evidence_digest="sha256:ev",
                    reference_snapshot_digest="sha256:ref",
                )
            ],
            estimated_reclaimable_bytes=100,
            complete=True,
            created_at="2026-09-01T00:00:00Z",
            expires_at="2026-09-01T00:15:00Z",
        )
        self.repo.save_preview(expired_preview)

        # Attempt reclaim with expired preview -> raises preview_expired
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.reclaim(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                preview_id="prev_expired_1",
                object_id="obj_exp_1",
                request_id="req_reclaim_exp",
                confirm=True,
            )
        self.assertIn("preview_expired", str(ctx.exception).lower())

    def test_reclaim_requires_confirm_and_eligible_preview_candidate(self):
        from sandbox.owned_storage.models import (
            AuthorityOwnedObject,
            ObjectKind,
            ObjectLifecycle,
            RelationshipCurrentSelection,
        )

        self._setup_future_policy_and_binding()

        obj_curr = AuthorityOwnedObject(
            object_id="obj_prot_1",
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id="rel_prot",
            workspace_id=None,
            job_id=None,
            parent_object_id=None,
            created_by_operation_id="op_prot",
            lifecycle=ObjectLifecycle.ACCEPTED,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            evidence_id=None,
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:prot",
            content_evidence={},
            filesystem_identity={},
            known_bytes=200,
            created_at="2026-09-04T00:00:00Z",
        )
        self.repo.save_object(obj_curr)
        self.repo.set_current_selection(
            RelationshipCurrentSelection(
                relationship_id="rel_prot",
                object_id="obj_prot_1",
                generation_id="gen_prot",
                selection_generation=1,
                operation_id="op_prot",
                changed_at="2026-09-04T00:00:00Z",
            )
        )

        preview = self.app_service.generate_preview(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
        )
        prev_id = preview["preview_id"]

        # 1. Missing confirm
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.reclaim(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                preview_id=prev_id,
                object_id="obj_prot_1",
                request_id="req_rec_1",
                confirm=False,
            )
        self.assertIn("request_invalid", str(ctx.exception).lower())

        # 2. Object not in preview
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.reclaim(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                preview_id=prev_id,
                object_id="obj_nonexistent",
                request_id="req_rec_2",
                confirm=True,
            )
        self.assertIn("object_not_previewed", str(ctx.exception).lower())

        # 3. Object was protected in preview
        with self.assertRaises(OwnedStorageApplicationError) as ctx:
            self.app_service.reclaim(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                preview_id=prev_id,
                object_id="obj_prot_1",
                request_id="req_rec_3",
                confirm=True,
            )
        self.assertTrue(
            "reference_active" in str(ctx.exception).lower()
            or "retention_active" in str(ctx.exception).lower()
        )


if __name__ == "__main__":
    unittest.main()
