import tempfile
import unittest
import fcntl
import os
import signal
import shutil
import sys
import threading
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

    def _fd_path(self, descriptor):
        if sys.platform.startswith("linux"):
            return Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        encoded = fcntl.fcntl(
            descriptor, fcntl.F_GETPATH, b"\0" * 1024)
        return Path(encoded.split(b"\0", 1)[0].decode())

    def _materialization_artifact(self, accepted):
        row = self.job_repository.get(accepted["job_id"])
        record = self.workspace_repository.get(row["workspace_id"])
        return Path(record.metadata["ci_cleanup_authority"]["artifact_locator"])

    def test_prelaunch_failure_retains_row_and_fails_closed_after_emptying_checkout(self):
        checkout = self._checkout("launch-failure")
        service = self._service(
            lambda _descriptor: (_ for _ in ()).throw(OSError("launch failed")))

        with self.assertRaisesRegex(RuntimeError, "supervisor_launch_failed"):
            service.submit(self._submission(
                checkout, request_id="launch-failure-request"))

        row = self.job_repository.list(limit=1)[0]
        self.assertEqual(row["lifecycle"], "failed")
        self.assertEqual(row["termination_reason"], "supervisor_launch_failed")
        self.assertEqual(row["cleanup_state"], "failed")
        self.assertFalse(checkout.exists())
        self.assertEqual(
            self.workspace_repository.get(row["workspace_id"]).lifecycle,
            "indeterminate",
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
                self.assertEqual(row["cleanup_state"], "failed")
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
                "sandbox.application.workspace_service._remove_tree_fd",
                side_effect=OSError("fixture cleanup failed")):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["lifecycle"], "succeeded")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["cleanup_state"], "failed")
        self.assertFalse(checkout.exists())
        quarantines = tuple(self.deploy_root.glob(".sandbox-ci-cleanup/*/owned"))
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

    def test_replay_is_idempotent_after_fail_closed_disposable_cleanup(self):
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
            "failed",
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
        self.assertEqual(projected["lifecycle"], "indeterminate")

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

    def test_background_process_group_blocks_cleanup_after_shell_leader_exits(self):
        checkout = self._checkout("background-group")
        descriptors = []
        service = self._service(descriptors.append)
        submission = self._submission(
            checkout, request_id="background-group-request")
        submission = JobSubmission(**{
            **submission.__dict__,
            "argv": ("/bin/sh", "-c", "sleep 30 >/dev/null 2>&1 &"),
        })
        accepted = service.submit(submission)
        pgid = None
        try:
            with patch(
                    "sandbox.application.workspace_service._observe_cleanup_references",
                    return_value={"containers": 0, "mounts": 0}):
                run_descriptor(descriptors[0])
            row = self.job_repository.get(accepted["job_id"])
            pgid = self.job_repository.snapshot(
                accepted["job_id"])["process"]["child_pgid"]
            self.assertEqual(row["cleanup_state"], "failed")
            self.assertTrue(checkout.exists())
        finally:
            if pgid:
                try:
                    os.killpg(int(pgid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_owned_child_cgroup_must_be_proven_empty_before_cleanup(self):
        checkout = self._checkout("owned-cgroup")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="owned-cgroup-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.put_process_identity(
            accepted["job_id"], host_boot_id="boot", supervisor_pid=101,
            supervisor_start_identity="gone-supervisor",
            supervisor_nonce_hash="nonce", child_pid=202, child_pgid=999999,
            child_cgroup_path="/sandbox/job-fixture",
            child_start_identity="gone-child",
        )
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        with patch(
                "sandbox.application.workspace_service.capture_process_identity",
                return_value=None), patch(
                "sandbox.application.workspace_service._owned_cgroup_empty",
                return_value=False):
            row = service.get(accepted["job_id"])
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

    def test_quarantine_replacement_after_validation_is_never_deleted(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("quarantine-second-aba")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="quarantine-second-aba-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        moved = self.deploy_root / "reviewer-moved-quarantine"
        real_remove = workspace_module._remove_tree_fd

        def replace_quarantine(directory_fd):
            fd_link = (f"/proc/self/fd/{directory_fd}"
                       if sys.platform.startswith("linux")
                       else f"/dev/fd/{directory_fd}")
            if sys.platform.startswith("linux"):
                candidate = Path(os.readlink(fd_link))
            else:
                encoded = fcntl.fcntl(
                    directory_fd, fcntl.F_GETPATH, b"\0" * 1024)
                candidate = Path(encoded.split(b"\0", 1)[0].decode())
            candidate.rename(moved)
            candidate.mkdir()
            (candidate / "foreign.txt").write_text("must survive")
            return real_remove(directory_fd)

        with patch(
                "sandbox.application.workspace_service._remove_tree_fd",
                side_effect=replace_quarantine):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "failed")
        foreign = tuple(self.deploy_root.glob(
            ".sandbox-ci-cleanup/*/owned/foreign.txt"))
        self.assertEqual(len(foreign), 1)
        self.assertEqual(foreign[0].read_text(), "must survive")
        self.assertFalse((moved / "retained-evidence.txt").exists())

    def test_empty_quarantine_aba_never_deletes_path_replacement(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("empty-quarantine-aba")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="empty-quarantine-aba-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        moved = self.deploy_root / "reviewer-empty-owned"
        real_remove = workspace_module._remove_tree_fd

        def replace_empty_quarantine(directory_fd):
            real_remove(directory_fd)
            candidate = self._fd_path(directory_fd)
            candidate.rename(moved)
            candidate.mkdir()

        with patch(
                "sandbox.application.workspace_service._remove_tree_fd",
                side_effect=replace_empty_quarantine):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "failed")
        replacements = tuple(self.deploy_root.glob(
            ".sandbox-ci-cleanup/*/owned"))
        self.assertEqual(len(replacements), 1)
        self.assertTrue(replacements[0].is_dir())
        self.assertTrue(moved.is_dir())

    def test_quarantine_post_recheck_replacement_is_never_deleted(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("quarantine-post-recheck")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="quarantine-post-recheck-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        moved = self.deploy_root / "reviewer-post-recheck-owned"
        real_rmdir = workspace_module.os.rmdir
        attacked = False

        def replace_at_remove(path, *, dir_fd=None):
            nonlocal attacked
            if path == "owned" and dir_fd is not None and not attacked:
                attacked = True
                candidate = self._fd_path(dir_fd) / path
                candidate.rename(moved)
                candidate.mkdir()
            return real_rmdir(path, dir_fd=dir_fd)

        with patch(
                "sandbox.application.workspace_service.os.rmdir",
                side_effect=replace_at_remove):
            row = service.get(accepted["job_id"])

        self.assertEqual(row["cleanup_state"], "failed")
        self.assertFalse(attacked)
        replacements = tuple(self.deploy_root.glob(
            ".sandbox-ci-cleanup/*/owned"))
        self.assertEqual(len(replacements), 1)
        self.assertTrue(replacements[0].is_dir())
        self.assertFalse(moved.exists())

    def test_concurrent_accept_during_delete_cannot_lose_checkout(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("concurrent-accept-delete")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="concurrent-delete-first"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        entered_delete = threading.Event()
        continue_delete = threading.Event()
        submit_finished = threading.Event()
        submit_results = []
        submit_errors = []
        real_remove = workspace_module._remove_tree_fd

        def pause_delete(directory_fd):
            entered_delete.set()
            if not continue_delete.wait(5):
                raise RuntimeError("fixture delete wait expired")
            return real_remove(directory_fd)

        def cleanup_worker():
            service.get(accepted["job_id"])

        def submit_worker():
            try:
                submit_results.append(service.submit(self._submission(
                    checkout, request_id="concurrent-delete-second")))
            except Exception as exc:
                submit_errors.append(exc)
            finally:
                submit_finished.set()

        with patch(
                "sandbox.application.workspace_service._remove_tree_fd",
                side_effect=pause_delete):
            cleanup_thread = threading.Thread(target=cleanup_worker)
            cleanup_thread.start()
            self.assertTrue(entered_delete.wait(5))
            submit_thread = threading.Thread(target=submit_worker)
            submit_thread.start()
            submit_finished.wait(0.2)
            continue_delete.set()
            cleanup_thread.join(5)
            submit_thread.join(5)

        self.assertFalse(cleanup_thread.is_alive())
        self.assertFalse(submit_thread.is_alive())
        self.assertEqual(submit_results, [])
        self.assertEqual(len(submit_errors), 1)
        self.assertEqual(len(self.job_repository.list(limit=10)), 1)
        self.assertFalse(checkout.exists())

    def test_retry_rematerializes_after_fail_closed_disposable_cleanup(self):
        checkout = self._checkout("retry-after-release")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="retry-after-release-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        self.assertEqual(service.get(accepted["job_id"])["cleanup_state"], "failed")
        self.assertFalse(checkout.exists())

        retry = service.retry(
            accepted["job_id"], request_id="retry-rematerialized-request")

        self.assertTrue(checkout.is_dir())
        self.assertEqual((checkout / "retained-evidence.txt").read_text(), "fixture")
        self.assertNotEqual(retry["job_id"], accepted["job_id"])
        self.assertEqual(len(tuple(
            self.workspace_repository.index_path.parent.glob(
                "ci-materializations/*.tar.gz"))), 1)

    def test_artifact_swap_after_hash_never_restores_replacement(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("restore-artifact-swap")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="restore-artifact-swap-first"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        self.assertEqual(service.get(accepted["job_id"])["cleanup_state"],
                         "failed")
        artifact = self._materialization_artifact(accepted)
        verified = artifact.with_name("reviewer-verified-restore.tar.gz")
        replacement = b"unrelated replacement"
        real_digest = workspace_module._file_sha256
        attacked = False

        def swap_after_hash(target):
            nonlocal attacked
            digest = real_digest(target)
            if not attacked:
                attacked = True
                artifact.rename(verified)
                artifact.write_bytes(replacement)
            return digest

        with patch(
                "sandbox.application.workspace_service._file_sha256",
                side_effect=swap_after_hash):
            with self.assertRaisesRegex(Exception, "artifact entry changed"):
                service.retry(
                    accepted["job_id"], request_id="restore-artifact-swap-retry")

        self.assertFalse(checkout.exists())
        self.assertTrue(verified.is_file())
        self.assertEqual(artifact.read_bytes(), replacement)

    def test_failed_artifact_restore_rolls_back_checkout(self):
        checkout = self._checkout("restore-artifact-failure")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="restore-artifact-failure-first"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        service.get(accepted["job_id"])
        self._materialization_artifact(accepted).write_bytes(b"invalid archive")

        with self.assertRaises(Exception):
            service.retry(
                accepted["job_id"], request_id="restore-artifact-failure-retry")

        self.assertFalse(checkout.exists())

    def test_retirement_swap_after_hash_never_deletes_replacement(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("retire-artifact-swap")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="retire-artifact-swap-first"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        service.get(accepted["job_id"])
        artifact = self._materialization_artifact(accepted)
        verified = artifact.with_name("reviewer-verified-retirement.tar.gz")
        replacement = b"unrelated replacement"
        real_digest = workspace_module._file_sha256
        attacked = False

        def swap_after_hash(target):
            nonlocal attacked
            digest = real_digest(target)
            if not attacked:
                attacked = True
                artifact.rename(verified)
                artifact.write_bytes(replacement)
            return digest

        with patch(
                "sandbox.application.workspace_service._file_sha256",
                side_effect=swap_after_hash):
            with self.assertRaisesRegex(
                    Exception, "open descriptor identity"):
                service.cleanup(accepted["job_id"])

        self.assertTrue(verified.is_file())
        self.assertEqual(artifact.read_bytes(), replacement)
        row = self.job_repository.get(accepted["job_id"])
        record = self.workspace_repository.get(row["workspace_id"])
        self.assertFalse(record.metadata.get("ci_materialization_retired", False))

    def test_retirement_post_recheck_replacement_is_never_deleted(self):
        from sandbox.application import workspace_service as workspace_module

        checkout = self._checkout("retirement-post-recheck")
        service = self._service(lambda _descriptor: None)
        accepted = service.submit(self._submission(
            checkout, request_id="retirement-post-recheck-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        service.get(accepted["job_id"])
        verified = self.deploy_root / "reviewer-post-recheck-artifact.tar.gz"
        replacement = b"unrelated post-recheck replacement"
        real_unlink = workspace_module.os.unlink
        attacked = False

        def replace_at_unlink(path, *, dir_fd=None):
            nonlocal attacked
            if dir_fd is not None and not attacked:
                attacked = True
                candidate = self._fd_path(dir_fd) / path
                candidate.rename(verified)
                candidate.write_bytes(replacement)
            return real_unlink(path, dir_fd=dir_fd)

        with patch(
                "sandbox.application.workspace_service.os.unlink",
                side_effect=replace_at_unlink):
            with self.assertRaises(Exception):
                service.cleanup(accepted["job_id"])

        self.assertFalse(attacked)
        self.assertFalse(verified.exists())
        artifact = self._materialization_artifact(accepted)
        self.assertTrue(artifact.is_file())
        self.assertNotEqual(artifact.read_bytes(), replacement)
        row = self.job_repository.get(accepted["job_id"])
        record = self.workspace_repository.get(row["workspace_id"])
        self.assertFalse(record.metadata.get("ci_materialization_retired", False))

    def test_materialization_archive_is_bounded_inventoried_and_retained_without_safe_removal(self):
        checkout = self._checkout("archive-lifecycle")
        service = self._service(lambda _descriptor: None)
        with patch(
                "sandbox.application.workspace_service.MAX_CI_MATERIALIZATION_ARCHIVE_BYTES",
                1, create=True):
            with self.assertRaisesRegex(Exception, "materialization archive"):
                service.submit(self._submission(
                    checkout, request_id="archive-bounded-request"))
        self.assertEqual(tuple(
            self.workspace_repository.index_path.parent.glob(
                "ci-materializations/*.tar.gz")), ())

        checkout = self._checkout("archive-retention")
        accepted = service.submit(self._submission(
            checkout, request_id="archive-retention-request"))
        self.job_repository.transition(accepted["job_id"], "running")
        self.job_repository.transition(
            accepted["job_id"], "succeeded", exit_code=0)
        service.get(accepted["job_id"])
        accepted_row = self.job_repository.get(accepted["job_id"])
        projection = self.workspace_repository.ownership_projection()["records"]
        owned = next(item for item in projection
                     if item["workspace_id"] == accepted_row["workspace_id"])
        self.assertEqual(owned["retained_materializations"]["count"], 1)
        with self.assertRaisesRegex(
                Exception, "cannot retire an archive by open descriptor identity"):
            service.retention_sweep(retention_days=0)
        self.assertEqual(len(tuple(
            self.workspace_repository.index_path.parent.glob(
                "ci-materializations/*.tar.gz"))), 1)

    def test_materialization_archive_refuses_when_disk_reserve_cannot_be_kept(self):
        checkout = self._checkout("archive-reserve")
        service = self._service(lambda _descriptor: None)
        usage = shutil._ntuple_diskusage(total=100, used=99, free=1)
        with patch("sandbox.application.workspace_service.shutil.disk_usage",
                   return_value=usage):
            with self.assertRaisesRegex(Exception, "disk reserve"):
                service.submit(self._submission(
                    checkout, request_id="archive-reserve-request"))

    def test_unpublished_materialization_archive_is_retained_when_safe_removal_is_unavailable(self):
        checkout = self._checkout("archive-index-failure")
        service = self._service(lambda _descriptor: None)
        with patch.object(
                self.workspaces, "_register",
                side_effect=RuntimeError("fixture index failure")):
            with self.assertRaisesRegex(
                    Exception, "cannot retire an archive by open descriptor identity"):
                service.submit(self._submission(
                    checkout, request_id="archive-index-failure-request"))
        self.assertEqual(len(tuple(
            self.workspace_repository.index_path.parent.glob(
                "ci-materializations/*.tar.gz"))), 1)

    def test_mountinfo_detects_checkout_used_as_a_bind_source_elsewhere(self):
        from sandbox.application.workspace_service import _mountinfo_reference_count

        checkout = Path("/deploy/workspace")
        mountinfo = "\n".join((
            "1 0 8:1 / / rw - ext4 /dev/sda1 rw",
            "2 1 8:1 /deploy/workspace /srv/consumer rw - ext4 /dev/sda1 rw",
            "3 1 0:42 / /deploy/workspace/nested rw - tmpfs tmpfs rw",
            "4 1 8:1 /deploy /srv/all-deployments rw - ext4 /dev/sda1 rw",
        ))
        self.assertEqual(_mountinfo_reference_count(
            mountinfo, checkout, device=(8, 1)), 3)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux mountinfo proof")
    def test_linux_mountinfo_probe_reads_current_namespace_without_unknown(self):
        from sandbox.application.workspace_service import _observe_mount_references

        with tempfile.TemporaryDirectory() as temporary:
            self.assertIsInstance(
                _observe_mount_references(Path(temporary)), int)

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
