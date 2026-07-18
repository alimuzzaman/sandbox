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

    def test_reconcile_marks_running_job_without_supervisor_identity_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            result = service.reconcile_startup()
            self.assertEqual(result["interrupted"], [row["job_id"]])
            self.assertEqual(repository.get(row["job_id"])["termination_reason"], "missing_supervisor_identity")
            repository.close()

    def test_retention_sweep_removes_terminal_outputs_and_marks_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.transition(row["job_id"], "succeeded", exit_code=0)
            job_dir = storage.job_dir(row["job_id"], create=True)
            for name in ("output", "artifacts", "metrics"):
                (job_dir / name).mkdir()
                (job_dir / name / "data").write_text("retained")
            result = service.retention_sweep(retention_days=0)
            self.assertEqual(len(result["cleaned"]), 1)
            self.assertEqual(repository.get(row["job_id"])["cleanup_state"], "completed")
            self.assertFalse((job_dir / "output").exists())
            repository.close()

    def test_storage_pressure_retention_reclaims_oldest_terminal_job(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repository, storage, None, launcher=lambda _descriptor: None)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "default",
                ("echo", "ok"), 60, SourceIdentity("source")))
            repository.transition(row["job_id"], "running")
            repository.transition(row["job_id"], "succeeded", exit_code=0)
            job_dir = storage.job_dir(row["job_id"], create=True)
            (job_dir / "output").mkdir()
            (job_dir / "output" / "data").write_text("retained")
            storage.is_under_pressure = lambda: True
            result = service.retention_sweep(retention_days=7, storage_pressure=True)
            self.assertTrue(result["storage_pressure"])
            self.assertEqual(len(result["cleaned"]), 1)
            repository.close()
