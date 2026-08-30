import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.application.workspace_service import WorkspaceService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository, read_resource_index
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.supervisor import run_descriptor
from sandbox.workspaces.repository import WorkspaceRepository


class DisposableCIWorkspaceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.deploy_root = self.root / "deploy-src"
        self.deploy_root.mkdir()
        self.job_repository = JobRepository(self.root / "runtime" / "jobs" / "registry.sqlite3")
        self.storage = JobStorage(self.root / "runtime", free_disk_reserve=0)
        self.workspace_repository = WorkspaceRepository(
            self.root / "runtime" / "workspaces" / "index.sqlite3",
            self.root / "runtime" / "jobs" / "workspaces",
            job_index_reader=lambda: read_resource_index(self.job_repository.path),
        )
        self.workspaces = WorkspaceService(
            None,
            repository=self.workspace_repository,
            deployment_root=self.deploy_root,
        )

    def tearDown(self):
        self.job_repository.close()
        self.temporary.cleanup()

    def _checkout(self, name):
        checkout = self.deploy_root / name
        checkout.mkdir()
        (checkout / "retained-evidence.txt").write_text("fixture")
        return checkout

    def _submission(self, checkout, *, request_id, mode="isolated", cleanup="ephemeral"):
        return JobSubmission(
            "ci", str(checkout), "project:ci", "local", checkout.name,
            ("/bin/sh", "-c", "true"), 20, SourceIdentity("source"),
            request_id=request_id, workspace_mode=mode, cleanup_policy=cleanup,
        )

    def _service(self, launcher):
        return JobService(
            self.job_repository, self.storage, None, launcher=launcher,
            workspace_registry=self.workspaces,
        )

    def test_prelaunch_failure_retains_terminal_row_then_releases_exact_disposable_workspace(self):
        checkout = self._checkout("launch-failure")
        service = self._service(
            lambda _descriptor: (_ for _ in ()).throw(OSError("launch failed")))

        with self.assertRaisesRegex(RuntimeError, "supervisor_launch_failed"):
            service.submit(self._submission(
                checkout, request_id="launch-failure-request"))

        row = self.job_repository.list(limit=1)[0]
        self.assertEqual(row["lifecycle"], "failed")
        self.assertEqual(row["termination_reason"], "supervisor_launch_failed")
        self.assertEqual(row["cleanup_state"], "completed")
        self.assertFalse(checkout.exists())
        self.assertEqual(
            self.workspace_repository.get(row["workspace_id"]).lifecycle,
            "destroyed",
        )

    def test_supervisor_success_and_failure_use_the_same_terminal_cleanup_seam(self):
        for name, command, lifecycle in (
            ("success", ("/bin/sh", "-c", "true"), "succeeded"),
            ("failure", ("/bin/sh", "-c", "exit 7"), "failed"),
        ):
            with self.subTest(lifecycle=lifecycle):
                checkout = self._checkout(name)
                descriptors = []
                service = self._service(descriptors.append)
                submission = self._submission(
                    checkout, request_id=f"{name}-request")
                submission = JobSubmission(**{
                    **submission.__dict__, "argv": command,
                })
                accepted = service.submit(submission)

                run_descriptor(descriptors[0])

                row = self.job_repository.get(accepted["job_id"])
                self.assertEqual(row["lifecycle"], lifecycle)
                self.assertEqual(row["cleanup_state"], "completed")
                self.assertFalse(checkout.exists())

    def test_persistent_or_retained_workspace_is_never_deleted(self):
        for mode, cleanup in (("persistent", "ephemeral"), ("isolated", "retain")):
            with self.subTest(mode=mode, cleanup=cleanup):
                checkout = self._checkout(f"retained-{mode}-{cleanup}")
                service = self._service(lambda _descriptor: None)
                accepted = service.submit(self._submission(
                    checkout, request_id=f"retained-{mode}-{cleanup}",
                    mode=mode, cleanup=cleanup))
                self.job_repository.transition(accepted["job_id"], "running")
                self.job_repository.transition(
                    accepted["job_id"], "succeeded", exit_code=0)

                row = service.get(accepted["job_id"])

                self.assertTrue(checkout.exists())
                self.assertEqual(row["cleanup_state"], "retained")
                self.assertEqual(
                    self.workspace_repository.get(row["workspace_id"]).lifecycle,
                    "ready",
                )

    def test_on_success_policy_retains_failed_disposable_workspace(self):
        checkout = self._checkout("on-success-failure")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="on-success-failure-request",
            cleanup="on-success"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "failed", exit_code=3,
            termination_reason="exit_nonzero")

        row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "retained")
        self.assertTrue(checkout.exists())

    def test_cleanup_failure_is_truthful_and_does_not_rewrite_terminal_result(self):
        checkout = self._checkout("cleanup-failure")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="cleanup-failure-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)

        with patch(
                "sandbox.application.workspace_service.shutil.rmtree",
                side_effect=OSError("fixture cleanup failed")):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["lifecycle"], "succeeded")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["cleanup_state"], "failed")
        self.assertTrue(checkout.exists())
        self.assertEqual(
            self.workspace_repository.get(row["workspace_id"]).lifecycle,
            "indeterminate",
        )

    def test_ambiguous_or_foreign_workspace_id_fails_cleanup_without_changing_job_result(self):
        checkout = self._checkout("ambiguous")
        foreign = self.workspace_repository.register(
            "project:foreign", "foreign", namespace="project-foreign")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="ambiguous-request"))
        self.job_repository.connection.execute(
            "UPDATE jobs SET workspace_id=? WHERE job_id=?",
            (foreign.workspace_id, accepted["job_id"]),
        )
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "failed", exit_code=9,
            termination_reason="exit_nonzero")

        row = service.get(accepted["job_id"])

        self.assertEqual(row["lifecycle"], "failed")
        self.assertEqual(row["exit_code"], 9)
        self.assertEqual(row["cleanup_state"], "failed")
        self.assertTrue(checkout.exists())
        self.assertEqual(
            self.workspace_repository.get(foreign.workspace_id).lifecycle,
            "ready",
        )

    def test_replay_is_idempotent_after_disposable_workspace_release(self):
        checkout = self._checkout("replay")
        service = self._service(
            lambda _descriptor: (_ for _ in ()).throw(OSError("launch failed")))
        submission = self._submission(checkout, request_id="replay-request")
        with self.assertRaisesRegex(RuntimeError, "supervisor_launch_failed"):
            service.submit(submission)
        first = self.job_repository.list(limit=1)[0]

        replay = service.submit(submission)

        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["job_id"], first["job_id"])
        self.assertEqual(
            self.job_repository.get(first["job_id"])["cleanup_state"],
            "completed",
        )

    def test_terminal_cleanup_clears_exact_active_job_projection(self):
        checkout = self._checkout("projection")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="projection-request"))
        row = self.job_repository.get(accepted["job_id"])
        workspace_id = row["workspace_id"]
        before = self.workspace_repository.ownership_projection()["records"]
        projected = next(item for item in before if item["workspace_id"] == workspace_id)
        self.assertEqual(projected["active_references"]["jobs"], 1)
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)

        service.get(accepted["job_id"])

        after = self.workspace_repository.ownership_projection()["records"]
        projected = next(item for item in after if item["workspace_id"] == workspace_id)
        self.assertEqual(projected["active_references"]["jobs"], 0)
        self.assertEqual(projected["lifecycle"], "destroyed")


if __name__ == "__main__":
    unittest.main()
