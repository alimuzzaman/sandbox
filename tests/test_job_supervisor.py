import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sandbox.application.job_service import JobService
from sandbox.jobs import supervisor
from sandbox.jobs.models import JobSubmission, Lifecycle, SourceIdentity
from sandbox.jobs.output import OutputError
from sandbox.jobs.registry import JobRepository
from sandbox.jobs.scheduler import JobScheduler, WorkspaceBusy
from sandbox.jobs.storage import JobStorage
from sandbox.jobs.supervisor import _child_identity_context, run_descriptor
from tests.subprocess_support import synthetic_environment


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

    def test_closed_pipes_live_child_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            self.addCleanup(repo.close)
            storage = JobStorage(temp, free_disk_reserve=0)
            scheduler = JobScheduler(repo, min_free_disk_mb=0, min_free_memory_mb=0)
            argv = ["/bin/sh", "-c", "exec 1>&- 2>&-; sleep 30"]
            row, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "closed-pipes", tuple(argv),
                1, SourceIdentity("source"),
            ))
            scheduler.acquire(row)
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps({
                "job_id": row["job_id"],
                "registry_path": str(repo.path),
                "runtime_dir": temp,
                "argv": argv,
                "cwd": temp,
                "deadline_seconds": 1,
                "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64,
                "environment": dict(synthetic_environment()),
                "execution_runtime": "host",
            }))
            descriptor_path.chmod(0o600)

            commands = []
            real_popen = subprocess.Popen

            def tracking_launch(*args, **kwargs):
                cmd = real_popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    commands.append(cmd)
                return cmd

            try:
                with patch("sandbox.jobs.supervisor.subprocess.Popen", tracking_launch):
                    rc = run_descriptor(descriptor_path)
                self.assertEqual(rc, 124)
                snap = repo.snapshot(row["job_id"])
                self.assertEqual(snap["lifecycle"], "timed_out")
                self.assertEqual(snap["termination_reason"], "deadline_exceeded")
                self.assertEqual(snap["output_completeness"], "complete")
                self.assertIsNotNone(snap["finished_at"])
                self.assertTrue(commands)
                self.assertFalse(any(not state.startswith("Z") for _, state in
                                     supervisor._group_members(commands[0].pid)))
            finally:
                if commands and commands[0].returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(commands[0].pid, 9)
                    commands[0].wait(timeout=1)

    def test_exited_leader_descendant_held_pipes_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            self.addCleanup(repo.close)
            storage = JobStorage(temp, free_disk_reserve=0)
            scheduler = JobScheduler(repo, min_free_disk_mb=0, min_free_memory_mb=0)
            argv = ["/bin/sh", "-c", "sleep 30 & printf retained; exit 0"]
            row, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "exited-leader-held-pipes", tuple(argv),
                1, SourceIdentity("source"),
            ))
            scheduler.acquire(row)
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps({
                "job_id": row["job_id"],
                "registry_path": str(repo.path),
                "runtime_dir": temp,
                "argv": argv,
                "cwd": temp,
                "deadline_seconds": 1,
                "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64,
                "environment": dict(synthetic_environment()),
                "execution_runtime": "host",
                "artifact_paths": ["missing-result.txt"],
            }))
            descriptor_path.chmod(0o600)

            commands = []
            real_popen = subprocess.Popen

            def tracking_launch(*args, **kwargs):
                cmd = real_popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    commands.append(cmd)
                return cmd

            try:
                with patch("sandbox.jobs.supervisor.subprocess.Popen", tracking_launch), \
                        patch("sandbox.jobs.supervisor.collect_artifacts") as collect:
                    rc = run_descriptor(descriptor_path)
                collect.assert_not_called()
                self.assertEqual(rc, 124)
                snap = repo.snapshot(row["job_id"])
                self.assertEqual(snap["lifecycle"], "timed_out")
                self.assertEqual(snap["termination_reason"], "deadline_exceeded")
                self.assertEqual(snap["exit_code"], 124)
                self.assertEqual(snap["output_completeness"], "complete")
                self.assertIsNotNone(snap["finished_at"])
                retained = b"".join(path.read_bytes() for path in
                                     (job_dir / "output" / "stdout").glob("*.bin"))
                self.assertIn(b"retained", retained)
                self.assertTrue(commands)
                self.assertFalse(any(not state.startswith("Z") for _, state in
                                     supervisor._group_members(commands[0].pid)))
            finally:
                if commands and commands[0].returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(commands[0].pid, 9)
                    commands[0].wait(timeout=1)

    def test_short_lived_descendant_preserves_final_output(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            self.addCleanup(repo.close)
            storage = JobStorage(temp, free_disk_reserve=0)
            scheduler = JobScheduler(repo, min_free_disk_mb=0, min_free_memory_mb=0)
            argv = ["/bin/sh", "-c", "(sleep 0.2; printf late) & printf early; exit 0"]
            row, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "short-lived-descendant", tuple(argv),
                5, SourceIdentity("source"),
            ))
            scheduler.acquire(row)
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps({
                "job_id": row["job_id"],
                "registry_path": str(repo.path),
                "runtime_dir": temp,
                "argv": argv,
                "cwd": temp,
                "deadline_seconds": 5,
                "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64,
                "environment": dict(synthetic_environment()),
                "execution_runtime": "host",
            }))
            descriptor_path.chmod(0o600)

            commands = []
            real_popen = subprocess.Popen

            def tracking_launch(*args, **kwargs):
                cmd = real_popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    commands.append(cmd)
                return cmd

            try:
                with patch("sandbox.jobs.supervisor.subprocess.Popen", tracking_launch):
                    rc = run_descriptor(descriptor_path)
                self.assertEqual(rc, 0)
                snap = repo.snapshot(row["job_id"])
                self.assertEqual(snap["lifecycle"], "succeeded")
                self.assertEqual(snap["exit_code"], 0)
                self.assertEqual(snap["output_completeness"], "complete")
                self.assertIsNotNone(snap["integrity_sha256"])
                stdout_bytes = b"".join(path.read_bytes() for path in
                                        (job_dir / "output" / "stdout").glob("*.bin"))
                self.assertIn(b"early", stdout_bytes)
                self.assertIn(b"late", stdout_bytes)
                self.assertTrue(commands)
                self.assertFalse(any(not state.startswith("Z") for _, state in
                                     supervisor._group_members(commands[0].pid)))
            finally:
                if commands and commands[0].returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(commands[0].pid, 9)
                    commands[0].wait(timeout=1)

    def test_stop_uncertainty_on_timeout_keeps_durable_exclusion(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            self.addCleanup(repo.close)
            storage = JobStorage(temp, free_disk_reserve=0)
            scheduler = JobScheduler(repo, min_free_disk_mb=0, min_free_memory_mb=0)
            argv = ["/bin/sh", "-c", "sleep 30"]
            row, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "timeout-uncertain", tuple(argv),
                1, SourceIdentity("source"),
            ))
            competitor, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "timeout-uncertain", tuple(argv),
                1, SourceIdentity("source"),
            ))
            scheduler.acquire(row)
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps({
                "job_id": row["job_id"],
                "registry_path": str(repo.path),
                "runtime_dir": temp,
                "argv": argv,
                "cwd": temp,
                "deadline_seconds": 1,
                "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64,
                "environment": dict(synthetic_environment()),
                "execution_runtime": "host",
            }))
            descriptor_path.chmod(0o600)

            commands = []
            real_popen = subprocess.Popen

            def tracking_launch(*args, **kwargs):
                cmd = real_popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    commands.append(cmd)
                return cmd

            def failing_signal(pgid, number):
                raise PermissionError("synthetic timeout signal failure")

            try:
                with patch("sandbox.jobs.supervisor.subprocess.Popen", tracking_launch),                      patch("sandbox.jobs.supervisor._signal_group", failing_signal):
                    rc = run_descriptor(descriptor_path)
                self.assertEqual(rc, 1)
                snap = repo.snapshot(row["job_id"])
                self.assertEqual(snap["lifecycle"], "cancelling")
                self.assertIsNone(snap["finished_at"])
                detail = json.loads(snap["result_json"])
                self.assertFalse(detail["owned_work_stopped"])
                self.assertEqual(detail["stop_error"], "PermissionError")
                with self.assertRaises(WorkspaceBusy):
                    scheduler.acquire(competitor)
                service = JobService(repo, storage, components=None, scheduler=scheduler)
                with patch("sandbox.application.job_service.capture_process_identity", return_value=None),                      patch("sandbox.application.job_service.process_absence_proven", return_value=True):
                    service.reconcile_startup()
                self.assertEqual(repo.get(row["job_id"])["lifecycle"], "cancelling")
                with self.assertRaises(WorkspaceBusy):
                    scheduler.acquire(competitor)
            finally:
                if commands and commands[0].returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(commands[0].pid, 9)
                    commands[0].wait(timeout=1)

    def test_read_errors_cannot_seal_complete_output(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = JobRepository(Path(temp) / "registry.sqlite")
            self.addCleanup(repo.close)
            storage = JobStorage(temp, free_disk_reserve=0)
            scheduler = JobScheduler(repo, min_free_disk_mb=0, min_free_memory_mb=0)
            argv = ["/bin/sh", "-c", "printf output; sleep 30"]
            row, _ = repo.accept(JobSubmission(
                "test", temp, "p", "local", "read-error", tuple(argv),
                5, SourceIdentity("source"),
            ))
            scheduler.acquire(row)
            job_dir = storage.job_dir(row["job_id"], create=True)
            descriptor_path = job_dir / "descriptor.json"
            descriptor_path.write_text(json.dumps({
                "job_id": row["job_id"],
                "registry_path": str(repo.path),
                "runtime_dir": temp,
                "argv": argv,
                "cwd": temp,
                "deadline_seconds": 5,
                "cancel_grace_seconds": 1,
                "nonce_hash": "0" * 64,
                "environment": dict(synthetic_environment()),
                "execution_runtime": "host",
            }))
            descriptor_path.chmod(0o600)

            commands = []
            real_popen = subprocess.Popen

            def tracking_launch(*args, **kwargs):
                cmd = real_popen(*args, **kwargs)
                if kwargs.get("start_new_session"):
                    commands.append(cmd)
                return cmd

            real_read = os.read

            def failing_read(fd, n):
                chunk = real_read(fd, n)
                if b"output" in chunk:
                    raise OSError("synthetic pipe read error")
                return chunk

            try:
                with patch("sandbox.jobs.supervisor.subprocess.Popen", tracking_launch),                      patch("sandbox.jobs.supervisor.os.read", failing_read):
                    rc = run_descriptor(descriptor_path)
                self.assertEqual(rc, 1)
                snap = repo.snapshot(row["job_id"])
                self.assertEqual(snap["lifecycle"], "failed")
                self.assertEqual(snap["termination_reason"], "supervisor_error")
                self.assertEqual(snap["output_completeness"], "partial")
                self.assertNotEqual(snap["output_completeness"], "complete")
                self.assertIsNone(snap["integrity_sha256"])
                self.assertFalse(any(item["complete"] for item in snap["output"]))
                self.assertTrue(commands)
                self.assertFalse(any(not state.startswith("Z") for _, state in
                                     supervisor._group_members(commands[0].pid)))
            finally:
                if commands and commands[0].returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(commands[0].pid, 9)
                    commands[0].wait(timeout=1)


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


class NormalExitCessationTests(unittest.TestCase):
    """A job that ends by itself records the stop proof only when nothing it started still runs."""

    def run_job(self, script, *, deadline=20, runner=run_descriptor):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repo = self.repo = JobRepository(temp / "registry.sqlite")
        self.addCleanup(repo.close)
        storage = JobStorage(temp, free_disk_reserve=0)
        argv = ["/bin/sh", "-c", script]
        row, _ = repo.accept(JobSubmission("test", str(temp), "p", "local", "normal-exit",
            tuple(argv), deadline, SourceIdentity("source")))
        JobScheduler(repo, min_free_disk_mb=0, min_free_memory_mb=0).acquire(row)
        descriptor = storage.job_dir(row["job_id"], create=True) / "descriptor.json"
        descriptor.write_text(json.dumps({
            "job_id": row["job_id"], "registry_path": str(repo.path), "runtime_dir": str(temp),
            "argv": argv, "cwd": str(temp), "deadline_seconds": deadline,
            "cancel_grace_seconds": 1, "nonce_hash": "0" * 64,
            "environment": dict(synthetic_environment()), "execution_runtime": "host",
            # Real descriptors always name the job; children inherit it.
            "authoritative_context": {"job_id": row["job_id"]},
        }))
        descriptor.chmod(0o600)
        runner(descriptor)
        snap = repo.snapshot(row["job_id"])
        return snap, json.loads(snap["result_json"] or "null")

    def leftover(self, path):
        """Kill a process a fixture left behind, by the PID it wrote."""
        for _ in range(100):
            if path.exists() and path.read_text().strip():
                break
            time.sleep(.03)
        pid = int(path.read_text().strip())

        def kill():
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)
        self.addCleanup(kill)
        return pid

    def test_a_job_that_ended_by_itself_records_its_owned_work_stopped(self):
        for script, lifecycle, code in (("exit 0", "succeeded", 0), ("exit 255", "failed", 255),
                                        ("(sleep .2) & exit 3", "failed", 3)):
            with self.subTest(script=script):
                snap, result = self.run_job(script)
                self.assertEqual((snap["lifecycle"], snap["exit_code"]), (lifecycle, code))
                self.assertEqual(result["owned_work_stopped"], True, result)
                self.assertIsNone(result["stop_error"])
                self.assertIsInstance(result["owned_descendants"], list)
                self.assertNotIn("unproven", result)

    def test_a_child_left_in_the_jobs_session_is_not_proven_stopped(self):
        pid_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "pid"
        # A new process group in the same session, output closed: the group check alone misses it.
        snap, result = self.run_job(
            f"perl -e 'setpgrp(0,0); open F, \">\", shift; print F $$; close F; sleep 30'"
            f" {pid_path} </dev/null >/dev/null 2>&1 & sleep .3; exit 255")
        pid = self.leftover(pid_path)
        os.kill(pid, 0)  # still running
        self.assertEqual(snap["lifecycle"], "failed")
        self.assertIs(result["owned_work_stopped"], False, result)
        self.assertIn(result["unproven"], {"session_member_running", "descendant_running",
                                           "job_environment_running"})

    def test_a_child_holding_the_jobs_output_is_not_proven_stopped(self):
        pid_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "pid"
        snap, result = self.run_job(
            f"perl -e 'use POSIX; POSIX::setsid(); open F, \">\", shift; print F $$; close F; sleep 30'"
            f" {pid_path} & sleep .3; exit 255")
        self.leftover(pid_path)
        self.assertEqual(snap["lifecycle"], "failed")
        self.assertEqual(snap["output_completeness"], "partial")
        self.assertIs(result["owned_work_stopped"], False, result)
        self.assertEqual(result["unproven"], "output_pipes_held")

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
    def test_a_double_fork_daemon_is_not_proven_stopped(self):
        pid_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "pid"
        # New session, output closed, never seen in the group: only its environment names the job.
        snap, result = self.run_job(
            "perl -e 'use POSIX; fork and exit; POSIX::setsid(); fork and exit;"
            " open STDIN, \"</dev/null\"; open STDOUT, \">/dev/null\"; open STDERR, \">/dev/null\";"
            f" open F, \">\", shift; print F $$; close F; sleep 30' {pid_path}; sleep .3; exit 255")
        self.leftover(pid_path)
        self.assertIs(result["owned_work_stopped"], False, result)
        self.assertEqual(result["unproven"], "job_environment_running")

    def test_a_live_group_member_is_not_proven_stopped(self):
        command = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(command.wait)
        self.addCleanup(command.kill)
        self.assertEqual(supervisor._normal_exit_unproven(command, "a" * 32, set(), True),
                         "group_member_running")

    def test_a_process_still_naming_the_job_is_found_by_its_environment(self):
        proc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for pid, environment in (("4242", b"A=1\0SANDBOX_DURABLE_JOB_ID=" + b"a" * 32 + b"\0"),
                                 ("4343", b"SANDBOX_DURABLE_JOB_ID=" + b"b" * 32 + b"\0"),
                                 ("4444", b"SANDBOX_DURABLE_JOB_ID=" + b"a" * 32 + b"x\0")):
            (proc / pid).mkdir()
            (proc / pid / "environ").write_bytes(environment)
        (proc / "self").mkdir()
        with patch.object(supervisor, "_PROC", proc):
            self.assertEqual(supervisor._job_environment_holders("a" * 32, 1), [4242])
            self.assertEqual(supervisor._job_environment_holders("c" * 32, 1), [])
            command = subprocess.Popen(["true"], start_new_session=True)
            command.wait()
            self.assertEqual(supervisor._normal_exit_unproven(command, "a" * 32, set(), True),
                             "job_environment_running")
        with patch.object(supervisor, "_PROC", proc / "missing"):
            self.assertEqual(supervisor._job_environment_holders("a" * 32, 1), [])

    @staticmethod
    def fake_stat(directory, start, ppid=0):
        """A /proc/<pid>/stat with parent ``ppid`` (field 4) and start time ``start`` (field 22)."""
        directory.mkdir(exist_ok=True)
        (directory / "stat").write_text(
            f"{directory.name} (a b) S {ppid} " + "0 " * 17 + f"{start} 0 0\n")

    def test_an_unreadable_process_counts_only_when_it_started_with_or_after_the_job(self):
        proc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fake_stat(proc / "500", 1000)  # the command, an unreaped zombie
        (proc / "500" / "environ").write_bytes(b"")
        self.fake_stat(proc / "4242", 900)
        (proc / "4242" / "environ").mkdir()  # reading it fails, as for a non-dumpable process
        with patch.object(supervisor, "_PROC", proc):
            self.assertEqual(supervisor._job_environment_holders("a" * 32, 500), [])
            for start in (1000, 1001):
                with self.subTest(start=start):
                    self.fake_stat(proc / "4242", start)
                    self.assertIsNone(supervisor._job_environment_holders("a" * 32, 500))
            (proc / "4242" / "stat").write_text("garbage")
            self.assertIsNone(supervisor._job_environment_holders("a" * 32, 500))
            (proc / "4242" / "stat").unlink()  # it exited after the listing
            self.assertEqual(supervisor._job_environment_holders("a" * 32, 500), [])
            self.fake_stat(proc / "4242", 900)
            (proc / "500" / "stat").unlink()  # no command start time: nothing is provably older
            self.assertIsNone(supervisor._job_environment_holders("a" * 32, 500))

    NON_DUMPABLE = "import ctypes; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)"  # PR_SET_DUMPABLE, 0

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
    def test_an_unrelated_non_dumpable_process_started_earlier_does_not_deny_the_proof(self):
        # As OpenSSH's per-connection process of the owner is, while an exec client waits.
        helper = subprocess.Popen(
            [sys.executable, "-c", self.NON_DUMPABLE + "; print('ready', flush=True);"
             " import time; time.sleep(60)"],
            stdout=subprocess.PIPE, text=True, env={"PATH": "/usr/bin:/bin"},
            start_new_session=True)
        self.addCleanup(helper.wait)
        self.addCleanup(helper.kill)
        self.assertEqual(helper.stdout.readline().strip(), "ready")
        helper.stdout.close()
        with self.assertRaises(PermissionError):
            Path(f"/proc/{helper.pid}/environ").read_bytes()
        snap, result = self.run_job("exit 255")
        self.assertEqual((snap["lifecycle"], snap["exit_code"]), ("failed", 255))
        self.assertIs(result["owned_work_stopped"], True, result)

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
    def test_a_job_daemon_that_becomes_non_dumpable_is_not_proven_stopped(self):
        pid_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "pid"
        daemon = ("import os, sys, time\n"
                  "if os.fork(): sys.exit(0)\n"
                  "os.setsid()\n"
                  "if os.fork(): sys.exit(0)\n"
                  "null = os.open(os.devnull, os.O_RDWR)\n"
                  "for fd in (0, 1, 2): os.dup2(null, fd)\n"
                  + self.NON_DUMPABLE + "\n"
                  "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                  "time.sleep(30)\n")
        script_path = pid_path.with_name("daemon.py")
        script_path.write_text(daemon)
        snap, result = self.run_job(
            f"{sys.executable} {script_path} {pid_path}; "
            f"while [ ! -s {pid_path} ]; do sleep .05; done; exit 255")
        self.leftover(pid_path)
        self.assertEqual(snap["lifecycle"], "failed")
        self.assertIs(result["owned_work_stopped"], False, result)
        self.assertEqual(result["unproven"], "process_environment_unavailable")

    def test_an_environment_read_that_blocks_cannot_hold_up_the_jobs_end(self):
        # Reading /proc/<pid>/environ takes the target's mmap lock and can block
        # with no timeout. A FIFO with no writer blocks the same way.
        proc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (proc / "4242").mkdir()
        fifo = proc / "4242" / "environ"
        os.mkfifo(fifo)

        def unblock():
            with contextlib.suppress(OSError):
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        # Releases the blocked read after the test, and on code without a limit.
        timer = threading.Timer(6, unblock)
        timer.start()
        self.addCleanup(unblock)
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with patch.object(supervisor, "_PROC", proc), \
                patch.object(supervisor, "_ENVIRONMENT_SCAN_SECONDS", .3, create=True):
            snap, result = self.run_job("exit 0")
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((snap["lifecycle"], snap["exit_code"]), ("succeeded", 0))
        self.assertIs(result["owned_work_stopped"], False, result)
        self.assertEqual(result["unproven"], "process_environment_scan_timed_out")
        with self.repo.transaction() as connection:
            self.assertEqual(connection.execute(
                "SELECT (SELECT count(*) FROM workspace_leases WHERE job_id=?)"
                " + (SELECT count(*) FROM host_capacity_leases WHERE job_id=?)",
                (snap["job_id"], snap["job_id"])).fetchone()[0], 0)

    def test_a_holder_that_appears_while_a_pass_runs_is_still_found(self):
        # A process naming the job forks a child and exits after the listing
        # was taken: only a later listing shows the child.
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "4141").mkdir()
        (root / "4141" / "environ").write_bytes(b"A=1\0")

        class RacingProc(type(root)):
            def iterdir(self):
                entries = list(super().iterdir())
                if not (root / "4242").exists():
                    (root / "4242").mkdir()
                    (root / "4242" / "environ").write_bytes(
                        b"SANDBOX_DURABLE_JOB_ID=" + b"a" * 32 + b"\0")
                return iter(entries)

        with patch.object(supervisor, "_PROC", RacingProc(root)):
            self.assertEqual(supervisor._job_environment_holders("a" * 32, 1), [4242])

    # A fake /proc for the parent-chain rule: the supervisor (this process) is a
    # child of 90001, a child of init; the command 90500 started at tick 1000.
    COMMAND = 90500

    def lineage_proc(self):
        proc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fake_stat(proc / str(os.getpid()), 10, ppid=90001)
        # Readable, so only membership in the adopters denies a child of theirs.
        self.fake_process(proc, 90001, 5, 1, b"A=1\0")
        self.fake_process(proc, 1, 1, 0, b"A=1\0")
        self.fake_process(proc, self.COMMAND, 1000, os.getpid(), b"")
        return proc

    def fake_process(self, proc, pid, start, ppid, environment=None):
        """``environment`` None: reading it fails, as for a non-dumpable process."""
        self.fake_stat(proc / str(pid), start, ppid)
        if environment is None:
            (proc / str(pid) / "environ").mkdir()
        else:
            (proc / str(pid) / "environ").write_bytes(environment)

    def holders(self, proc):
        with patch.object(supervisor, "_PROC", proc):
            return supervisor._job_environment_holders("a" * 32, self.COMMAND)

    def test_an_unreadable_process_started_during_the_job_is_judged_by_its_parents(self):
        job = b"SANDBOX_DURABLE_JOB_ID=" + b"a" * 32 + b"\0"
        cases = (
            # A Tailscale SSH incubator: its root tailscaled started long before.
            ("unreadable parent started earlier", [(90600, 900, 1, None)], []),
            ("readable parent started earlier", [(90600, 900, 1, b"A=1\0")], []),
            ("walks through a newer parent", [(90600, 1005, 90550, b"A=1\0"),
                                             (90550, 800, 1, None)], []),
            ("newer parent names the job", [(90600, 1005, 90550, job), (90550, 800, 1, None)],
             [90600, 90700]),
            ("child of the command", [], None),
            ("child of the supervisor", [], None),
            ("adopted by a supervisor ancestor", [], None),
            ("adopted by init", [], None),
            ("newer unreadable parent adopted by init", [(90600, 1005, 1, None)], None),
        )
        parents = {"child of the command": self.COMMAND, "child of the supervisor": os.getpid(),
                   "adopted by a supervisor ancestor": 90001, "adopted by init": 1}
        for name, chain, expected in cases:
            with self.subTest(name):
                proc = self.lineage_proc()
                self.fake_process(proc, 90700, 1010, parents.get(name, 90600))
                for pid, start, ppid, environment in chain:
                    self.fake_process(proc, pid, start, ppid, environment)
                self.assertEqual(self.holders(proc), expected)

    def test_an_unreadable_process_whose_parents_cannot_be_traced_counts(self):
        def proc_with(*chain):
            proc = self.lineage_proc()
            self.fake_process(proc, 90700, 1100, 90600)
            for pid, start, ppid in chain:
                self.fake_process(proc, pid, start, ppid, b"A=1\0")
            return proc

        with self.subTest("missing parent"):
            self.assertIsNone(self.holders(proc_with()))
        with self.subTest("garbled parent stat"):
            proc = proc_with((90600, 900, 1))
            (proc / "90600" / "stat").write_text("garbage")
            self.assertIsNone(self.holders(proc))
        with self.subTest("parent started after its child: a reused PID"):
            self.assertIsNone(self.holders(proc_with((90600, 1120, 1))))
        with self.subTest("supervisor ancestry unreadable"):
            proc = proc_with((90600, 900, 1))
            (proc / "90001" / "stat").unlink()
            self.assertIsNone(self.holders(proc))
        for depth, expected in ((63, []), (64, None)):
            with self.subTest(f"{depth} newer parents, then an older one"):
                chain = [(90600 - i, 1099 - i, 90600 - i - 1) for i in range(depth)]
                chain.append((90600 - depth, 900, 1))
                self.assertEqual(self.holders(proc_with(*chain)), expected)

        with self.subTest("the process exits during the walk"):
            proc = proc_with()  # its parent is missing, which alone counts
            read = supervisor._parent_and_start

            def exits_after_its_stat_is_read(directory):
                result = read(directory)
                if directory.name == "90700":
                    for entry in ("environ", "stat"):
                        path = directory / entry
                        path.rmdir() if path.is_dir() else path.unlink()
                    directory.rmdir()
                return result
            with patch.object(supervisor, "_parent_and_start", exits_after_its_stat_is_read):
                self.assertEqual(self.holders(proc), [])

    @staticmethod
    def proc_stat(pid):
        """(parent PID, start ticks) from the real /proc/<pid>/stat."""
        fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
        return int(fields[1]), int(fields[19])

    def non_dumpable_session_helper(self, fifo, pid_path):
        """Model a Tailscale SSH session: a non-dumpable process started before the
        job forks a non-dumpable child once ``fifo`` is written, during the job."""
        os.mkfifo(fifo)
        script = (self.NON_DUMPABLE + "\n"
                  "import os, sys, time\n"
                  "print('ready', flush=True)\n"
                  "open(sys.argv[1]).read()\n"
                  "if os.fork() == 0:\n"
                  "    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)\n"
                  "    open(sys.argv[2], 'w').write(str(os.getpid()))\n"
                  "    time.sleep(60)\n"
                  "    os._exit(0)\n"
                  "time.sleep(60)\n")
        helper = subprocess.Popen([sys.executable, "-c", script, str(fifo), str(pid_path)],
                                  stdout=subprocess.PIPE, text=True, env={"PATH": "/usr/bin:/bin"},
                                  start_new_session=True)
        self.addCleanup(helper.wait)
        self.addCleanup(helper.kill)
        self.assertEqual(helper.stdout.readline().strip(), "ready")
        helper.stdout.close()
        return helper

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
    def test_a_non_dumpable_session_opened_during_the_job_under_an_older_parent_does_not_deny(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        fifo, pid_path, start_path = temp / "go", temp / "pid", temp / "start"
        helper = self.non_dumpable_session_helper(fifo, pid_path)
        snap, result = self.run_job(
            f"cut -d' ' -f22 /proc/$$/stat > {start_path}; echo go > {fifo}; "
            f"while [ ! -s {pid_path} ]; do sleep .05; done; exit 255")
        pid = self.leftover(pid_path)
        with self.assertRaises(PermissionError):
            Path(f"/proc/{pid}/environ").read_bytes()
        self.assertEqual((snap["lifecycle"], snap["exit_code"]), ("failed", 255))
        self.assertIs(result["owned_work_stopped"], True, result)
        session, parent = self.proc_stat(pid), self.proc_stat(helper.pid)
        command_start = int(start_path.read_text())
        self.assertEqual(session[0], helper.pid)
        self.assertGreaterEqual(session[1], command_start)  # it started during the job
        self.assertLess(parent[1], command_start)

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
    def test_a_non_dumpable_daemon_adopted_by_a_subreaper_ancestor_is_not_proven_stopped(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pid_path = temp / "pid"
        daemon = ("import os, sys, time\n"
                  "if os.fork(): sys.exit(0)\n"
                  "os.setsid()\n"
                  "parent = os.getpid()\n"
                  "if os.fork(): sys.exit(0)\n"
                  "null = os.open(os.devnull, os.O_RDWR)\n"
                  "for fd in (0, 1, 2): os.dup2(null, fd)\n"
                  + self.NON_DUMPABLE + "\n"
                  "while os.getppid() == parent: time.sleep(.01)\n"
                  "open(sys.argv[1], 'w').write(f'{os.getpid()} {os.getppid()}')\n"
                  "time.sleep(30)\n")
        script_path = temp / "daemon.py"
        script_path.write_text(daemon)
        # The supervisor runs under a child subreaper, as under systemd or a
        # container init that sets PR_SET_CHILD_SUBREAPER.
        wrapper_script = ("import ctypes, subprocess, sys\n"
                          "ctypes.CDLL(None).prctl(36, 1, 0, 0, 0)\n"
                          "sys.exit(subprocess.call([sys.executable, '-m',"
                          " 'sandbox.jobs.supervisor', sys.argv[1]]))\n")
        wrappers = []

        def under_subreaper(descriptor):
            root = str(Path(__file__).resolve().parents[1])
            wrapper = subprocess.Popen(
                [sys.executable, "-c", wrapper_script, str(descriptor)], cwd=root,
                env=synthetic_environment({"PYTHONPATH": root, "PYTHONDONTWRITEBYTECODE": "1"}))
            wrappers.append(wrapper.pid)
            self.assertEqual(wrapper.wait(60), 1)  # the supervisor's code for a failed job

        snap, result = self.run_job(
            f"{sys.executable} {script_path} {pid_path}; "
            f"while [ ! -s {pid_path} ]; do sleep .05; done; exit 255", runner=under_subreaper)
        pid, adopter = map(int, pid_path.read_text().split())
        pid_path.write_text(str(pid))
        self.leftover(pid_path)
        self.assertEqual(adopter, wrappers[0])  # adopted by the subreaper, not init
        self.assertEqual(snap["lifecycle"], "failed")
        self.assertIs(result["owned_work_stopped"], False, result)
        self.assertEqual(result["unproven"], "process_environment_unavailable")

    @unittest.skipUnless(Path("/proc/self/environ").exists(), "needs Linux /proc")
    def test_a_non_dumpable_child_of_the_command_counts(self):
        pid_path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "pid"
        child = (self.NON_DUMPABLE + "; import sys, time;"
                 " open(sys.argv[1], 'w').write(str(__import__('os').getpid())); time.sleep(30)")
        command = subprocess.Popen(
            ["/bin/sh", "-c", f"{sys.executable} -c \"{child}\" {pid_path} & wait"],
            env={"PATH": "/usr/bin:/bin"}, start_new_session=True)
        self.addCleanup(command.wait)

        def kill():
            with contextlib.suppress(ProcessLookupError):
                os.killpg(command.pid, 9)
        self.addCleanup(kill)
        pid = self.leftover(pid_path)
        self.assertEqual(self.proc_stat(pid)[0], command.pid)
        with self.assertRaises(PermissionError):
            Path(f"/proc/{pid}/environ").read_bytes()
        self.assertIsNone(supervisor._job_environment_holders("a" * 32, command.pid))
