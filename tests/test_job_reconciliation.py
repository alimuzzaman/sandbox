import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.application.context import durable_job_dependencies
from sandbox.jobs.models import JobSubmission, SourceIdentity
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
        self.assertEqual(state["lifecycle"], "interrupted")
        self.assertEqual(state["termination_reason"], "child_process_missing")
        self.assertEqual(state["output_completeness"], "partial")

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
