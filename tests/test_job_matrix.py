import json
import tempfile
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler
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

    def test_terminal_parent_persists_normalized_inspectable_child_result(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            result = service.submit_matrix([JobSubmission(
                "ci", temp, "p", "local", "cell-a", ("echo", "a"), 60, SourceIdentity("s"),
                workspace_mode="isolated", cleanup_policy="on-success",
                compatibility_differences=({"id": "act.safe-mode", "accepted": True},),
            )])
            service.get(result["parent_job_id"])
            child_id = result["children"][0]["job_id"]
            repo.upsert_output_stream(child_id, "stdout", bytes_stored=4, events_stored=1,
                                      next_sequence=1, complete=True, sha256="1" * 64)
            repo.add_artifact(child_id, artifact_id="report", display_name="report.txt",
                              stored_relative_path="artifacts/report", size_bytes=4, sha256="2" * 64)
            repo.set_cleanup_state(child_id, "retained")
            repo.transition(child_id, "running")
            repo.transition(child_id, "succeeded", exit_code=0, output_completeness="complete")
            parent = service.get(result["parent_job_id"])
            persisted = json.loads(repo.get(result["parent_job_id"])["result_json"])
            self.assertEqual(parent["aggregate"]["passed"], 1)
            self.assertEqual(parent["result"], persisted)
            self.assertIn("result_json", parent)
            outcome = persisted["child_outcomes"][0]
            self.assertEqual(outcome["job_id"], child_id)
            self.assertEqual(outcome["output_completeness"], "complete")
            self.assertEqual(outcome["artifact_count"], 1)
            self.assertEqual(outcome["compatibility_difference_count"], 1)
            self.assertEqual(outcome["cleanup"]["state"], "retained")
            current_child = parent["children"][0]
            self.assertEqual(current_child["artifacts"][0]["artifact_id"], "report")
            self.assertEqual(current_child["compatibility_differences"][0]["difference_id"], "act.safe-mode")
            repo.close()

    def test_ci_parent_result_contains_bounded_truthful_context(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            result = service.submit_matrix([JobSubmission(
                "ci", temp, "p", "local", "unit", ("sb", "ci", "run", ".github/workflows/ci.yml"),
                60, SourceIdentity("deploy-source", "commit", "dirty"), workspace_mode="isolated",
                compatibility_differences=({"id": "safe-mode:unit:1", "accepted": True},),
            )])
            child_id = result["children"][0]["job_id"]
            repo.transition(child_id, "running")
            repo.transition(child_id, "succeeded", exit_code=0)
            parent = service.get(result["parent_job_id"])
            context = parent["result"]["context"]
            self.assertEqual(context["engine"], {"name": "act", "version": "unobserved"})
            self.assertEqual(context["workflows"], [".github/workflows/ci.yml"])
            self.assertEqual(context["source"]["identity"], "deploy-source")
            self.assertEqual(context["graph"]["children"][0]["job_id"], child_id)
            self.assertEqual(context["accepted_differences"], ["safe-mode:unit:1"])
            self.assertEqual(context["safe_mode_skips"], ["safe-mode:unit:1"])
            self.assertLessEqual(len(parent["result_json"].encode()), 262_144)
            repo.close()

    def test_terminal_parent_freezes_original_membership_and_exposes_retries_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repo, storage, None, launcher=lambda _: None)
            result = service.submit_matrix([JobSubmission(
                "ci", temp, "p", "local", "cell-a", ("echo", "a"), 60, SourceIdentity("s"),
                workspace_mode="isolated")])
            child_id = result["children"][0]["job_id"]
            repo.transition(child_id, "running")
            repo.transition(child_id, "failed", exit_code=1)
            terminal = service.get(result["parent_job_id"])
            immutable_result = terminal["result_json"]
            retry = service.retry(child_id)
            parent = service.get(result["parent_job_id"])
            self.assertEqual([item["job_id"] for item in parent["children"]], [child_id])
            self.assertEqual([item["job_id"] for item in parent["retry_attempts"]], [retry["job_id"]])
            self.assertEqual(parent["aggregate"], terminal["aggregate"])
            self.assertEqual(parent["result_json"], immutable_result)
            repo.close()

    def test_cleanup_after_parent_terminal_keeps_result_immutable_but_current_child_metadata_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            service = JobService(repo, storage, None, launcher=lambda _: None)
            result = service.submit_matrix([JobSubmission(
                "ci", temp, "p", "local", "cell-a", ("echo", "a"), 60, SourceIdentity("s"),
                workspace_mode="isolated")])
            child_id = result["children"][0]["job_id"]
            job_dir = storage.job_dir(child_id)
            (job_dir / "artifacts").mkdir(exist_ok=True)
            (job_dir / "artifacts" / "report").write_text("data")
            repo.add_artifact(child_id, artifact_id="report", display_name="report.txt",
                stored_relative_path="artifacts/report", size_bytes=4, sha256="0" * 64)
            repo.transition(child_id, "running")
            repo.transition(child_id, "succeeded", exit_code=0)
            terminal = service.get(result["parent_job_id"])
            immutable_result = terminal["result_json"]
            service.cleanup(child_id, logs=False, artifacts=True, metrics=False)
            parent = service.get(result["parent_job_id"])
            self.assertEqual(parent["result_json"], immutable_result)
            self.assertEqual(parent["children"][0]["artifacts"][0]["status"], "expired")
            repo.close()

    def test_persisted_aggregate_result_is_bounded_and_reports_truncation(self):
        children = [{
            "job_id": f"{index:032x}", "parent_job_id": "f" * 32,
            "retry_of_job_id": None, "attempt": 1, "kind": "ci",
            "workspace_label": f"cell-{index}", "lifecycle": "succeeded",
            "exit_code": 0, "termination_reason": None,
            "output_completeness": "complete", "output": [],
            "artifacts": [{"artifact_id": "a" * 128}] * 50,
            "compatibility_differences": [{"detail": "x" * 2048}] * 50,
            "cleanup_policy": "retain", "cleanup_state": "retained",
        } for index in range(5000)]
        result = JobService._normalized_aggregate_result(children, "succeeded")
        encoded = json.dumps(result, sort_keys=True).encode()
        self.assertLessEqual(len(encoded), 262_144)
        self.assertTrue(result["child_outcomes_truncated"])
        self.assertEqual(result["children"], 5000)

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

    def test_failed_dependency_dispatches_continue_policy_child(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            launched = []
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=launched.append)
            source = SourceIdentity("s")
            upstream = JobSubmission("test", temp, "p", "local", "build", ("echo", "build"), 60,
                source, workspace_mode="isolated", failure_policy="continue")
            downstream = JobSubmission("test", temp, "p", "local", "unit", ("echo", "unit"), 60,
                source, workspace_mode="isolated", depends_on=("build",), failure_policy="continue")
            result = service.submit_matrix([upstream, downstream])
            child = {item["workspace"]: item for item in result["children"]}
            repo.transition(child["build"]["job_id"], "queued")
            repo.transition(child["build"]["job_id"], "running")
            repo.transition(child["build"]["job_id"], "failed", exit_code=1)
            state = service.get(child["unit"]["job_id"])
            self.assertEqual(state["lifecycle"], "queued")
            self.assertEqual(len(launched), 2)
            repo.close()

    def test_matrix_capacity_queues_independent_cell_until_slot_is_released(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            launched = []
            scheduler = JobScheduler(repo, max_parallel=1, min_free_memory_mb=0, min_free_disk_mb=0)
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=launched.append, scheduler=scheduler)
            source = SourceIdentity("s")
            result = service.submit_matrix([
                JobSubmission("test", temp, "p", "local", "cell-a", ("echo", "a"), 60,
                    source, workspace_mode="isolated"),
                JobSubmission("test", temp, "p", "local", "cell-b", ("echo", "b"), 60,
                    source, workspace_mode="isolated"),
            ])
            child = {item["workspace"]: item for item in result["children"]}
            self.assertEqual(repo.get(child["cell-b"]["job_id"])["queue_reason"],
                             "workspace_or_capacity_busy")
            scheduler.release(child["cell-a"]["job_id"])
            service.get(child["cell-b"]["job_id"])
            self.assertEqual(len(launched), 2)
            repo.close()
