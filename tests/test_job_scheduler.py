import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_renewal_and_stale_reconciliation_release_only_expired_leases(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            scheduler = JobScheduler(repo, max_parallel=2)
            first = repo.accept(JobSubmission("test", "/p", "p", "local", "one", ("echo", "x"), 60, SourceIdentity("s")))[0]
            second = repo.accept(JobSubmission("test", "/p", "p", "local", "two", ("echo", "x"), 60, SourceIdentity("s")))[0]
            scheduler.acquire(first); scheduler.acquire(second)
            self.assertTrue(scheduler.renew(first["job_id"], deadline_seconds=300))
            future = datetime.now(timezone.utc) + timedelta(seconds=120)
            removed = scheduler.reconcile_stale(now=future)
            self.assertIn(second["job_id"], removed)
            self.assertNotIn(first["job_id"], removed)
            self.assertEqual(len(scheduler.active()), 1)
            repo.close()
