import json
import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class RetryTests(unittest.TestCase):
    def test_retry_links_a_new_attempt_and_cleanup_protects_active(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            original = service.submit(JobSubmission("test", temp, "p", "local", "w", ("echo", "x"), 60, SourceIdentity("s")))
            with self.assertRaises(RuntimeError): service.cleanup(original["job_id"])
            repo.transition(original["job_id"], "failed")
            retry = service.retry(original["job_id"])
            self.assertNotEqual(retry["job_id"], original["job_id"])
            self.assertEqual(repo.get(retry["job_id"])["retry_of_job_id"], original["job_id"])
            repo.close()

    def test_standalone_retry_preserves_canonical_submission_and_terminal_result(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            submission = JobSubmission(
                "ci", temp, "p", "remote", "cell", ("python", "-V"), 120,
                SourceIdentity("source", "commit", "dirty"), remote_name="remote-1",
                workspace_mode="isolated", cwd_relative="build", execution_profile="ci",
                output_profile="errors", deadline_source="workflow", stall_seconds=30,
                cancel_on_stall=True, cleanup_policy="on-success",
                environment_keys=("CI", "TOKEN_NAME"), artifact_paths=("reports",),
                depends_on=("build",), failure_policy="continue",
                compatibility_differences=({"id": "act.demo", "accepted": True},),
            )
            original = service.submit(submission)
            terminal = {"outcome": "failed", "immutable": True}
            repo.transition(original["job_id"], "failed", exit_code=1,
                            result_json=json.dumps(terminal, sort_keys=True))
            retry = service.retry(original["job_id"])
            retried = repo.get(retry["job_id"])
            retry_snapshot = repo.submission_snapshot(retry["job_id"])
            self.assertIsNone(retried["parent_job_id"])
            self.assertEqual(retried["retry_of_job_id"], original["job_id"])
            self.assertEqual(retried["attempt"], 2)
            self.assertEqual(retry_snapshot["workspace_label"], "cell")
            self.assertEqual(retry_snapshot["workspace_mode"], "isolated")
            self.assertEqual(retry_snapshot["cwd_relative"], "build")
            self.assertEqual(retry_snapshot["execution_profile"], "ci")
            self.assertEqual(retry_snapshot["output_profile"], "errors")
            self.assertEqual(retry_snapshot["deadline_seconds"], 120)
            self.assertEqual(retry_snapshot["deadline_source"], "workflow")
            self.assertEqual(retry_snapshot["source"], {
                "identity": "source", "commit": "commit", "dirty_digest": "dirty"})
            self.assertEqual(retry_snapshot["artifact_paths"], ["reports"])
            self.assertEqual(retry_snapshot["environment_keys"], ["CI", "TOKEN_NAME"])
            self.assertEqual(retry_snapshot["depends_on"], ["build"])
            self.assertEqual(retry_snapshot["failure_policy"], "continue")
            self.assertEqual(retry_snapshot["cleanup_policy"], "on-success")
            self.assertEqual(retry_snapshot["compatibility_differences"][0]["id"], "act.demo")
            self.assertEqual(json.loads(repo.get(original["job_id"])["result_json"]), terminal)
            repo.close()

    def test_legacy_retry_fallback_uses_safe_columns_and_difference_table(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            original = service.submit(JobSubmission(
                "ci", temp, "p", "local", "cell", ("echo", "x"), 90, SourceIdentity("source"),
                output_profile="errors", cleanup_policy="retain",
                compatibility_differences=({"id": "act.legacy", "accepted": True},),
            ))
            repo.connection.execute("UPDATE jobs SET submission_json=NULL WHERE job_id=?", (original["job_id"],))
            repo.transition(original["job_id"], "failed", exit_code=1)
            retry = service.retry(original["job_id"])
            snapshot = repo.submission_snapshot(retry["job_id"])
            self.assertIsNone(repo.get(retry["job_id"])["parent_job_id"])
            self.assertEqual(snapshot["deadline_seconds"], 90)
            self.assertEqual(snapshot["output_profile"], "errors")
            self.assertEqual(snapshot["compatibility_differences"][0]["id"], "act.legacy")
            self.assertEqual(snapshot["artifact_paths"], [])
            repo.close()

    def test_aggregate_parent_retry_is_rejected_without_launching_fake_command(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            launched = []
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=launched.append)
            result = service.submit_matrix([JobSubmission(
                "ci", temp, "p", "local", "cell", ("echo", "x"), 60, SourceIdentity("source"),
                workspace_mode="isolated")])
            child_id = result["children"][0]["job_id"]
            repo.transition(child_id, "running")
            repo.transition(child_id, "failed", exit_code=1)
            service.get(result["parent_job_id"])
            launched.clear()
            with self.assertRaisesRegex(RuntimeError, "aggregate_retry_unsupported"):
                service.retry(result["parent_job_id"])
            self.assertEqual(launched, [])
            self.assertEqual(len(repo.children(result["parent_job_id"])), 1)
            repo.close()

    def test_ci_child_retry_keeps_actual_parent_not_root_guess(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            parent, _ = repo.accept(JobSubmission(
                "matrix", temp, "p", "local", "parent", ("matrix",), 60, SourceIdentity("source")))
            child = service.submit(JobSubmission(
                "ci", temp, "p", "local", "cell", ("echo", "x"), 60, SourceIdentity("source"),
                parent_job_id=parent["job_id"], workspace_mode="isolated"))
            repo.transition(child["job_id"], "failed", exit_code=1)
            retry = service.retry(child["job_id"])
            retried = repo.get(retry["job_id"])
            self.assertEqual(retried["parent_job_id"], parent["job_id"])
            self.assertEqual(retried["root_job_id"], parent["job_id"])
            self.assertEqual(retried["retry_of_job_id"], child["job_id"])
            repo.close()
