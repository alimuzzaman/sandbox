import tempfile
import unittest
from pathlib import Path

from sandbox.jobs.artifacts import ArtifactError, collect
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class ArtifactTests(unittest.TestCase):
    def test_regular_project_file_is_collected_and_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "result.txt").write_text("result")
            repo = JobRepository(root / "jobs.sqlite")
            job, _ = repo.accept(JobSubmission("test", str(root), "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            storage = JobStorage(root, free_disk_reserve=0); storage.job_dir(job["job_id"], create=True)
            items = collect(storage, repo, job["job_id"], project_root=root, declared_paths=("result.txt",))
            self.assertEqual(items[0]["display_name"], "result.txt")
            with self.assertRaises(ArtifactError): collect(storage, repo, job["job_id"], project_root=root, declared_paths=("../escape",))
            repo.close()
