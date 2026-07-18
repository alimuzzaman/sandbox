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
            self.assertTrue(result["parent_job_id"])
            parent = service.get(result["parent_job_id"])
            self.assertEqual(parent["aggregate"]["children"], 2)
            for child in result["children"]:
                repo.transition(child["job_id"], "running")
                repo.transition(child["job_id"], "succeeded", exit_code=0)
            parent = service.get(result["parent_job_id"])
            self.assertEqual(parent["lifecycle"], "succeeded")
            self.assertEqual(parent["aggregate"]["passed"], 2)
            repo.close()

    def test_dependency_edges_queue_until_prerequisite_and_then_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            launched = []
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=launched.append)
            source = SourceIdentity("s")
            upstream = JobSubmission("ci", temp, "p", "local", "build", ("echo", "build"), 60,
                source, workspace_mode="isolated")
            downstream = JobSubmission("ci", temp, "p", "local", "unit", ("echo", "unit"), 60,
                source, workspace_mode="isolated", depends_on=("build",))
            result = service.submit_matrix([upstream, downstream])
            child = {item["workspace"]: item for item in result["children"]}
            self.assertEqual(child["unit"]["queue"]["reason"], "dependency")
            self.assertEqual(len(launched), 1)
            repo.transition(child["build"]["job_id"], "queued")
            repo.transition(child["build"]["job_id"], "running")
            repo.transition(child["build"]["job_id"], "succeeded", exit_code=0)
            service.get(child["unit"]["job_id"])
            self.assertEqual(len(launched), 2)
            repo.close()

    def test_failed_dependency_blocks_fail_fast_child(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            source = SourceIdentity("s")
            upstream = JobSubmission("test", temp, "p", "local", "build", ("echo", "build"), 60,
                source, workspace_mode="isolated")
            downstream = JobSubmission("test", temp, "p", "local", "unit", ("echo", "unit"), 60,
                source, workspace_mode="isolated", depends_on=("build",))
            result = service.submit_matrix([upstream, downstream])
            child = {item["workspace"]: item for item in result["children"]}
            repo.transition(child["build"]["job_id"], "queued")
            repo.transition(child["build"]["job_id"], "running")
            repo.transition(child["build"]["job_id"], "failed", exit_code=1)
            state = service.get(child["unit"]["job_id"])
            self.assertEqual(state["lifecycle"], "cancelled")
            self.assertEqual(state["termination_reason"], "dependency_failed")
            repo.close()
