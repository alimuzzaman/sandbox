"""Behavioral tests for spec 004's per-instance WP-CLI jobs.

These tests use temporary artifact directories and mocked process handles for
adversarial paths. Recorded local Docker and Herd evidence lives with Spec 004.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, call, patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sandbox.commands import jobs  # noqa: E402


class TestWpCliJobs(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="sb-wp-jobs-")
        self.root = Path(self.tempdir.name)
        self.instance = "unit"
        self.job_dir = self.root / ".sb-jobs"

    def tearDown(self):
        self.tempdir.cleanup()

    def _paths(self, jid: str):
        self.job_dir.mkdir(parents=True, exist_ok=True)
        return tuple(self.job_dir / f"job_{jid}.{suffix}" for suffix in ("log", "status", "pid"))

    def test_herd_launch_uses_python_session_isolation_not_external_setsid(self):
        process = SimpleNamespace(pid=4242)
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=True), \
                patch.object(jobs, "_herd_wp_cmd", return_value=["wp"]), \
                patch.object(jobs.secrets, "token_hex", return_value="a" * 16), \
                patch.object(jobs.subprocess, "Popen", return_value=process) as popen:
            jid = jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        self.assertEqual(jid, "a" * 16)
        self.assertEqual(popen.call_args.args[0][:2], ["sh", "-c"])
        self.assertNotIn("setsid", popen.call_args.args[0])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(self._paths(jid)[2].read_text(), "4242")

    def test_docker_launch_returns_after_durable_supervisor_acceptance(self):
        process = SimpleNamespace(pid=5252)
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs.secrets, "token_hex", return_value="2" * 16), \
                patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                patch.object(jobs, "compose_file", return_value=self.root / "unit.yml"), \
                patch.object(jobs.subprocess, "Popen", return_value=process) as popen:
            jid = jobs.launch_job(self.instance, ["eval", "sleep(30);"])

        log, status, handle = self._paths(jid)
        self.assertEqual(jid, "2" * 16)
        self.assertTrue(log.exists())
        self.assertFalse(status.exists())
        self.assertEqual(handle.read_text(), "launch:5252")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        launched = popen.call_args.args[0]
        self.assertEqual(launched[:3], ["sh", "-c", ANY])
        self.assertIn("container_absent", launched[2])
        self.assertIn("if cleanup_and_record 143", launched[2])
        self.assertIn("sandbox-unit", launched)
        self.assertIn("sb-job-unit-" + jid, launched)
        self.assertIn("run", launched)
        self.assertIn("wpcli", launched)

    def test_docker_launch_failure_leaves_no_known_job_or_artifacts(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs.secrets, "token_hex", return_value="3" * 16), \
                patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                patch.object(jobs, "compose_file", return_value=self.root / "unit.yml"), \
                patch.object(jobs.subprocess, "Popen", side_effect=OSError("closed")):
            with self.assertRaises(OSError):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        self.assertFalse(any(path.exists() for path in self._paths("3" * 16)))

    def test_docker_marker_failure_stops_supervisor_and_removes_partial_artifacts(self):
        real_write = jobs._write_new_artifact
        process = SimpleNamespace(pid=5353, wait=lambda timeout: 143)

        def fail_handle(path, value):
            if path.suffix == ".pid":
                raise OSError("marker unavailable")
            return real_write(path, value)

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs.secrets, "token_hex", return_value="6" * 16), \
                patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                patch.object(jobs, "compose_file", return_value=self.root / "unit.yml"), \
                patch.object(jobs.subprocess, "Popen", return_value=process), \
                patch.object(jobs, "_write_new_artifact", side_effect=fail_handle), \
                patch.object(jobs, "_remove_docker_job_container"), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_herd_group_running", side_effect=[True, False]), \
                patch.object(jobs.os, "killpg") as killpg:
            with self.assertRaises(OSError):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        self.assertEqual(
            killpg.call_args_list,
            [call(5353, jobs.signal.SIGTERM), call(5353, jobs.signal.SIGKILL)],
        )
        self.assertFalse(any(path.exists() for path in self._paths("6" * 16)))

    def test_docker_marker_failure_retains_evidence_when_cleanup_is_unknown(self):
        real_write = jobs._write_new_artifact
        process = SimpleNamespace(pid=5454, wait=lambda timeout: 143)

        def fail_handle(path, value):
            if path.suffix == ".pid":
                raise OSError("marker unavailable")
            return real_write(path, value)

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs.secrets, "token_hex", return_value="0" * 16), \
                patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                patch.object(jobs, "compose_file", return_value=self.root / "unit.yml"), \
                patch.object(jobs.subprocess, "Popen", return_value=process), \
                patch.object(jobs, "_write_new_artifact", side_effect=fail_handle), \
                patch.object(jobs, "_remove_docker_job_container"), \
                patch.object(jobs, "_docker_container_running", return_value=None), \
                patch.object(jobs, "_herd_group_running", return_value=False), \
                patch.object(jobs.os, "killpg"):
            with self.assertRaises(OSError):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        log, status, handle = self._paths("0" * 16)
        self.assertIn("cleanup could not be verified", log.read_text())
        self.assertFalse(status.exists())
        self.assertFalse(handle.exists())

    def test_marker_failure_timeout_uses_kill_and_retains_orphan_evidence(self):
        real_write = jobs._write_new_artifact

        def wait(*, timeout):
            raise jobs.subprocess.TimeoutExpired("supervisor", 2)

        process = SimpleNamespace(pid=5555, wait=wait)

        def fail_handle(path, value):
            if path.suffix == ".pid":
                raise OSError("marker unavailable")
            return real_write(path, value)

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs.secrets, "token_hex", return_value="1" * 16), \
                patch.object(jobs, "project_name", return_value="sandbox-unit"), \
                patch.object(jobs, "compose_file", return_value=self.root / "unit.yml"), \
                patch.object(jobs.subprocess, "Popen", return_value=process), \
                patch.object(jobs, "_write_new_artifact", side_effect=fail_handle), \
                patch.object(jobs, "_remove_docker_job_container"), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_herd_group_running", side_effect=[True, None]), \
                patch.object(jobs.os, "killpg") as killpg:
            with self.assertRaises(OSError):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        self.assertEqual(
            killpg.call_args_list,
            [call(5555, jobs.signal.SIGTERM),
             call(5555, jobs.signal.SIGKILL)],
        )
        self.assertIn(
            "cleanup could not be verified",
            self._paths("1" * 16)[0].read_text(),
        )

    def test_herd_marker_failure_retains_validated_receipt_until_group_absence(self):
        real_write = jobs._write_new_artifact
        process = SimpleNamespace(pid=5656, wait=lambda timeout: 143)

        def fail_handle(path, value):
            if path.suffix == ".pid":
                raise OSError("marker unavailable")
            return real_write(path, value)

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=True), \
                patch.object(jobs, "_herd_wp_cmd", return_value=["wp"]), \
                patch.object(jobs.secrets, "token_hex", return_value="2" * 16), \
                patch.object(jobs.subprocess, "Popen", return_value=process), \
                patch.object(jobs, "_write_new_artifact", side_effect=fail_handle), \
                patch.object(jobs, "_herd_group_running", return_value=None), \
                patch.object(jobs.os, "killpg"):
            with self.assertRaises(OSError):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        log, status, handle = self._paths("2" * 16)
        with patch.object(jobs, "wp_dir", return_value=self.root):
            self.assertEqual(
                jobs._read_cleanup_receipt(self.instance, "2" * 16),
                ("herd", 5656),
            )
        self.assertIn("cleanup could not be verified", log.read_text())
        self.assertFalse(status.exists())
        self.assertFalse(handle.exists())

    def test_docker_cleanup_receipt_retries_both_owned_boundaries(self):
        jid = "3" * 16
        log, status, _ = self._paths(jid)
        log.touch()
        receipt = self.job_dir / f"job_{jid}.cleanup"
        receipt.write_text(f"docker-cleanup-v1|5757|5757|sb-job-{self.instance}-{jid}")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_supervisor_running", return_value=True), \
                patch.object(jobs, "_herd_group_running", side_effect=[True, False]), \
                patch.object(jobs, "_remove_docker_job_container") as remove, \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs.os, "killpg") as killpg:
            result = jobs.job_status(self.instance, jid)

        killpg.assert_called_once_with(5757, jobs.signal.SIGTERM)
        remove.assert_called_once_with(self.instance, jid)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 1)
        self.assertFalse(receipt.exists())

    def test_herd_cleanup_receipt_retries_and_verifies_entire_group(self):
        jid = "a" * 16
        log, status, _ = self._paths(jid)
        log.touch()
        receipt = self.job_dir / f"job_{jid}.cleanup"
        receipt.write_text("herd-cleanup-v1|5858|5858")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=True), \
                patch.object(jobs, "_herd_launcher_running", return_value=True), \
                patch.object(jobs, "_herd_group_running", side_effect=[True, False]), \
                patch.object(jobs.os, "killpg") as killpg:
            result = jobs.job_status(self.instance, jid)

        killpg.assert_called_once_with(5858, jobs.signal.SIGTERM)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(status.read_text(), "1")
        self.assertFalse(receipt.exists())

    def test_cleanup_receipt_pid_reuse_never_signals_mismatched_groups(self):
        for kind, pid in (("herd", 5959), ("docker", 6060)):
            with self.subTest(kind=kind):
                jid = ("b" if kind == "herd" else "c") * 16
                log, status, _ = self._paths(jid)
                log.touch()
                receipt = self.job_dir / f"job_{jid}.cleanup"
                value = f"{kind}-cleanup-v1|{pid}|{pid}"
                if kind == "docker":
                    value += f"|sb-job-{self.instance}-{jid}"
                receipt.write_text(value)
                with patch.object(jobs, "wp_dir", return_value=self.root), \
                        patch.object(jobs, "_is_herd_instance", return_value=kind == "herd"), \
                        patch.object(jobs, "_herd_launcher_running", return_value=False), \
                        patch.object(jobs, "_docker_supervisor_running", return_value=False), \
                        patch.object(jobs, "_herd_group_running", return_value=True), \
                        patch.object(jobs, "_remove_docker_job_container"), \
                        patch.object(jobs, "_docker_container_running", return_value=False), \
                        patch.object(jobs.os, "killpg") as killpg:
                    result = jobs.job_status(self.instance, jid)

                killpg.assert_not_called()
                self.assertEqual(result["status"], "running")
                self.assertTrue(receipt.exists())
                self.assertFalse(status.exists())

    def test_immediate_poll_treats_verified_docker_supervisor_as_running(self):
        jid = "4" * 16
        log, _, handle = self._paths(jid)
        log.touch()
        handle.write_text("launch:4242")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_docker_launcher_running", return_value=True):
            result = jobs.job_status(self.instance, jid)

        self.assertEqual(result["status"], "running")

    def test_timed_out_docker_observation_is_unknown_not_completed(self):
        with patch.object(
            jobs.subprocess, "run", side_effect=jobs.subprocess.TimeoutExpired("inspect", 2),
        ):
            self.assertIsNone(jobs._docker_container_running(self.instance, "4" * 16))

    def test_malformed_docker_observation_is_unknown_not_dead(self):
        observation = SimpleNamespace(returncode=0, stdout="maybe", stderr="")
        with patch.object(jobs.subprocess, "run", return_value=observation):
            self.assertIsNone(jobs._docker_container_running(self.instance, "4" * 16))

    def test_timed_out_supervisor_probe_is_not_an_authorized_handle(self):
        jid = "7" * 16
        _, _, handle = self._paths(jid)
        handle.write_text("launch:4242")
        with patch.object(
            jobs.subprocess, "run", side_effect=jobs.subprocess.TimeoutExpired("ps", 1),
        ):
            self.assertIsNone(jobs._docker_launcher_running(self.instance, jid, handle))

    def test_probe_timeout_never_reconciles_launch_as_terminal(self):
        jid = "7" * 16
        log, status, handle = self._paths(jid)
        log.touch()
        handle.write_text("launch:4242")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_docker_launcher_running", return_value=None):
            result = jobs.job_status(self.instance, jid)

        self.assertEqual(result["status"], "running")
        self.assertFalse(status.exists())

    def test_absent_container_before_transition_never_reconciles_terminal(self):
        jid = "7" * 16
        log, status, handle = self._paths(jid)
        log.touch()
        handle.write_text("launch:4242")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_docker_launcher_running", return_value=False):
            result = jobs.job_status(self.instance, jid)

        self.assertEqual(result["status"], "running")
        self.assertFalse(status.exists())

    def test_immediate_docker_kill_reaps_supervisor_then_container_boundary(self):
        jid = "5" * 16
        log, status, handle = self._paths(jid)
        log.touch()
        handle.write_text("launch:4242")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", side_effect=[True, False]), \
                patch.object(jobs, "_docker_launcher_running", side_effect=[True, False, False]), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_remove_docker_job_container"), \
                patch.object(jobs, "_herd_group_running", return_value=False), \
                patch.object(jobs.os, "killpg") as killpg, \
                patch.object(jobs.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            result = jobs.kill_job(self.instance, jid)

        killpg.assert_called_once_with(4242, jobs.signal.SIGTERM)
        self.assertTrue(result["killed"])
        self.assertEqual(status.read_text(), "143")

    def test_docker_launch_kill_refuses_reused_supervisor_pgid(self):
        jid = "f" * 16
        log, status, handle = self._paths(jid)
        log.touch()
        handle.write_text("launch:6161")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=True), \
                patch.object(jobs, "_docker_launcher_running", return_value=False), \
                patch.object(jobs, "_herd_group_running", return_value=True), \
                patch.object(jobs, "_remove_docker_job_container"), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs.os, "killpg") as killpg:
            result = jobs.kill_job(self.instance, jid)

        killpg.assert_not_called()
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["killed"])
        self.assertFalse(status.exists())

    def test_unknown_kill_is_a_noop_without_creating_artifacts(self):
        jid = "b" * 16
        with patch.object(jobs, "wp_dir", return_value=self.root):
            result = jobs.kill_job(self.instance, jid)

        self.assertEqual(result, {"job_id": jid, "status": "not_found", "killed": False})
        self.assertFalse(self.job_dir.exists())

    def test_status_reconciles_a_dead_herd_process_without_terminal_artifact(self):
        jid = "c" * 16
        _, status, pid = self._paths(jid)
        pid.write_text("4242")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=True), \
                patch.object(jobs, "_herd_group_running", return_value=False):
            result = jobs.job_status(self.instance, jid)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(status.read_text(), "1")

    def test_kill_persists_cancelled_only_after_herd_group_is_gone(self):
        jid = "d" * 16
        _, status, pid = self._paths(jid)
        pid.write_text("4242")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=True), \
                patch.object(jobs, "_herd_launcher_running", return_value=True), \
                patch.object(jobs, "_herd_group_running", side_effect=[True, False, False]), \
                patch.object(jobs.os, "killpg") as killpg:
            result = jobs.kill_job(self.instance, jid)

        killpg.assert_called_once_with(4242, jobs.signal.SIGTERM)
        self.assertTrue(result["killed"])
        self.assertEqual(status.read_text(), "143")

    def test_normal_herd_kill_refuses_pid_reuse_identity_mismatch(self):
        jid = "d" * 16
        log, status, pid = self._paths(jid)
        log.touch()
        pid.write_text("6262")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=True), \
                patch.object(jobs, "_herd_group_running", return_value=True), \
                patch.object(jobs, "_herd_launcher_running", return_value=False), \
                patch.object(jobs.os, "killpg") as killpg:
            result = jobs.kill_job(self.instance, jid)

        killpg.assert_not_called()
        self.assertEqual(result["status"], "running")
        self.assertFalse(result["killed"])
        self.assertFalse(status.exists())

    def test_cli_kill_identity_refusal_is_nonzero_and_bounded(self):
        jid = "d" * 16
        args = SimpleNamespace(resolved_instance=self.instance, job_id=jid, kill=True)
        errors = io.StringIO()
        refusal = {
            "job_id": jid,
            "status": "running",
            "killed": False,
            "error": "job process identity could not be verified",
        }
        with patch.object(jobs, "kill_job", return_value=refusal), \
                redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            jobs.cmd_job({}, args)

        self.assertNotEqual(raised.exception.code, 0)
        self.assertEqual(
            errors.getvalue().strip(),
            "error: job process identity could not be verified",
        )

    def test_cli_kill_only_exact_terminal_noops_are_success(self):
        jid = "d" * 16
        args = SimpleNamespace(resolved_instance=self.instance, job_id=jid, kill=True)
        for status in ("completed", "not_found"):
            with self.subTest(status=status), \
                    patch.object(
                        jobs, "kill_job",
                        return_value={"job_id": jid, "status": status, "killed": False},
                    ), redirect_stdout(io.StringIO()):
                jobs.cmd_job({}, args)

    def test_kill_verifies_docker_container_removal_before_recording_cancelled(self):
        jid = "e" * 16
        log, status, handle = self._paths(jid)
        log.touch()
        handle.write_text("container")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=True), \
                patch.object(jobs, "_docker_container_running", return_value=False), \
                patch.object(jobs, "_remove_docker_job_container"):
            result = jobs.kill_job(self.instance, jid)

        self.assertTrue(result["killed"])
        self.assertEqual(status.read_text(), "143")

    def test_docker_rm_failure_and_unknown_observation_never_records_cancelled(self):
        jid = "6" * 16
        log, status, handle = self._paths(jid)
        log.touch()
        handle.write_text("container")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=True), \
                patch.object(jobs, "_docker_container_running", return_value=None), \
                patch.object(
                    jobs.subprocess, "run",
                    side_effect=jobs.subprocess.TimeoutExpired("docker rm", 5),
                ):
            result = jobs.kill_job(self.instance, jid)

        self.assertEqual(result["status"], "running")
        self.assertFalse(result["killed"])
        self.assertFalse(status.exists())

    def test_unverified_docker_termination_keeps_the_job_running(self):
        jid = "9" * 16
        log, status, _ = self._paths(jid)
        log.touch()
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", side_effect=[True, True]), \
                patch.object(jobs.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            result = jobs.kill_job(self.instance, jid)

        self.assertFalse(result["killed"])
        self.assertEqual(result["status"], "running")
        self.assertFalse(status.exists())

    def test_status_reconciles_a_dead_docker_container_without_terminal_artifact(self):
        jid = "8" * 16
        log, status, _ = self._paths(jid)
        log.touch()
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=False):
            result = jobs.job_status(self.instance, jid)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(status.read_text(), "1")

    def test_prune_removes_old_terminal_groups_but_keeps_running_groups(self):
        terminal = "f" * 16
        running = "a" * 15 + "b"
        terminal_paths = self._paths(terminal)
        running_paths = self._paths(running)
        for path in terminal_paths:
            path.write_text("0" if path.suffix == ".status" else "old")
        running_paths[0].write_text("still running")
        old = time.time() - jobs._JOB_MAX_AGE - 1
        for path in (*terminal_paths, running_paths[0]):
            os.utime(path, (old, old))

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=None):
            removed = jobs.prune_jobs(self.instance)

        self.assertEqual(removed, 1)
        self.assertFalse(any(path.exists() for path in terminal_paths))
        self.assertTrue(running_paths[0].exists())

    def test_ordinary_list_runs_the_terminal_group_retention_sweep(self):
        jid = "1" * 16
        log, status, pid = self._paths(jid)
        log.write_text("done")
        status.write_text("0")
        pid.write_text("100")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "prune_jobs", return_value=0) as prune:
            with redirect_stdout(io.StringIO()):
                jobs.cmd_jobs({}, SimpleNamespace(resolved_instance=self.instance, prune=False))

        prune.assert_called_once_with(self.instance)


if __name__ == "__main__":
    unittest.main()
