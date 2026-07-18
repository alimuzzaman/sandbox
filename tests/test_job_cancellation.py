import tempfile
import time
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class CancellationTests(unittest.TestCase):
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
