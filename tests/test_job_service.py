import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class JobServiceTests(unittest.TestCase):
    def test_acceptance_precedes_launcher_and_idempotency_replays(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            launched = []
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None, launcher=launched.append)
            submission = JobSubmission("test", temp, "p", "local", "default", ("echo", "ok"), 60,
                SourceIdentity("source"), request_id="once")
            first = service.submit(submission); second = service.submit(submission)
            self.assertFalse(first["idempotent_replay"]); self.assertTrue(second["idempotent_replay"])
            self.assertTrue(launched[0].exists())
            repository.close()

    def test_reconcile_marks_lost_supervisor_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=99999999,
                supervisor_start_identity="start", supervisor_nonce_hash="nonce")
            result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["lifecycle"], "interrupted")
            repository.close()
