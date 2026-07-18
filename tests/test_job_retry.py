import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class RetryTests(unittest.TestCase):
    def test_retry_links_a_new_attempt_and_cleanup_protects_active(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            original = service.submit(JobSubmission("test", temp, "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            with self.assertRaises(RuntimeError): service.cleanup(original["job_id"])
            repo.transition(original["job_id"], "failed")
            retry = service.retry(original["job_id"])
            self.assertNotEqual(retry["job_id"], original["job_id"])
            self.assertEqual(repo.get(retry["job_id"])["retry_of_job_id"], original["job_id"])
            repo.close()
