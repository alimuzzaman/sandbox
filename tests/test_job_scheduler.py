import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy


class JobSchedulerTests(unittest.TestCase):
    def test_exclusive_workspace_and_capacity_are_transactional(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            scheduler = JobScheduler(repo, max_parallel=1)
            def accept(label):
                return repo.accept(JobSubmission("test", "/p", "p", "local", label, ("echo", "x"), 60, SourceIdentity("s")))[0]
            first = accept("same"); second = accept("same")
            scheduler.acquire(first)
            with self.assertRaises(WorkspaceBusy): scheduler.acquire(second)
            scheduler.release(first["job_id"]); scheduler.acquire(second)
            repo.close()
