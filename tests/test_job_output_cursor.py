import unittest
import tempfile
import threading
import time
from pathlib import Path

from sandbox.jobs.models import OutputQuery
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.output import JobOutputStore
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class OutputCursorModelTests(unittest.TestCase):
    def test_only_one_position_selector_is_allowed(self):
        with self.assertRaises(ValueError):
            OutputQuery(cursor="abc", offset=0)
        self.assertEqual(OutputQuery(wait_seconds=20).wait_seconds, 20)

    def test_line_tail_and_cursor_long_poll_read_retained_output(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", "/p", "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(temp, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            output = JobOutputStore(storage, repo, job["job_id"])
            output.append("stdout", b"one\ntwo\nthree\n")
            page = output.read(OutputQuery(lines=2))
            self.assertEqual(page["data"], "two\nthree\n")
            cursor = output.read(OutputQuery()).get("cursor")
            thread = threading.Thread(target=lambda: (time.sleep(.05), output.append("stdout", b"four\n")))
            thread.start()
            waited = output.read(OutputQuery(cursor=cursor, wait_seconds=1))
            thread.join()
            self.assertEqual(waited["data"], "four\n")
            repo.close()
