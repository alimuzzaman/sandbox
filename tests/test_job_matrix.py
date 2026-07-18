import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class MatrixTests(unittest.TestCase):
    def test_matrix_children_are_isolated_and_independently_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            source = SourceIdentity("s")
            children = [JobSubmission("test", temp, "p", "local", label, ("echo", label), 60, source,
                workspace_mode="isolated") for label in ("cell-a", "cell-b")]
            result = service.submit_matrix(children)
            self.assertEqual(result["summary"]["submitted"], 2)
            self.assertEqual({item["workspace"] for item in result["children"]}, {"cell-a", "cell-b"})
            repo.close()
