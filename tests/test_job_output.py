import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity
from sandbox.jobs.output import JobOutputStore, OutputError
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class _PressureStorage(JobStorage):
    def require_capacity(self, incoming_bytes):
        from sandbox.jobs.storage import StoragePressureError
        raise StoragePressureError("reserve")


class JobOutputTests(unittest.TestCase):
    def test_storage_pressure_is_explicit_before_any_output_write(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            row, _ = repo.accept(JobSubmission("test", temp, "p", "local", "w", ("echo",), 60, SourceIdentity("s")))
            JobStorage(temp, free_disk_reserve=0).job_dir(row["job_id"], create=True)
            storage = _PressureStorage(temp, free_disk_reserve=0)
            output = JobOutputStore(storage, repo, row["job_id"])
            with self.assertRaisesRegex(OutputError, "pressure"):
                output.append("stdout", b"must-not-write")
            repo.close()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        submission = JobSubmission("test", "/project", "project", "local", "default", ("echo", "x"), 60, SourceIdentity("source"))
        self.job, _ = self.repository.accept(submission)
        self.storage = JobStorage(self.temp.name, free_disk_reserve=0)
        self.storage.job_dir(self.job["job_id"], create=True)

    def tearDown(self):
        self.repository.close(); self.temp.cleanup()

    def test_cross_chunk_redaction_and_combined_order(self):
        output = JobOutputStore(self.storage, self.repository, self.job["job_id"], secrets=["secret-value"])
        output.append("stdout", b"one secret-")
        output.append("stderr", b"two\n")
        output.append("stdout", b"value three\n")
        output.finish("stdout"); output.finish("stderr")
        page = output.read(OutputQuery())
        self.assertNotIn("secret-value", page["data"])
        self.assertIn("[REDACTED]", page["data"])
        self.assertEqual([event["stream"] for event in page["events"]], ["stdout", "stderr", "stdout"])

    def test_service_read_after_cleanup_fails_without_recreating_output_directory(self):
        output = JobOutputStore(self.storage, self.repository, self.job["job_id"])
        output.append("stdout", b"retained\n"); output.finish("stdout")
        self.repository.transition(self.job["job_id"], "running")
        self.repository.transition(self.job["job_id"], "succeeded", exit_code=0)
        service = JobService(self.repository, self.storage, None, launcher=lambda _: None)
        directory = self.storage.job_dir(self.job["job_id"]) / "output"
        service.cleanup(self.job["job_id"], logs=True, artifacts=False, metrics=False)
        self.assertFalse(directory.exists())
        with self.assertRaisesRegex(RuntimeError, "output_unavailable"):
            service.read_output(self.job["job_id"], OutputQuery())
        self.assertFalse(directory.exists())

    def test_cursor_does_not_repeat_event_and_invalid_utf8_is_safe(self):
        output = JobOutputStore(self.storage, self.repository, self.job["job_id"])
        output.append("stdout", b"a\xff\n"); output.finish("stdout")
        first = output.read(OutputQuery(max_events=1))
        second = output.read(OutputQuery(cursor=first["cursor"]))
        self.assertEqual(first["events_read"], 1)
        self.assertEqual(second["events_read"], 0)
        self.assertIn("�", first["data"])

    def test_service_applies_builtin_profile_only_at_read_time(self):
        output = JobOutputStore(self.storage, self.repository, self.job["job_id"])
        output.append("stdout", b"one\ntwo\nthree\nerror four\nfive\nsix\nseven\neight\nnine\nten\n"); output.finish("stdout")
        service = JobService(self.repository, self.storage, None, launcher=lambda _: None)
        presented = service.read_output(self.job["job_id"], OutputQuery(profile="errors"))
        full = service.read_output(self.job["job_id"], OutputQuery(profile="full"))
        self.assertEqual(presented["data"], "two\nthree\nerror four\nfive\nsix\nseven\neight\nnine\n")
        self.assertIn("ten\n", full["data"])
        self.assertNotIn("ten\n", presented["data"])
