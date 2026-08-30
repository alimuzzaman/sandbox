import tempfile
import unittest
import os
import shutil
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.application.workspace_service import WorkspaceService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.process import ProcessIdentity
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
            cleanup_reference_observer=lambda _checkout, _record: {
                "containers": 0, "mounts": 0},
        )
        self.sources = {}

    def tearDown(self):
        self.job_repository.close()
        self.temporary.cleanup()

    def _checkout(self, name):
        source = self.deploy_root / f"{name}-source"
        source.mkdir()
        (source / "retained-evidence.txt").write_text("fixture")
        checkout = self.deploy_root / name
        shutil.copytree(source, checkout)
        self.sources[str(checkout)] = source
        return checkout

    def _submission(self, checkout, *, request_id, mode="isolated", cleanup="ephemeral"):
        return JobSubmission(
            "ci", str(checkout), "project:ci", "local", checkout.name,
            ("/bin/sh", "-c", "true"), 20, SourceIdentity("source"),
            request_id=request_id, workspace_mode=mode, cleanup_policy=cleanup,
            materialization_source_root=str(self.sources[str(checkout)]),
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

                with patch(
                        "sandbox.application.workspace_service._observe_cleanup_references",
                        return_value={"containers": 0, "mounts": 0}):
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
        self.assertFalse(checkout.exists())
        quarantines = tuple(self.deploy_root.glob(".sandbox-ci-cleanup-*"))
        self.assertEqual(len(quarantines), 1)
        self.assertTrue((quarantines[0] / "retained-evidence.txt").is_file())
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

    def test_reconciled_terminal_job_refuses_cleanup_while_recorded_child_is_live(self):
        checkout = self._checkout("live-child")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="live-child-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.put_process_identity(
            accepted["job_id"], host_boot_id="boot", supervisor_pid=101,
            supervisor_start_identity="gone", supervisor_nonce_hash="nonce",
            child_pid=202, child_pgid=202, child_start_identity="live-child",
        )
        self.job_repository.transition(
            accepted["job_id"], "interrupted",
            termination_reason="supervisor_lost")

        def observed(pid):
            if pid == 202:
                return ProcessIdentity("boot", 202, "live-child", "", 202)
            return None

        with patch(
                "sandbox.application.workspace_service.capture_process_identity",
                side_effect=observed, create=True):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["lifecycle"], "interrupted")
        self.assertEqual(row["cleanup_state"], "failed")
        self.assertTrue(checkout.exists())

    def test_cleanup_requires_positive_zero_container_mount_and_binding_proof(self):
        cases = (
            ("container", {"containers": 1, "mounts": 0}, ()),
            ("mount", {"containers": 0, "mounts": 1}, ()),
            ("unknown", {"containers": None, "mounts": 0}, ()),
            ("binding", {"containers": 0, "mounts": 0},
             (("compose_project", "owned-runtime"),)),
        )
        for name, observed, bindings in cases:
            with self.subTest(name=name):
                checkout = self._checkout(f"reference-{name}")
                self.workspaces.cleanup_reference_observer = (
                    lambda _checkout, _record, result=observed: result)
                self.workspaces.resource_binding_resolver = (
                    lambda _submission, result=bindings: result)
                service = self._service(lambda _descriptor: None)
                accepted = service.submit(self._submission(
                    checkout, request_id=f"reference-{name}-request"))
                self.job_repository.transition(accepted["job_id"], "running")
                self.job_repository.transition(
                    accepted["job_id"], "succeeded", exit_code=0)

                row = service.get(accepted["job_id"])

                self.assertEqual(row["cleanup_state"], "failed")
                self.assertTrue(checkout.exists())

    def test_cleanup_refuses_a_residual_workspace_lease(self):
        checkout = self._checkout("reference-lease")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="reference-lease-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        self.job_repository.connection.execute(
            "INSERT INTO workspace_leases(lease_id,target_namespace,"
            "project_identity,workspace_label,job_id,mode,parallel_safe,"
            "acquired_at,expires_at,heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("lease-residual", "local", "project:ci", checkout.name,
             accepted["job_id"], "isolated", 0,
             "2026-08-31T00:00:00Z", "2026-09-01T00:00:00Z",
             "2026-08-31T00:00:00Z"),
        )

        row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "failed")
        self.assertTrue(checkout.exists())

    def test_cleanup_quarantines_owned_inode_and_never_deletes_path_replacement(self):
        checkout = self._checkout("pathname-aba")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="pathname-aba-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        moved = self.deploy_root / "reviewer-moved-owned"
        real_rename = os.rename
        attacked = False

        def replace_before_quarantine(src, dst, *args, **kwargs):
            nonlocal attacked
            if not attacked and src == checkout.name:
                attacked = True
                real_rename(checkout, moved)
                checkout.mkdir()
                (checkout / "foreign.txt").write_text("must survive")
            return real_rename(src, dst, *args, **kwargs)

        with patch(
                "sandbox.application.workspace_service.os.rename",
                side_effect=replace_before_quarantine):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "failed")
        self.assertEqual((checkout / "foreign.txt").read_text(), "must survive")
        self.assertTrue((moved / "retained-evidence.txt").is_file())

    def test_retry_rematerializes_fresh_disposable_checkout_after_auto_release(self):
        checkout = self._checkout("retry-after-release")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="retry-after-release-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        self.assertEqual(service.get(accepted["job_id"])["cleanup_state"], "completed")
        self.assertFalse(checkout.exists())

        retry = service.retry(
            accepted["job_id"], request_id="retry-rematerialized-request")

        self.assertTrue(checkout.is_dir())
        self.assertEqual((checkout / "retained-evidence.txt").read_text(), "fixture")
        self.assertNotEqual(retry["job_id"], accepted["job_id"])

    def test_non_ci_mode_and_policy_cannot_self_authorize_checkout_deletion(self):
        checkout = self._checkout("non-ci")
        service = self._service(lambda _descriptor: None)
        submission = self._submission(
            checkout, request_id="non-ci-request")
        submission = JobSubmission(**{**submission.__dict__, "kind": "test"})
        accepted = service.submit(submission)
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)

        row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "retained")
        self.assertTrue(checkout.exists())

    def test_supported_index_workspace_remains_compatible_and_is_not_reclassified(self):
        checkout = self._checkout("supported-index")
        namespace = "project-" + __import__("hashlib").sha256(
            b"project:ci").hexdigest()[:24]
        existing, _created = self.workspaces._register(
            project_identity="project:ci", label=checkout.name,
            namespace=namespace, checkout_locator=str(checkout), source="index",
        )
        service = self._service(lambda _descriptor: None)

        accepted = service.submit(self._submission(
            checkout, request_id="supported-index-request",
            mode="persistent", cleanup="retain"))

        row = self.job_repository.get(accepted["job_id"])
        self.assertEqual(row["workspace_id"], existing.workspace_id)
        self.assertEqual(self.workspace_repository.get(existing.workspace_id).source, "index")
        self.assertTrue(checkout.exists())


if __name__ == "__main__":
    unittest.main()
