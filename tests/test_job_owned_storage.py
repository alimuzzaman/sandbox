"""Tests for immutable terminal job truth retention during owned storage cleanup."""

import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.models import Lifecycle
from sandbox.jobs.registry import JobRepository
from sandbox.owned_storage.cleanup import OwnedStorageCleanupManager


class TestJobOwnedStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.job_repo_path = self.root / "jobs.sqlite3"
        self.job_repo = JobRepository(self.job_repo_path)

    def tearDown(self):
        self.job_repo.close()
        self.tmp_dir.cleanup()

    def _create_terminal_job(self, job_id: str, lifecycle: str = "succeeded", exit_code: int = 0) -> dict:
        from sandbox.jobs.models import JobSubmission, SourceIdentity

        submission = JobSubmission(
            "ci",
            str(self.root / "dummy"),
            "proj_1",
            "local",
            "ws_lbl",
            ("/bin/true",),
            30,
            SourceIdentity("src_1"),
            request_id=f"req_{job_id}",
            workspace_mode="isolated",
            cleanup_policy="ephemeral",
        )
        row, _ = self.job_repo.accept(
            submission,
            workspace_id="ws_" + "1" * 32,
            workspace_authority_digest="sha256:" + "0" * 64,
        )
        # Transition queued -> running -> terminal
        self.job_repo.transition(row["job_id"], Lifecycle.RUNNING)
        self.job_repo.transition(
            row["job_id"],
            Lifecycle(lifecycle),
            exit_code=exit_code,
            termination_reason="normal" if lifecycle == "succeeded" else "error",
            integrity_sha256="0" * 64,
        )
        return self.job_repo.get(row["job_id"])

    def test_job_truth_immutable_on_cleanup_failure(self):
        job = self._create_terminal_job("job_fail_clean", lifecycle="failed", exit_code=1)
        original_lifecycle = job["lifecycle"]
        original_exit_code = job["exit_code"]
        original_digest = job.get("integrity_sha256")

        # Simulate cleanup failure: set cleanup_state to failed
        self.job_repo.set_cleanup_state(job["job_id"], "failed")

        re_read = self.job_repo.get(job["job_id"])
        self.assertEqual(re_read["lifecycle"], original_lifecycle)
        self.assertEqual(re_read["exit_code"], original_exit_code)
        self.assertEqual(re_read.get("integrity_sha256"), original_digest)
        self.assertEqual(re_read["cleanup_state"], "failed")

    def test_job_truth_immutable_on_cleanup_success(self):
        job = self._create_terminal_job("job_succ_clean", lifecycle="succeeded", exit_code=0)
        original_lifecycle = job["lifecycle"]
        original_exit_code = job["exit_code"]
        original_digest = job.get("integrity_sha256")

        # Simulate cleanup success
        self.job_repo.set_cleanup_state(job["job_id"], "completed")

        re_read = self.job_repo.get(job["job_id"])
        self.assertEqual(re_read["lifecycle"], original_lifecycle)
        self.assertEqual(re_read["exit_code"], original_exit_code)
        self.assertEqual(re_read.get("integrity_sha256"), original_digest)
        self.assertEqual(re_read["cleanup_state"], "completed")

    def test_job_service_cleanup_invokes_workspace_release_preserving_truth(self):
        from unittest.mock import MagicMock
        from sandbox.application.job_service import JobService
        from sandbox.jobs.storage import JobStorage

        job_storage_dir = self.root / "jobs_storage"
        job_storage_dir.mkdir(parents=True, exist_ok=True)
        job_storage = JobStorage(job_storage_dir)

        job = self._create_terminal_job("job_svc_clean", lifecycle="succeeded", exit_code=0)
        original_lifecycle = job["lifecycle"]
        original_exit_code = job["exit_code"]
        original_digest = job.get("integrity_sha256")

        ws_reg = MagicMock()
        ws_reg.retire_terminal_materialization.return_value = False
        ws_reg.release_terminal_job.return_value = {"ok": True, "status": "released", "observed_reclaimed_bytes": 100}
        ws_reg.has_retained_materialization.return_value = False

        svc = JobService(self.job_repo, job_storage, None, workspace_registry=ws_reg)
        res = svc.cleanup(job["job_id"])

        self.assertTrue(res["ok"])
        self.assertIn("workspace_cleanup", res["removed"])
        ws_reg.release_terminal_job.assert_called_once()

        # Terminal truth must be strictly immutable
        after = self.job_repo.get(job["job_id"])
        self.assertEqual(after["lifecycle"], original_lifecycle)
        self.assertEqual(after["exit_code"], original_exit_code)
        self.assertEqual(after.get("integrity_sha256"), original_digest)


if __name__ == "__main__":
    unittest.main()

