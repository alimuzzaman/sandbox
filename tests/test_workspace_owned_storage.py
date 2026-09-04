"""Unit and contract tests for identity-bound workspace cleanup under owned storage."""

import os
import tempfile
import unittest
from pathlib import Path

from sandbox.owned_storage.cleanup import OwnedStorageCleanupManager, CleanupExecutionError
from sandbox.owned_storage.models import (
    AuthorityOwnedObject,
    CleanupOutcome,
    CleanupPhase,
    LeaseState,
    MaterializationLease,
    ObjectKind,
    ObjectLifecycle,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository


class TestWorkspaceOwnedStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.storage_root = self.root / "storage"
        self.db_path = self.storage_root / "authority.db"
        self.repo = StorageAuthorityRepository(self.db_path)
        self.cleanup_manager = OwnedStorageCleanupManager(self.storage_root, self.repo)

        self.remote_id = "rem_1"
        self.project_id = "proj_1"
        self.workspace_id = "ws_ci_1"
        self.job_id = "job_ci_1"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_materialization_object(self, object_id: str, file_count: int = 3, byte_count: int = 300) -> tuple[AuthorityOwnedObject, Path]:
        obj_dir = self.storage_root / "objects" / self.project_id / "workspaces" / object_id
        obj_dir.mkdir(parents=True, exist_ok=True)
        work_dir = obj_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        for i in range(file_count):
            (work_dir / f"file_{i}.txt").write_text("x" * (byte_count // file_count))

        st = os.stat(obj_dir)
        filesystem_ident = {
            "inode": st.st_ino,
            "device": st.st_dev,
            "mode": st.st_mode,
            "uid": st.st_uid,
            "gid": st.st_gid,
        }

        obj = AuthorityOwnedObject(
            object_id=object_id,
            object_kind=ObjectKind.CI_MATERIALIZATION,
            remote_identity=self.remote_id,
            project_identity=self.project_id,
            relationship_id=None,
            workspace_id=self.workspace_id,
            job_id=self.job_id,
            parent_object_id=None,
            created_by_operation_id="op_mat_1",
            lifecycle=ObjectLifecycle.ELIGIBLE,
            policy_id="pol_1",
            policy_generation=1,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id="prom_1",
            evidence_id="ev_1",
            authority_binding_id="bind_1",
            retention_policy_digest="sha256:ret",
            content_evidence={"materialization_id": object_id},
            filesystem_identity=filesystem_ident,
            known_bytes=byte_count,
            created_at="2026-09-04T00:00:00Z",
            accepted_at="2026-09-04T00:01:00Z",
            removed_at=None,
        )
        self.repo.save_object(obj)
        return obj, obj_dir

    def test_cleanup_eligible_materialization_success(self):
        obj, obj_dir = self._create_materialization_object("mat_obj_1", file_count=3, byte_count=300)
        self.assertTrue(obj_dir.exists())

        result = self.cleanup_manager.cleanup_object(
            preview_id="prev_1",
            object_id=obj.object_id,
            request_id="req_clean_1",
            confirm=True,
            expected_object_evidence_digest="sha256:obj_ev",
            expected_reference_digest="sha256:ref_ev",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(obj_dir.exists())
        self.assertGreaterEqual(result["observed_reclaimed_bytes"], 300)

        # Object lifecycle in repository is REMOVED
        updated_obj = self.repo.get_object(obj.object_id)
        self.assertEqual(updated_obj.lifecycle, ObjectLifecycle.REMOVED)
        self.assertIsNotNone(updated_obj.removed_at)

    def test_cleanup_refused_when_active_lease_exists(self):
        obj, obj_dir = self._create_materialization_object("mat_obj_active", file_count=2, byte_count=100)

        # Record active lease
        lease = MaterializationLease(
            lease_id="lease_1",
            object_id=obj.object_id,
            job_id=self.job_id,
            workspace_id=self.workspace_id,
            lifecycle_generation=1,
            mount_identity_digest="sha256:mount",
            state=LeaseState.ACTIVE,
            opened_at="2026-09-04T00:00:00Z",
            heartbeat_at="2026-09-04T00:05:00Z",
            expires_at="2026-09-04T01:00:00Z",
            closed_at=None,
        )
        self.repo.save_lease(lease)

        with self.assertRaises(CleanupExecutionError) as ctx:
            self.cleanup_manager.cleanup_object(
                preview_id="prev_1",
                object_id=obj.object_id,
                request_id="req_clean_active",
                confirm=True,
                expected_object_evidence_digest="sha256:obj_ev",
                expected_reference_digest="sha256:ref_ev",
            )
        self.assertEqual(ctx.exception.code, "workspace_lease_active")
        self.assertTrue(obj_dir.exists())

    def test_cleanup_refused_when_filesystem_identity_drifts(self):
        obj, obj_dir = self._create_materialization_object("mat_obj_drift", file_count=2, byte_count=100)

        # Simulate replacement: remove and recreate directory with new inode
        for f in (obj_dir / "work").iterdir():
            f.unlink()
        (obj_dir / "work").rmdir()
        obj_dir.rmdir()
        obj_dir.mkdir(parents=True)

        # If inode differs, cleanup should fail-closed
        new_st = os.stat(obj_dir)
        if new_st.st_ino != obj.filesystem_identity["inode"]:
            with self.assertRaises(CleanupExecutionError) as ctx:
                self.cleanup_manager.cleanup_object(
                    preview_id="prev_1",
                    object_id=obj.object_id,
                    request_id="req_clean_drift",
                    confirm=True,
                    expected_object_evidence_digest="sha256:obj_ev",
                    expected_reference_digest="sha256:ref_ev",
                )
            self.assertIn("object_identity_drift", ctx.exception.code)

    def test_workspace_service_release_terminal_job_owned_storage(self):
        import hashlib
        from sandbox.application.workspace_service import (
            WorkspaceRepository,
            WorkspaceService,
            _digest_payload,
            _filesystem_identity,
        )
        from sandbox.jobs.models import JobSubmission, Lifecycle, SourceIdentity
        from sandbox.jobs.registry import JobRepository, read_resource_index

        deploy_root = self.root / "deploy"
        deploy_root.mkdir(parents=True, exist_ok=True)
        ws_repo_dir = self.root / "workspaces"
        ws_repo_dir.mkdir(parents=True, exist_ok=True)
        job_repo_path = self.root / "jobs.sqlite3"
        job_repo = JobRepository(job_repo_path)
        ws_repo = WorkspaceRepository(
            ws_repo_dir / "index.sqlite3",
            ws_repo_dir / "legacy",
            job_index_reader=lambda: read_resource_index(job_repo.path),
        )
        ws_service = WorkspaceService(
            None,
            repository=ws_repo,
            deployment_root=deploy_root,
            owned_storage_cleanup_manager=self.cleanup_manager,
            cleanup_reference_observer=lambda _c, _r: {"containers": 0, "mounts": 0},
        )

        obj, obj_dir = self._create_materialization_object("mat_ws_svc_1", file_count=3, byte_count=300)
        checkout_path = deploy_root / "ws_ci_1"
        checkout_path.mkdir(parents=True, exist_ok=True)
        (checkout_path / "file.txt").write_text("dummy")

        checkout_str = str(checkout_path.resolve())
        checkout_digest = "sha256:" + hashlib.sha256(checkout_str.encode()).hexdigest()

        auth_dict = {
            "owner": "controller-ci-materialization",
            "job_kind": "ci",
            "checkout_locator": checkout_str,
            "checkout_identity": _filesystem_identity(checkout_path),
            "source_checkout_locator": checkout_str,
            "source_checkout_identity": _filesystem_identity(checkout_path),
            "workspace_label": "ci_ws_lbl",
            "generation": 1,
            "artifact_locator": str(obj_dir / "work" / "file_0.txt"),
            "artifact_digest": "sha256:dummy",
            "artifact_size_bytes": 100,
        }
        auth_dict["digest"] = _digest_payload(auth_dict)

        rec, _ = ws_service._register(
            project_identity=self.project_id,
            label="ci_ws_lbl",
            namespace="proj_1",
            checkout_locator=checkout_str,
            source="ci-materialization",
            deployment_proof={
                "checkout_locator": checkout_str,
                "checkout_locator_digest": checkout_digest,
                "source_checkout_locator": checkout_str,
                "source_checkout_locator_digest": checkout_digest,
                "ci_cleanup_authority": auth_dict,
                "owned_storage_object_id": obj.object_id,
            },
            mode="isolated",
        )
        ws_repo.mark_lifecycle(rec.workspace_id, "ready", status="ready")

        submission = JobSubmission(
            "ci", checkout_str, self.project_id, "local", "ci_ws_lbl",
            ("/bin/true",), 30, SourceIdentity("src_1"),
            request_id="req_ws_term_1",
            workspace_mode="isolated",
            cleanup_policy="ephemeral",
        )
        row, _ = job_repo.accept(
            submission,
            workspace_id=rec.workspace_id,
            workspace_authority_digest=auth_dict["digest"],
        )
        job_repo.transition(row["job_id"], Lifecycle.RUNNING)
        job_repo.transition(
            row["job_id"], Lifecycle.SUCCEEDED,
            exit_code=0, termination_reason="normal", integrity_sha256="0" * 64,
        )
        job = job_repo.get(row["job_id"])

        result = ws_service.release_terminal_job(job, job_repo)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "released")
        self.assertGreaterEqual(result["observed_reclaimed_bytes"], 300)
        self.assertFalse(obj_dir.exists())

        updated_ws = ws_repo.get(rec.workspace_id)
        self.assertEqual(updated_ws.lifecycle, "destroyed")
        updated_job = job_repo.get(job["job_id"])
        self.assertEqual(updated_job["cleanup_state"], "completed")
        self.assertEqual(updated_job["lifecycle"], "succeeded")
        self.assertEqual(updated_job["exit_code"], 0)
        job_repo.close()

    def test_workspace_service_release_refused_on_active_lease(self):
        import hashlib
        from sandbox.application.workspace_service import (
            WorkspaceRepository,
            WorkspaceService,
            _digest_payload,
            _filesystem_identity,
        )
        from sandbox.jobs.models import JobSubmission, Lifecycle, SourceIdentity
        from sandbox.jobs.registry import JobRepository, read_resource_index

        deploy_root = self.root / "deploy"
        deploy_root.mkdir(parents=True, exist_ok=True)
        ws_repo_dir = self.root / "workspaces"
        ws_repo_dir.mkdir(parents=True, exist_ok=True)
        job_repo_path = self.root / "jobs.sqlite3"
        job_repo = JobRepository(job_repo_path)
        ws_repo = WorkspaceRepository(
            ws_repo_dir / "index.sqlite3",
            ws_repo_dir / "legacy",
            job_index_reader=lambda: read_resource_index(job_repo.path),
        )
        ws_service = WorkspaceService(
            None,
            repository=ws_repo,
            deployment_root=deploy_root,
            owned_storage_cleanup_manager=self.cleanup_manager,
            cleanup_reference_observer=lambda _c, _r: {"containers": 0, "mounts": 0},
        )

        obj, obj_dir = self._create_materialization_object("mat_ws_svc_active", file_count=2, byte_count=100)

        # Active lease
        lease = MaterializationLease(
            lease_id="lease_active_svc",
            object_id=obj.object_id,
            job_id=self.job_id,
            workspace_id=self.workspace_id,
            lifecycle_generation=1,
            mount_identity_digest="sha256:mount",
            state=LeaseState.ACTIVE,
            opened_at="2026-09-04T00:00:00Z",
            heartbeat_at="2026-09-04T00:05:00Z",
            expires_at="2026-09-04T01:00:00Z",
            closed_at=None,
        )
        self.repo.save_lease(lease)

        checkout_path = deploy_root / "ws_ci_active"
        checkout_path.mkdir(parents=True, exist_ok=True)
        (checkout_path / "file.txt").write_text("dummy")

        checkout_str = str(checkout_path.resolve())
        checkout_digest = "sha256:" + hashlib.sha256(checkout_str.encode()).hexdigest()

        auth_dict = {
            "owner": "controller-ci-materialization",
            "job_kind": "ci",
            "checkout_locator": checkout_str,
            "checkout_identity": _filesystem_identity(checkout_path),
            "source_checkout_locator": checkout_str,
            "source_checkout_identity": _filesystem_identity(checkout_path),
            "workspace_label": "ci_ws_active_lbl",
            "generation": 1,
            "artifact_locator": str(obj_dir / "work" / "file_0.txt"),
            "artifact_digest": "sha256:dummy",
            "artifact_size_bytes": 100,
        }
        auth_dict["digest"] = _digest_payload(auth_dict)

        rec, _ = ws_service._register(
            project_identity=self.project_id,
            label="ci_ws_active_lbl",
            namespace="proj_1",
            checkout_locator=checkout_str,
            source="ci-materialization",
            deployment_proof={
                "checkout_locator": checkout_str,
                "checkout_locator_digest": checkout_digest,
                "source_checkout_locator": checkout_str,
                "source_checkout_locator_digest": checkout_digest,
                "ci_cleanup_authority": auth_dict,
                "owned_storage_object_id": obj.object_id,
            },
            mode="isolated",
        )
        ws_repo.mark_lifecycle(rec.workspace_id, "ready", status="ready")

        submission = JobSubmission(
            "ci", checkout_str, self.project_id, "local", "ci_ws_active_lbl",
            ("/bin/true",), 30, SourceIdentity("src_1"),
            request_id="req_ws_active_1",
            workspace_mode="isolated",
            cleanup_policy="ephemeral",
        )
        row, _ = job_repo.accept(
            submission,
            workspace_id=rec.workspace_id,
            workspace_authority_digest=auth_dict["digest"],
        )
        job_repo.transition(row["job_id"], Lifecycle.RUNNING)
        job_repo.transition(
            row["job_id"], Lifecycle.SUCCEEDED,
            exit_code=0, termination_reason="normal", integrity_sha256="0" * 64,
        )
        job = job_repo.get(row["job_id"])

        with self.assertRaises(CleanupExecutionError) as ctx:
            ws_service.release_terminal_job(job, job_repo)
        self.assertEqual(ctx.exception.code, "workspace_lease_active")

        # Workspace marked indeterminate (fail-closed), obj preserved
        updated_ws = ws_repo.get(rec.workspace_id)
        self.assertEqual(updated_ws.lifecycle, "indeterminate")
        self.assertTrue(obj_dir.exists())

        # Job truth strictly preserved
        updated_job = job_repo.get(job["job_id"])
        self.assertEqual(updated_job["lifecycle"], "succeeded")
        self.assertEqual(updated_job["exit_code"], 0)
        job_repo.close()


if __name__ == "__main__":
    unittest.main()

