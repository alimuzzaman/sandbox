import os
import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.metrics import append, read, sample
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class MetricsTests(unittest.TestCase):
    def test_metrics_are_best_effort_and_durably_indexed(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            value = append(storage, repo, job["job_id"], sample(os.getpid()))
            self.assertEqual(len(read(storage, job["job_id"])), 1)
            self.assertIn("timestamp", value)
            self.assertEqual(repo.snapshot(job["job_id"])["metrics"]["samples"], 1)
            repo.close()
