import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.models import JobSubmission, OutputQuery, SourceIdentity
from sandbox.jobs.output import JobOutputStore
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class JobOutputTests(unittest.TestCase):
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

    def test_cursor_does_not_repeat_event_and_invalid_utf8_is_safe(self):
        output = JobOutputStore(self.storage, self.repository, self.job["job_id"])
        output.append("stdout", b"a\xff\n"); output.finish("stdout")
        first = output.read(OutputQuery(max_events=1))
        second = output.read(OutputQuery(cursor=first["cursor"]))
        self.assertEqual(first["events_read"], 1)
        self.assertEqual(second["events_read"], 0)
        self.assertIn("�", first["data"])
