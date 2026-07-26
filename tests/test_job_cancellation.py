import os
import tempfile
import time
import unittest
from pathlib import Path

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.process import capture_process_identity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage


class CancellationTests(unittest.TestCase):
    def test_cancel_acceptance_before_supervisor_launch_releases_the_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            row, _ = repo.accept(JobSubmission("test", temp, "p", "local", "cancel", ("echo", "ok"), 30,
                SourceIdentity("s")))
            result = service.cancel(row["job_id"], force=True)
            self.assertEqual(result["lifecycle"], "cancelled")
            self.assertEqual(result["termination_reason"], "cancelled_before_process_start")
            repo.close()

    def test_ci_leaf_without_children_cancels_as_leaf(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            row, _ = repo.accept(JobSubmission("ci", temp, "p", "local", "ci-cell",
                ("echo", "ok"), 30, SourceIdentity("s"), workspace_mode="isolated"))
            result = service.cancel(row["job_id"])
            self.assertEqual(result["lifecycle"], "cancelled")
            self.assertEqual(result["termination_reason"], "cancelled_before_process_start")
            repo.close()

    def test_verified_cancel_transitions_to_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            job = service.submit(JobSubmission("test", temp, "p", "local", "cancel", ("/bin/sh", "-c", "sleep 5"), 30, SourceIdentity("s")))
            for _ in range(100):
                state = service.get(job["job_id"])
                if (state.get("process") or {}).get("child_pid"): break
                time.sleep(.03)
            service.cancel(job["job_id"])
            for _ in range(100):
                state = service.get(job["job_id"])
                if state["lifecycle"] == "cancelled": break
                time.sleep(.03)
            self.assertEqual(state["lifecycle"], "cancelled")
            repo.close()

    def test_cancel_rejects_stale_process_identity_without_signalling_a_group(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None,
                                 launcher=lambda _: None)
            row, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "stale", ("echo", "ok"), 30, SourceIdentity("s")))
            observed = capture_process_identity(os.getpid(), nonce="expected", process_group_id=os.getpgrp())
            self.assertIsNotNone(observed)
            repo.transition(row["job_id"], "running")
            repo.put_process_identity(
                row["job_id"], host_boot_id=observed.host_boot_id, supervisor_pid=os.getpid(),
                supervisor_start_identity=observed.start_identity, supervisor_nonce_hash=observed.nonce_hash,
                child_pid=os.getpid(), child_pgid=os.getpgrp(), child_start_identity="stale-identity",
            )
            with self.assertRaisesRegex(RuntimeError, "process_identity_mismatch"):
                service.cancel(row["job_id"], force=True)
            self.assertEqual(repo.get(row["job_id"])["lifecycle"], "running")
            repo.close()

    def test_force_cancel_terminates_child_process_group_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None)
            pid_path = Path(temp) / "descendant.pid"
            job = service.submit(JobSubmission(
                "test", temp, "p", "local", "descendants",
                ("/bin/sh", "-c", f"sleep 30 & echo $! > {pid_path}; wait"), 60, SourceIdentity("s"),
            ))
            for _ in range(100):
                if pid_path.exists():
                    break
                time.sleep(.03)
            self.assertTrue(pid_path.exists())
            descendant_pid = int(pid_path.read_text().strip())
            service.cancel(job["job_id"], force=True)
            for _ in range(100):
                state = service.get(job["job_id"])
                if state["lifecycle"] == "cancelled":
                    break
                time.sleep(.03)
            self.assertEqual(state["lifecycle"], "cancelled")
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)
            repo.close()

    def test_parent_cancel_cancels_each_nonterminal_matrix_child(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repo, JobStorage(temp, free_disk_reserve=0), None, launcher=lambda _: None)
            source = SourceIdentity("s")
            matrix = service.submit_matrix([
                JobSubmission("ci", temp, "p", "local", label, ("echo", label), 30, source,
                              workspace_mode="isolated")
                for label in ("unit", "lint")
            ])
            cancelled = service.cancel(matrix["parent_job_id"], force=True)
            self.assertEqual(len(cancelled["cancelled_children"]), 2)
            parent = service.get(matrix["parent_job_id"])
            self.assertEqual(parent["lifecycle"], "cancelled")
            self.assertEqual({child["lifecycle"] for child in parent["children"]}, {"cancelled"})
            repo.close()
