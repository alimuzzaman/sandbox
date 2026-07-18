import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
from sandbox.application.job_service import JobService
from sandbox.jobs.storage import JobStorage


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

    def test_busy_submission_queues_then_dispatches_after_release(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            scheduler = JobScheduler(repo, max_parallel=1)
            launched = []
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None,
                launcher=launched.append, scheduler=scheduler)
            first = service.submit(JobSubmission("test", temp, "p", "local", "same", ("echo", "one"), 60, SourceIdentity("s")))
            second = service.submit(JobSubmission("test", temp, "p", "local", "same", ("echo", "two"), 60, SourceIdentity("s")))
            self.assertEqual(repo.get(second["job_id"])["lifecycle"], "queued")
            scheduler.release(first["job_id"])
            service.get(second["job_id"])
            self.assertEqual(len(launched), 2)
            repo.close()
