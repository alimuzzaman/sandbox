import os
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.jobs.metrics import append, read, sample
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class MetricsTests(unittest.TestCase):
    def test_proc_and_portable_fallback_metrics_expose_movement_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "42").mkdir()
            (root / "42" / "stat").write_text("42 (worker) S " + "0 " * 11 + "10 20 0\n")
            (root / "42" / "status").write_text("VmRSS:\t12 kB\n")
            (root / "42" / "io").write_text("read_bytes: 5\nwrite_bytes: 7\n")
            value = sample(42, proc_root=root, disk_path=root)
            self.assertEqual(value["rss_bytes"], 12 * 1024)
            self.assertEqual((value["io_read_bytes"], value["io_write_bytes"]), (5, 7))
            self.assertIn("proc_stat", value["capabilities"])
            self.assertIn("disk_free", value["capabilities"])
            self.assertEqual(len(value["movement_digest"]), 64)

    def test_portable_ps_fallback_keeps_sampling_best_effort(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("sandbox.jobs.metrics.subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout="9 S\n")):
            value = sample(42, proc_root=Path(temp), disk_path=temp)
        self.assertEqual((value["rss_bytes"], value["state"]), (9 * 1024, "S"))
        self.assertIn("portable_ps", value["capabilities"])
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
