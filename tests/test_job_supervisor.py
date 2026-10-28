import tempfile
import time
import unittest
import os
import json
from pathlib import Path
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.jobs.models import JobSubmission, SourceIdentity
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.output import OutputError
from sandbox.jobs.supervisor import _child_identity_context, run_descriptor


class SupervisorTests(unittest.TestCase):
    def test_fixed_child_context_does_not_enumerate_or_copy_environment(self):
        class FixedEnvironment(dict):
            def __iter__(self):
                raise AssertionError("parent environment must not be enumerated")

            def items(self):
                raise AssertionError("parent environment must not be copied")

            def copy(self):
                raise AssertionError("parent environment must not be copied")

        environment = FixedEnvironment({"UNCHANGED": "value"})
        with patch("sandbox.jobs.supervisor.os.environ", environment):
            with _child_identity_context({"job_id": "a" * 32,
                                          "request_id": "apply-1"}):
                self.assertEqual(environment["SANDBOX_DURABLE_JOB_ID"], "a" * 32)
                self.assertEqual(environment["UNCHANGED"], "value")
            self.assertNotIn("SANDBOX_DURABLE_JOB_ID", environment)

    @staticmethod
    def _wait_terminal(service, job_id):
        for _ in range(200):
            state = service.get(job_id)
            if state["lifecycle"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                return state
            time.sleep(.03)
        raise AssertionError("job did not reach a terminal state")

    def test_fast_child_does_not_depend_on_a_racy_getpgid_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            with patch("sandbox.jobs.supervisor.os.getpgid", side_effect=AssertionError("must not be called")):
                submitted = service.submit(JobSubmission("test", temp, "p", "local", "fast",
                    ("/bin/sh", "-c", "true"), 20, SourceIdentity("source")))
                for _ in range(100):
                    state = service.get(submitted["job_id"])
                    if state["lifecycle"] in {"succeeded", "failed", "timed_out"}:
                        break
                    time.sleep(.05)
            self.assertEqual(state["lifecycle"], "succeeded", state)
            self.assertIsNotNone(state["heartbeat"]["supervisor_at"])
            repository.close()

    def test_detached_process_drains_output_and_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "default",
                ("/bin/sh", "-c", "printf start; sleep .1; printf done"), 20, SourceIdentity("source")))
            for _ in range(100):
                state = service.get(submitted["job_id"])
                if state["lifecycle"] in {"succeeded", "failed", "timed_out"}:
                    break
                time.sleep(.05)
            self.assertEqual(state["lifecycle"], "succeeded", state)
            self.assertEqual(state["output_completeness"], "complete")
            self.assertIsNotNone(state["integrity_sha256"])
            self.assertTrue(all(item["complete"] for item in state["output"]))
            self.assertEqual(service.read_output(submitted["job_id"])["data"], "startdone")
            repository.close()

    def test_opt_in_stall_cancellation_records_a_distinct_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "stalled",
                ("/bin/sh", "-c", "sleep 10"), 20, SourceIdentity("source"),
                stall_seconds=1, cancel_on_stall=True))
            state = self._wait_terminal(service, submitted["job_id"])
            self.assertEqual(state["lifecycle"], "cancelled", state)
            self.assertEqual(state["termination_reason"], "cancelled_on_stall")
            repository.close()

    def test_deadline_terminates_the_owned_process_group_and_records_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            pid_path = Path(temp) / "deadline-descendant.pid"
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "deadline",
                ("/bin/sh", "-c", f"sleep 30 & echo $! > {pid_path}; wait"), 1, SourceIdentity("source")))
            for _ in range(100):
                if pid_path.exists():
                    break
                time.sleep(.03)
            self.assertTrue(pid_path.exists())
            descendant_pid = int(pid_path.read_text().strip())
            state = self._wait_terminal(service, submitted["job_id"])
            self.assertEqual(state["lifecycle"], "timed_out", state)
            self.assertEqual(state["termination_reason"], "deadline_exceeded")
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)
            repository.close()

    def test_nonzero_child_exit_is_preserved_as_a_failed_result(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "nonzero",
                ("/bin/sh", "-c", "exit 7"), 20, SourceIdentity("source")))
            state = self._wait_terminal(service, submitted["job_id"])
            self.assertEqual(state["lifecycle"], "failed", state)
            self.assertEqual(state["exit_code"], 7)
            self.assertEqual(state["termination_reason"], "exit_nonzero")
            repository.close()

    def test_sigkill_exit_is_classified_without_claiming_oom(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            service = JobService(repository, JobStorage(temp, free_disk_reserve=0), components=None)
            submitted = service.submit(JobSubmission("test", temp, "p", "local", "killed",
                ("/bin/sh", "-c", "exit 137"), 20, SourceIdentity("source")))
            state = self._wait_terminal(service, submitted["job_id"])
            self.assertEqual(state["lifecycle"], "failed", state)
            self.assertEqual(state["exit_code"], 137)
            self.assertEqual(state["termination_reason"], "process_killed")
            repository.close()

    def test_output_storage_failure_is_a_durable_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            storage = JobStorage(temp, free_disk_reserve=0)
            row, _ = repository.accept(JobSubmission("test", temp, "p", "local", "storage",
                ("/bin/sh", "-c", "printf retained-output"), 20, SourceIdentity("source")))
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor = {
                "job_id": row["job_id"], "registry_path": str(repository.path),
                "runtime_dir": str(storage.root.parent), "argv": ["/bin/sh", "-c", "printf retained-output"],
                "cwd": temp, "deadline_seconds": 20, "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64, "environment": None,
                "execution_runtime": "host",
            }
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps(descriptor))
            with patch("sandbox.jobs.supervisor.JobOutputStore.append",
                       side_effect=OutputError("durable output storage failed")):
                self.assertEqual(run_descriptor(descriptor_path), 1)
            state = repository.snapshot(row["job_id"])
            self.assertEqual(state["lifecycle"], "failed")
            self.assertEqual(state["termination_reason"], "output_storage_failed")
            self.assertEqual(state["output_completeness"], "write_failed")
            repository.close()

    def test_supervisor_failure_persists_only_redacted_bounded_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            repository = JobRepository(Path(temp) / "registry.sqlite")
            row, _ = repository.accept(JobSubmission(
                "test", temp, "p", "local", "supervisor-redaction",
                ("/bin/true",), 20, SourceIdentity("source"),
            ))
            storage = JobStorage(temp, free_disk_reserve=0)
            descriptor = {
                "job_id": row["job_id"], "registry_path": str(repository.path),
                "runtime_dir": str(storage.root.parent), "argv": ["/bin/true"],
                "cwd": temp, "deadline_seconds": 20, "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64, "environment": None,
                "execution_runtime": "host",
            }
            descriptor_path = Path(temp) / "descriptor.json"
            descriptor_path.write_text(json.dumps(descriptor))
            canary = "synthetic-candidate-value"
            with patch(
                "sandbox.jobs.supervisor.capture_process_identity",
                side_effect=RuntimeError(f"token={canary}"),
            ):
                self.assertEqual(run_descriptor(descriptor_path), 1)
            result_json = repository.get(row["job_id"])["result_json"]
            self.assertFalse(canary in result_json)
            self.assertIn("[REDACTED]", result_json)
            repository.close()


