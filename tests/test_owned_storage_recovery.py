"""Unit and crash-recovery tests for owned storage authority (User Story 3)."""

import hashlib
import io
import os
import random
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from sandbox.owned_storage.cleanup import CleanupExecutionError, OwnedStorageCleanupManager
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityOwnedObject,
    AuthorityPolicy,
    CleanupIntent,
    CleanupPhase,
    ObjectKind,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    OperationType,
    PolicyMode,
)
from sandbox.owned_storage.protocol import (
    StorageProtocolError,
    canonical_json_dumps,
    compute_request_digest,
)
from sandbox.owned_storage.repository import (
    StorageAuthorityRepository,
    StorageRepositoryConflictError,
)
from sandbox.owned_storage.service import OwnedStorageService, OwnedStorageServiceError


class TestOwnedStorageRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.service = OwnedStorageService(self.storage_root, self.repo)
        self.cleanup_manager = OwnedStorageCleanupManager(self.storage_root, self.repo)

        self.remote_id = "rem_rec_1"
        self.project_id = "proj_rec_1"
        self.rel_id = "rel_rec_1"
        self.ws_id = "ws_rec_1"

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

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_sample_archive(self) -> tuple[bytes, str, int, int]:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            data = b"sample archive content"
            ti = tarfile.TarInfo("file.txt")
            ti.size = len(data)
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))
        raw = buf.getvalue()
        manifest_digest = f"sha256:{hashlib.sha256(b'file.txt:1:sample').hexdigest()}"
        return raw, manifest_digest, 1, len(data)


    # --- T024: Unit tests for canonical request hashing, replay idempotency, conflict refusal ---

    def test_canonical_request_hashing(self):
        req1 = {
            "protocol": "owned-storage-authority-v1",
            "operation": "publish",
            "request_id": "req_h_1",
            "remote_identity": self.remote_id,
            "project_identity": self.project_id,
            "authorization": {"token": "tok_1"},
            "qualification": None,
            "input": {"generation_id": "gen_1", "bytes": 100},
        }
        # Reordered keys in dictionary
        req2 = {
            "input": {"bytes": 100, "generation_id": "gen_1"},
            "protocol": "owned-storage-authority-v1",
            "project_identity": self.project_id,
            "authorization": {"token": "tok_1"},
            "remote_identity": self.remote_id,
            "operation": "publish",
            "qualification": None,
            "request_id": "req_h_1",
        }
        d1 = compute_request_digest(req1)
        d2 = compute_request_digest(req2)
        self.assertEqual(d1, d2)

        # Changed field produces different digest
        req3 = dict(req1)
        req3["input"] = {"generation_id": "gen_2", "bytes": 100}
        d3 = compute_request_digest(req3)
        self.assertNotEqual(d1, d3)

        # Floats are rejected by canonical encoding
        req_float = dict(req1)
        req_float["input"] = {"bytes": 100.5}
        with self.assertRaises(StorageProtocolError):
            compute_request_digest(req_float)

    def test_publication_replay_idempotency(self):
        payload, manifest_digest, file_count, byte_count = self._create_sample_archive()
        archive_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        req_id = "req_pub_idemp_1"
        req_digest = "sha256:pub_idemp_digest_1"

        # First publication
        res1 = self.service.publish_generation(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id=req_id,
            request_digest=req_digest,
            authorization_id="auth_1",
            controller_epoch="epoch_1",
            sequence=1,
            caller_identity_digest="sha256:caller",
            relationship_id=self.rel_id,
            workspace_id=self.ws_id,
            generation_id="gen_pub_idemp_1",
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_digest,
            file_count=file_count,
            byte_count=byte_count,
            stream=io.BytesIO(payload),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["status"], "accepted")
        self.assertFalse(res1.get("replay", False))

        # Replay exact same request
        res2 = self.service.publish_generation(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id=req_id,
            request_digest=req_digest,
            authorization_id="auth_1",
            controller_epoch="epoch_1",
            sequence=1,
            caller_identity_digest="sha256:caller",
            relationship_id=self.rel_id,
            workspace_id=self.ws_id,
            generation_id="gen_pub_idemp_1",
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_digest,
            file_count=file_count,
            byte_count=byte_count,
            stream=io.BytesIO(payload),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["status"], "accepted")
        self.assertTrue(res2.get("replay"))
        self.assertEqual(res1["object"]["id"], res2["object"]["id"])

    def test_publication_conflicting_request_refusal(self):
        payload, manifest_digest, file_count, byte_count = self._create_sample_archive()
        archive_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        req_id = "req_pub_conflict_1"
        req_digest1 = "sha256:pub_conflict_digest_1"
        req_digest2 = "sha256:pub_conflict_digest_2"

        # First publication
        self.service.publish_generation(
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            request_id=req_id,
            request_digest=req_digest1,
            authorization_id="auth_1",
            controller_epoch="epoch_1",
            sequence=1,
            caller_identity_digest="sha256:caller",
            relationship_id=self.rel_id,
            workspace_id=self.ws_id,
            generation_id="gen_pub_conf_1",
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_digest,
            file_count=file_count,
            byte_count=byte_count,
            stream=io.BytesIO(payload),
            promotion_id="prom_1",
            authority_binding_id="bind_1",
        )

        # Replay with same request_id but different digest
        with self.assertRaises(OwnedStorageServiceError) as ctx:
            self.service.publish_generation(
                remote_identity=self.remote_id,
                project_identity=self.project_id,
                request_id=req_id,
                request_digest=req_digest2,
                authorization_id="auth_1",
                controller_epoch="epoch_1",
                sequence=1,
                caller_identity_digest="sha256:caller",
                relationship_id=self.rel_id,
                workspace_id=self.ws_id,
                generation_id="gen_pub_conf_2",
                manifest_digest=manifest_digest,
                archive_manifest_digest=archive_digest,
                file_count=file_count,
                byte_count=byte_count,
                stream=io.BytesIO(payload),
                promotion_id="prom_1",
                authority_binding_id="bind_1",
            )
        self.assertEqual(ctx.exception.code, "request_id_conflict")

    def test_cleanup_replay_idempotency_and_conflict_refusal(self):
        # Create an object to clean up
        obj_dir = self.storage_root / "objects" / self.project_id / "workspaces" / "mat_clean_rec_1"
        obj_dir.mkdir(parents=True, exist_ok=True)
        (obj_dir / "data.bin").write_bytes(b"1234567890")
        st = os.stat(obj_dir)

        obj = AuthorityOwnedObject(
            object_id="mat_clean_rec_1",
            object_kind=ObjectKind.CI_MATERIALIZATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id=None,
            workspace_id=self.ws_id,
            job_id="job_rec_1",
            parent_object_id=None,
            created_by_operation_id="op_rec_init",
            lifecycle=ObjectLifecycle.ELIGIBLE,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id="prom_1",
            evidence_id="ev_1",
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret",
            content_evidence={"materialization_id": "mat_clean_rec_1"},
            filesystem_identity={"inode": st.st_ino, "device": st.st_dev},
            known_bytes=10,
            created_at="2026-09-04T00:00:00Z",
            accepted_at="2026-09-04T00:01:00Z",
            removed_at=None,
        )
        self.repo.save_object(obj)

        req_clean_id = "req_clean_rec_1"
        res1 = self.cleanup_manager.cleanup_object(
            preview_id="prev_rec_1",
            object_id=obj.object_id,
            request_id=req_clean_id,
            confirm=True,
            expected_object_evidence_digest="sha256:obj_ev",
            expected_reference_digest="sha256:ref_ev",
        )
        self.assertTrue(res1["ok"])
        self.assertEqual(res1["status"], "completed")
        self.assertFalse(obj_dir.exists())

        # Exact replay of cleanup
        res2 = self.cleanup_manager.cleanup_object(
            preview_id="prev_rec_1",
            object_id=obj.object_id,
            request_id=req_clean_id,
            confirm=True,
            expected_object_evidence_digest="sha256:obj_ev",
            expected_reference_digest="sha256:ref_ev",
        )
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["status"], "already_completed")

        # Conflict replay: same request_id but different object_id
        with self.assertRaises(CleanupExecutionError) as ctx:
            self.cleanup_manager.cleanup_object(
                preview_id="prev_rec_1",
                object_id="different_object",
                request_id=req_clean_id,
                confirm=True,
                expected_object_evidence_digest="sha256:obj_ev",
                expected_reference_digest="sha256:ref_ev",
            )
        self.assertEqual(ctx.exception.code, "request_id_conflict")

    # --- T025: 100-trial simulated crash and interruption recovery suite ---

    def test_100_trial_crash_and_interruption_recovery(self):
        random.seed(42)

        for trial in range(100):
            op_kind = "publish" if trial % 2 == 0 else "cleanup"

            if op_kind == "publish":
                # Create leftover staging directory to simulate crash during staging
                staged_op_id = f"op_crash_pub_{trial}"
                leftover_dir = self.storage_root / "staging" / staged_op_id
                leftover_dir.mkdir(parents=True, exist_ok=True)
                (leftover_dir / "partial_file.tmp").write_text("unfinished data")

                # Restart service: reconcile_startup should clean up uncommitted staging
                res = self.service.reconcile_startup()
                self.assertFalse(leftover_dir.exists())
                self.assertGreaterEqual(res.get("reconciled_staging_count", 0), 1)

            else:
                # Create an object and simulate crash during quarantine
                obj_id = f"mat_crash_obj_{trial}"
                obj_dir = self.storage_root / "objects" / self.project_id / "workspaces" / obj_id
                obj_dir.mkdir(parents=True, exist_ok=True)
                (obj_dir / "file.bin").write_bytes(b"sample" * 5)
                st = os.stat(obj_dir)

                obj = AuthorityOwnedObject(
                    object_id=obj_id,
                    object_kind=ObjectKind.CI_MATERIALIZATION,
                    remote_identity=self.remote_id,
                    project_identity=self.project_id,
                    relationship_id=None,
                    workspace_id=f"ws_{trial}",
                    job_id=f"job_{trial}",
                    parent_object_id=None,
                    created_by_operation_id=f"op_init_{trial}",
                    lifecycle=ObjectLifecycle.ELIGIBLE,
                    policy_id="pol_1",
                    policy_generation=1,
                    qualification_admission_id=None,
                    evidence_candidate_id=None,
                    promotion_id="prom_1",
                    evidence_id="ev_1",
                    authority_binding_id="bind_1",
                    retention_policy_digest="sha256:ret",
                    content_evidence={"materialization_id": obj_id},
                    filesystem_identity={"inode": st.st_ino, "device": st.st_dev},
                    known_bytes=30,
                    created_at="2026-09-04T00:00:00Z",
                    accepted_at="2026-09-04T00:01:00Z",
                    removed_at=None,
                )
                self.repo.save_object(obj)

                # Simulate crash after quarantine rename
                clean_id = f"clean_crash_{trial}"
                quarantine_target_dir = self.storage_root / "quarantine" / clean_id / "target"
                quarantine_target_dir.mkdir(parents=True, exist_ok=True)
                (quarantine_target_dir / "leftover.bin").write_bytes(b"data")

                intent = CleanupIntent(
                    cleanup_id=clean_id,
                    operation_id=f"op_clean_crash_{trial}",
                    preview_id=f"prev_{trial}",
                    object_id=obj_id,
                    expected_object_evidence_digest="sha256:ev",
                    expected_reference_digest="sha256:ref",
                    final_entry_evidence_digest=None,
                    phase=CleanupPhase.QUARANTINED,
                    outcome=None,
                    reason_code=None,
                    estimated_bytes=30,
                    observed_reclaimed_bytes=None,
                    job_result_digest_before=None,
                    job_result_digest_after=None,
                    created_at="2026-09-04T00:00:00Z",
                    updated_at="2026-09-04T00:00:00Z",
                    completed_at=None,
                )
                self.repo.save_cleanup_intent(intent)

                # Run reconcile_startup
                res = self.service.reconcile_startup()
                self.assertFalse((self.storage_root / "quarantine" / clean_id).exists())
                self.assertGreaterEqual(res.get("reconciled_quarantine_count", 0), 1)

                updated_intent = self.repo.get_cleanup_intent(clean_id)
                self.assertEqual(updated_intent.phase, CleanupPhase.TERMINAL)


if __name__ == "__main__":
    unittest.main()
