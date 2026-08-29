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
    def test_parallel_safe_jobs_share_only_the_same_accepted_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            scheduler = JobScheduler(
                repo, max_parallel=3, min_free_memory_mb=0, min_free_disk_mb=0,
            )

            def accept(request, generation):
                return repo.accept(JobSubmission(
                    "test", "/p", "p", "remote", "same", ("echo", "x"), 60,
                    SourceIdentity("s"), remote_name="remote", request_id=request,
                    sync_relationship_id="rel", sync_generation_id=generation,
                    source_access="managed_read_only", parallel_safe=True,
                ))[0]

            first = accept("one", "gen_a")
            peer = accept("two", "gen_a")
            newest = accept("three", "gen_b")
            scheduler.acquire(first, parallel_safe=True)
            scheduler.acquire(peer, parallel_safe=True)
            with self.assertRaises(WorkspaceBusy):
                scheduler.acquire(newest, parallel_safe=True)
            repo.close()
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

    def test_busy_submission_reports_advisory_position_and_blocker(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            scheduler = JobScheduler(repo, max_parallel=1,
                                     min_free_memory_mb=0, min_free_disk_mb=0)
            service = JobService(
                repo, JobStorage(temp, free_disk_reserve=0), None,
                launcher=lambda _: None, scheduler=scheduler,
            )
            source = SourceIdentity("s")
            first = service.submit(JobSubmission(
                "test", temp, "p", "local", "same", ("echo", "one"), 60,
                source, request_id="first",
            ))
            second = service.submit(JobSubmission(
                "test", temp, "p", "local", "other", ("echo", "two"), 60,
                source, request_id="second",
            ))
            self.assertEqual(second["queue"]["position"], 1)
            self.assertEqual(second["queue"]["blocking_jobs"][0]["job_id"], first["job_id"])
            status = service.get(second["job_id"])
            self.assertEqual(status["queue"]["position"], 1)
            repo.close()

    def test_new_admission_reaps_terminal_job_leases_without_status_poll(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            scheduler = JobScheduler(repo, max_parallel=1,
                                     min_free_memory_mb=0, min_free_disk_mb=0)
            first = repo.accept(JobSubmission(
                "test", temp, "p", "local", "one", ("echo", "one"), 60,
                SourceIdentity("s"), request_id="first",
            ))[0]
            scheduler.acquire(first)
            repo.transition(first["job_id"], "running")
            repo.transition(first["job_id"], "succeeded", exit_code=0)
            second = repo.accept(JobSubmission(
                "test", temp, "p", "local", "two", ("echo", "two"), 60,
                SourceIdentity("s"), request_id="second",
            ))[0]
            scheduler.acquire(second)
            self.assertEqual([row["job_id"] for row in scheduler.active()], [second["job_id"]])
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