class FailureCleanupTests(unittest.TestCase):
    def _exercise_failure(self, mode):
        import subprocess
        from contextlib import ExitStack
        from unittest.mock import Mock
        from sandbox.jobs import supervisor
        from sandbox.jobs.models import Lifecycle
        from sandbox.jobs.output import JobOutputStore
        from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
        from tests.subprocess_support import synthetic_environment

        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "jobs.sqlite")
            self.addCleanup(repo.close)
            storage = JobStorage(temp, free_disk_reserve=0)
            scheduler = JobScheduler(repo, min_free_memory_mb=0, min_free_disk_mb=0)
            script = "sleep 30 & printf 'retained \\n'; sleep .2; printf trigger; wait"
            if mode == "force":
                script = "trap '' TERM; " + script
            elif mode == "exited-leader":
                script = "sleep 30 & printf trigger; exit 0"
            elif mode == "fast":
                script = "printf trigger"
            argv = ["/bin/sh", "-c", script]
            def accept():
                return repo.accept(JobSubmission(
                    "test", temp, "p", "local", "same", tuple(argv), 20,
                    SourceIdentity("source")))[0]
            row = accept()
            competitor = accept()
            scheduler.acquire(row)
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps({
                "job_id": row["job_id"], "registry_path": str(repo.path),
                "runtime_dir": temp, "argv": argv, "cwd": temp,
                "deadline_seconds": 20, "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64,
                "environment": dict(synthetic_environment()), "execution_runtime": "host",
            }))
            popen = subprocess.Popen
            append = JobOutputStore.append
            persist = JobRepository.put_process_identity
            release = JobRepository.release_leases
            capture = supervisor.capture_process_identity
            stop = supervisor._stop_failed_child
            signal_group = supervisor._signal_group
            commands, released, signals = [], [], []
            cleanup = Mock()

            def launch(*args, **kwargs):
                command = popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    commands.append(command)
                return command

            def failing_append(output, stream, chunk, **kwargs):
                if b"trigger" not in chunk:
                    return append(output, stream, chunk, **kwargs)
                if mode == "generic":
                    raise RuntimeError("synthetic post-spawn error")
                if mode == "oserror":
                    raise OSError("synthetic output write error")
                raise OutputError("storage pressure" if mode == "pressure" else "synthetic output error")

            def failing_persist(repository, job_id, **kwargs):
                if mode == "persist" and kwargs.get("child_pid"):
                    raise OSError("synthetic identity persistence error")
                return persist(repository, job_id, **kwargs)

            def failing_capture(pid, **kwargs):
                if mode == "capture" and commands and pid == commands[0].pid:
                    return None
                return capture(pid, **kwargs)

            def checked_signal(pgid, number):
                self.assertEqual(pgid, commands[0].pid)
                self.assertIsNone(commands[0].returncode)
                with self.assertRaises(WorkspaceBusy):
                    scheduler.acquire(competitor)
                signals.append(number)
                if mode == "uncertain":
                    raise PermissionError("synthetic signal denial")
                return signal_group(pgid, number)

            def checked_release(repository, job_id):
                self.assertIsNotNone(commands[0].returncode)
                self.assertFalse(any(not state.startswith("Z") for _, state in
                                     supervisor._group_members(commands[0].pid)))
                released.append(job_id)
                return release(repository, job_id)

            try:
                with ExitStack() as stack:
                    for target, replacement in (
                        ("sandbox.jobs.supervisor.subprocess.Popen", launch),
                        ("sandbox.jobs.supervisor.JobOutputStore.append", failing_append),
                        ("sandbox.jobs.supervisor.JobRepository.put_process_identity", failing_persist),
                        ("sandbox.jobs.supervisor.JobRepository.release_leases", checked_release),
                        ("sandbox.jobs.supervisor.capture_process_identity", failing_capture),
                        ("sandbox.jobs.supervisor._signal_group", checked_signal),
                        ("sandbox.application.workspace_service.finalize_terminal_workspace", cleanup),
                    ):
                        stack.enter_context(patch(target, replacement))
                    self.assertEqual(run_descriptor(descriptor_path), 1)
                self.assertEqual(len(commands), 1)
                state = repo.snapshot(row["job_id"])
                reason = ("supervisor_error" if mode in {"generic", "oserror", "capture", "persist"}
                          else "storage_pressure" if mode == "pressure" else "output_storage_failed")
                self.assertEqual(state["termination_reason"], reason)
                self.assertNotEqual(state["output_completeness"], "complete")
                self.assertTrue(commands[0].stdout.closed)
                self.assertTrue(commands[0].stderr.closed)
                if mode == "uncertain":
                    self.assertEqual(state["lifecycle"], "cancelling")
                    self.assertIsNone(state["finished_at"])
                    self.assertFalse(json.loads(state["result_json"])["owned_work_stopped"])
                    self.assertEqual(json.loads(state["result_json"])["stop_error"], "PermissionError")
                    self.assertEqual(released, [])
                    cleanup.assert_not_called()
                    with self.assertRaises(ValueError):
                        repo.transition(row["job_id"], Lifecycle.INTERRUPTED)
                    with self.assertRaises(ValueError):
                        release(repo, row["job_id"])
                    # Simulate a later observer proving the supervisor lost.
                    service = JobService(repo, storage, components=None, scheduler=scheduler)
                    with patch("sandbox.application.job_service.capture_process_identity", return_value=None), \
                         patch("sandbox.application.job_service.process_absence_proven", return_value=True):
                        service.reconcile_startup()
                    self.assertEqual(repo.get(row["job_id"])["lifecycle"], "cancelling")
                    self.assertEqual(len(scheduler.active()), 1)
                    with self.assertRaises(WorkspaceBusy):
                        scheduler.acquire(competitor)
                else:
                    self.assertEqual(state["lifecycle"], "failed")
                    self.assertTrue(json.loads(state["result_json"])["owned_work_stopped"])
                    self.assertEqual(released, [row["job_id"]])
                    cleanup.assert_called_once()
                    self.assertEqual(scheduler.active(), [])
                    scheduler.acquire(competitor)
                    if mode == "force":
                        self.assertEqual(signals, [15, 9])
                if mode not in {"capture", "persist", "fast", "exited-leader"}:
                    retained = b"".join(path.read_bytes() for path in
                                         (job_dir / "output" / "stdout").glob("*.bin"))
                    self.assertIn(b"retained", retained)
                    self.assertFalse(any(item["complete"] for item in state["output"]))
            finally:
                if commands:
                    self.assertTrue(stop(commands[0], 0), "fixture-owned group did not stop")

    def test_failures_stop_owned_group_before_releasing_authority(self):
        for mode in ("write", "pressure", "generic", "oserror", "capture", "persist",
                     "force", "fast", "exited-leader"):
            with self.subTest(mode=mode):
                self._exercise_failure(mode)

    def test_unproven_stop_retains_fences_across_reconciliation(self):
        self._exercise_failure("uncertain")

    def test_reaped_child_cannot_authorize_signalling_a_reused_group(self):
        from unittest.mock import Mock
        from sandbox.jobs.supervisor import _stop_failed_child
        command = Mock(pid=12345, returncode=0)
        with patch("sandbox.jobs.supervisor.os.killpg") as killpg:
            self.assertFalse(_stop_failed_child(command, 0))
            killpg.assert_called_once_with(12345, 0)
        with patch("sandbox.jobs.supervisor.os.killpg", side_effect=ProcessLookupError):
            self.assertTrue(_stop_failed_child(command, 0))
