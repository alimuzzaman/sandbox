import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.application.context import durable_job_dependencies
from sandbox.application.target_service import TargetResolutionError
from sandbox.jobs.models import JobSubmission, SourceIdentity, TargetRequest
from sandbox.jobs.process import ProcessIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class JobReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = JobRepository(Path(self.temp.name) / "jobs.sqlite")
        self.service = JobService(self.repository, JobStorage(self.temp.name, free_disk_reserve=0), None,
                                  launcher=lambda _descriptor: None)

    def tearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def _running(self):
        row, _ = self.repository.accept(JobSubmission(
            "test", self.temp.name, "project", "local", "default", ("echo", "ok"), 60,
            SourceIdentity("source")))
        self.repository.transition(row["job_id"], "running")
        return row

    def test_host_boot_change_is_interrupted_without_claiming_success(self):
        row = self._running()
        self.repository.put_process_identity(row["job_id"], host_boot_id="old-boot", supervisor_pid=101,
            supervisor_start_identity="start", supervisor_nonce_hash="nonce")
        observed = ProcessIdentity("new-boot", 101, "start", "nonce")
        with patch("sandbox.application.job_service.capture_process_identity", return_value=observed):
            result = self.service.reconcile_startup()
        state = self.repository.get(row["job_id"])
        self.assertEqual(result["interrupted"], [row["job_id"]])
        self.assertEqual(state["lifecycle"], "interrupted")
        self.assertEqual(state["termination_reason"], "supervisor_lost")

    def test_missing_child_identity_is_best_available_interruption_evidence(self):
        row = self._running()
        self.repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=101,
            supervisor_start_identity="supervisor", supervisor_nonce_hash="nonce", child_pid=202,
            child_pgid=202, child_start_identity="child")

        def capture(pid):
            if pid == 101:
                return ProcessIdentity("boot", 101, "supervisor", "")
            return None

        with patch("sandbox.application.job_service.capture_process_identity", side_effect=capture):
            self.service.reconcile_startup()
        state = self.repository.get(row["job_id"])
        self.assertEqual(state["lifecycle"], "running")
        self.assertIsNone(state["termination_reason"])

    def test_missing_child_waits_for_verified_supervisor_terminalization_on_read(self):
        row = self._running()
        self.repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=101,
            supervisor_start_identity="supervisor", supervisor_nonce_hash="nonce", child_pid=202,
            child_pgid=202, child_start_identity="child")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.repository.put_heartbeat(row["job_id"], supervisor_at=now,
            health_evidence={"process_alive": True})

        def capture(pid):
            if pid == 101:
                return ProcessIdentity("boot", 101, "supervisor", "")
            return None

        with patch("sandbox.jobs.health.capture_process_identity", side_effect=capture):
            state = self.service.get(row["job_id"])
        self.assertEqual(state["lifecycle"], "running")
        self.assertEqual(state["health"], "active")
        self.assertIn("verified supervisor finalizes",
                      " ".join(state["health_evidence"]["reasons"]))

    def test_child_identity_mismatch_still_interrupts_with_live_supervisor(self):
        row = self._running()
        self.repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=101,
            supervisor_start_identity="supervisor", supervisor_nonce_hash="nonce", child_pid=202,
            child_pgid=202, child_start_identity="child")

        def capture(pid):
            if pid == 101:
                return ProcessIdentity("boot", 101, "supervisor", "")
            return ProcessIdentity("boot", 202, "different-child", "")

        with patch("sandbox.application.job_service.capture_process_identity", side_effect=capture):
            self.service.reconcile_startup()
        state = self.repository.get(row["job_id"])
        self.assertEqual(state["lifecycle"], "interrupted")
        self.assertEqual(state["termination_reason"], "child_process_identity_mismatch")

    def test_on_read_orphaned_child_identity_is_interrupted(self):
        row = self._running()
        self.repository.put_process_identity(row["job_id"], host_boot_id="boot", supervisor_pid=os.getpid(),
            supervisor_start_identity="supervisor", supervisor_nonce_hash="nonce", child_pid=os.getpid(),
            child_pgid=os.getpgrp(), child_start_identity="stale-child")
        with patch("sandbox.jobs.health.capture_process_identity",
                   return_value=ProcessIdentity("boot", os.getpid(), "different", "")):
            state = self.service.get(row["job_id"])
        self.assertEqual(state["lifecycle"], "interrupted")
        self.assertEqual(state["termination_reason"], "orphaned_process_identity")

    def test_stale_heartbeat_and_terminal_rows_keep_truthful_lifecycles(self):
        stale = self._running()
        old = (datetime.now(timezone.utc) - timedelta(seconds=601)).isoformat().replace("+00:00", "Z")
        self.repository.put_heartbeat(stale["job_id"], supervisor_at=old, health_evidence={})
        self.assertEqual(self.service.get(stale["job_id"])["termination_reason"], "supervisor_heartbeat_stale")

        terminal = self._running()
        self.repository.transition(terminal["job_id"], "succeeded", exit_code=0)
        self.assertEqual(self.service.reconcile_startup()["interrupted"], [])
        self.assertEqual(self.repository.get(terminal["job_id"])["lifecycle"], "succeeded")

    def test_service_composition_runs_bounded_startup_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch("sandbox.core._paths.RUNTIME_DIR", Path(temp)), \
                patch.object(JobService, "reconcile_startup",
                             return_value={"ok": True, "interrupted": [],
                                           "released_leases": []}) as reconcile:
            dependencies = durable_job_dependencies()
            self.addCleanup(dependencies["job_service"].repository.close)

        reconcile.assert_called_once_with()

    def test_composed_target_service_uses_one_configured_remote_and_preserves_explicit_precedence(self):
        import sandbox_core as sc

        remotes = {"alpha": {"provisioned": True}}
        def lookup(name):
            if name is None:
                raise AssertionError("name lookup cannot enumerate remotes")
            return remotes.get(name)
        with tempfile.TemporaryDirectory() as temp, \
                patch("sandbox.core._paths.RUNTIME_DIR", Path(temp)), \
                patch.object(sc, "load_project_config", return_value={
                    "root": "/tmp/project", "runtime": {"default": "local"},
                }), \
                patch("sandbox.core._remote.list_remotes", return_value=remotes), \
                patch("sandbox.core._remote.get_remote", side_effect=lookup), \
                patch.object(JobService, "reconcile_startup",
                             return_value={"ok": True, "interrupted": [],
                                           "released_leases": []}):
            dependencies = durable_job_dependencies()
            self.addCleanup(dependencies["job_service"].repository.close)
            service = dependencies["target_service"]

            inferred = service.resolve(TargetRequest("/tmp/project"))
            explicit_local = service.resolve(TargetRequest("/tmp/project", local=True))
            explicit_remote = service.resolve(TargetRequest("/tmp/project", remote="alpha"))

        self.assertEqual((inferred.kind, inferred.remote_name), ("remote", "alpha"))
        self.assertEqual(inferred.sources["remote_selection"], "single-configured")
        self.assertEqual((explicit_local.kind, explicit_local.remote_name), ("local", None))
        self.assertEqual(explicit_local.sources["remote_selection"], "explicit")
        self.assertEqual((explicit_remote.kind, explicit_remote.remote_name), ("remote", "alpha"))
        self.assertEqual(explicit_remote.sources["remote_selection"], "explicit")

    def test_composed_target_service_fails_closed_on_ambiguous_remote_catalog(self):
        import sandbox_core as sc

        remotes = {
            "alpha": {"provisioned": True},
            "beta": {"provisioned": True},
        }
        def lookup(name):
            if name is None:
                raise AssertionError("name lookup cannot enumerate remotes")
            return remotes.get(name)
        with tempfile.TemporaryDirectory() as temp, \
                patch("sandbox.core._paths.RUNTIME_DIR", Path(temp)), \
                patch.object(sc, "load_project_config", return_value={
                    "root": "/tmp/project", "runtime": {"default": "local"},
                }), \
                patch("sandbox.core._remote.list_remotes", return_value=remotes), \
                patch("sandbox.core._remote.get_remote", side_effect=lookup), \
                patch.object(JobService, "reconcile_startup",
                             return_value={"ok": True, "interrupted": [],
                                           "released_leases": []}):
            dependencies = durable_job_dependencies()
            self.addCleanup(dependencies["job_service"].repository.close)
            with self.assertRaisesRegex(TargetResolutionError, "multiple configured remotes"):
                dependencies["target_service"].resolve(TargetRequest("/tmp/project"))
