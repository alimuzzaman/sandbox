import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class SupervisorTests(unittest.TestCase):
    def test_fast_child_does_not_depend_on_a_racy_getpgid_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            with patch("sandbox.jobs.supervisor.os.getpgid", side_effect=AssertionError("must not be called")):
                submitted = service.submit(JobSubmission("test", temp, "p", "local", "fast",
                    ("/bin/sh", "-c", "true"), 20, SourceIdentity("source")))
                for _ in range(100):
                    state = service.get(submitted["job_id"])
                    if state["lifecycle"] in {"succeeded", "failed", "timed_out"}:
                        break
                    time.sleep(.05)
            self.assertEqual(state["lifecycle"], "succeeded", state)
            repository.close()

    def test_detached_process_drains_output_and_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "default",
                ("/bin/sh", "-c", "printf start; sleep .1; printf done"), 20, SourceIdentity("source")))
            for _ in range(100):
                state = service.get(submitted["job_id"])
                if state["lifecycle"] in {"succeeded", "failed", "timed_out"}:
                    break
                time.sleep(.05)
            self.assertEqual(state["lifecycle"], "succeeded", state)
            self.assertEqual(state["output_completeness"], "complete")
            self.assertIsNotNone(state["integrity_sha256"])
            self.assertTrue(all(item["complete"] for item in state["output"]))
            self.assertEqual(service.read_output(submitted["job_id"])["data"], "startdone")
            repository.close()

    def test_opt_in_stall_cancellation_records_a_distinct_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "stalled",
                ("/bin/sh", "-c", "sleep 10"), 20, SourceIdentity("source"),
                stall_seconds=1, cancel_on_stall=True))
            for _ in range(100):
                state = service.get(submitted["job_id"])
                if state["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                    break
                time.sleep(.05)
            self.assertEqual(state["lifecycle"], "cancelled", state)
            self.assertEqual(state["termination_reason"], "cancelled_on_stall")
            repository.close()
