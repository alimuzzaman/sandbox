import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
from sandbox.jobs.storage import JobStorage


class WorkspaceConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        self.scheduler = JobScheduler(self.repository, max_parallel=2,
                                      min_free_memory_mb=0, min_free_disk_mb=0)

    def tearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def _job(self, workspace, request):
        row, _ = self.repository.accept(JobSubmission(
            "test", self.temp.name, "project", "local", workspace, ("echo", "ok"), 60,
            SourceIdentity("source"), request_id=request,
            workspace_mode="isolated" if workspace != "shared" else "persistent"))
        return row

    def test_same_workspace_is_serial_and_returns_an_immediate_busy_suggestion(self):
        first = self._job("shared", "first")
        second = self._job("shared", "second")
        self.scheduler.acquire(first)
        with self.assertRaisesRegex(WorkspaceBusy, "use an isolated label or wait"):
            self.scheduler.acquire(second)

    def test_explicitly_shared_safe_jobs_can_use_the_same_workspace(self):
        first = self._job("shared", "first")
        second = self._job("shared", "second")
        self.scheduler.acquire(first, parallel_safe=True)
        self.scheduler.acquire(second, parallel_safe=True)
        self.assertEqual(len(self.scheduler.active()), 2)

    def test_isolated_jobs_have_independent_workspace_labels_and_job_storage(self):
        first = self._job("cell-a", "first")
        second = self._job("cell-b", "second")
        self.scheduler.acquire(first)
        self.scheduler.acquire(second)
        storage = JobStorage(self.temp.name, free_disk_reserve=0)
        first_dir = storage.job_dir(first["job_id"], create=True)
        second_dir = storage.job_dir(second["job_id"], create=True)
        self.assertNotEqual(first["workspace_label"], second["workspace_label"])
        self.assertNotEqual(first_dir, second_dir)
        self.assertTrue(first_dir.is_dir())
        self.assertTrue(second_dir.is_dir())
