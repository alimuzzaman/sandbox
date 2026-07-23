import tempfile
import time
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class CancellationTests(unittest.TestCase):
    def test_cancel_acceptance_before_supervisor_launch_releases_the_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            row, _ = repo.accept(JobSubmission("test", temp, "p", "local", "cancel", ("echo", "ok"), 30,
                SourceIdentity("s")))
            result = service.cancel(row["job_id"], force=True)
            self.assertEqual(result["lifecycle"], "cancelled")
            self.assertEqual(result["termination_reason"], "cancelled_before_process_start")
            repo.close()

    def test_ci_leaf_without_children_cancels_as_leaf(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            row, _ = repo.accept(JobSubmission("ci", temp, "p", "local", "ci-cell",
                ("echo", "ok"), 30, SourceIdentity("s"), workspace_mode="isolated"))
            result = service.cancel(row["job_id"])
            self.assertEqual(result["lifecycle"], "cancelled")
            self.assertEqual(result["termination_reason"], "cancelled_before_process_start")
            repo.close()

    def test_verified_cancel_transitions_to_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            job = service.submit(JobSubmission("test", temp, "p", "local", "cancel", ("/bin/sh", "-c", "sleep 5"), 30, SourceIdentity("s")))
            for _ in range(100):
                state = service.get(job["job_id"])
                if (state.get("process") or {}).get("child_pid"): break
                time.sleep(.03)
            service.cancel(job["job_id"])
            for _ in range(100):
                state = service.get(job["job_id"])
                if state["lifecycle"] == "cancelled": break
                time.sleep(.03)
            self.assertEqual(state["lifecycle"], "cancelled")
            repo.close()
