import os
import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
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

    def test_cleanup_removes_real_metrics_file_and_read_service_rejects_unavailable_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repo, storage, None, launcher=lambda _: None)
            job, _ = repo.accept(JobSubmission("test", temp, "p", "local", "w",
                ("echo", "x"), 60, SourceIdentity("s")))
            storage.job_dir(job["job_id"], create=True)
            append(storage, repo, job["job_id"], {"timestamp": 1.0, "cpu_seconds": 0.1})
            repo.transition(job["job_id"], "running")
            repo.transition(job["job_id"], "succeeded", exit_code=0)
            metrics_file = storage.job_dir(job["job_id"]) / "metrics.jsonl"
            self.assertTrue(metrics_file.exists())
            service.cleanup(job["job_id"], logs=False, artifacts=False, metrics=True)
            self.assertFalse(metrics_file.exists())
            with self.assertRaisesRegex(RuntimeError, "metrics_unavailable"):
                service.read_metrics(job["job_id"])
            repo.close()
