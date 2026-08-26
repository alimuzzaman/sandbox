"""Behavioral tests for spec 004's per-instance WP-CLI jobs.

These tests intentionally use temporary artifact directories and mocked process
handles: Docker/Herd live proof remains the explicitly blocked T018 work.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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

    def test_docker_launch_reuses_running_web_container_when_builtin_cli_exists(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_wp_has_builtin_cli", return_value=True), \
                patch.object(jobs.secrets, "token_hex", return_value="a" * 16), \
                patch.object(jobs, "compose") as compose:
            jid = jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        self.assertEqual(jid, "a" * 16)
        compose.assert_called_once()
        args = compose.call_args.args
        self.assertEqual(args[:7], ("exec", "-d", "-u", "www-data", "-T", "wp", "sh"))
        self.assertEqual(args[7], "-c")
        wrapper = args[8]
        self.assertIn("job_root=/var/www/html", wrapper)
        self.assertIn("/var/www/vhosts/localhost/html/.sb-jobs", wrapper)
        self.assertIn("wp option get siteurl", wrapper)
        self.assertTrue(self._paths(jid)[0].exists())
        receipt = json.loads((self.job_dir / f"job_{jid}.receipt").read_text())
        self.assertEqual(receipt["job_id"], jid)
        self.assertEqual(receipt["launcher"], "web-exec")
        self.assertGreaterEqual(receipt["acceptance_ms"], 0)
        self.assertGreater(compose.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(compose.call_args.kwargs["timeout"], jobs._JOB_ACCEPTANCE_TIMEOUT)

    def test_docker_launch_keeps_run_fallback_without_builtin_cli(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_wp_has_builtin_cli", return_value=False), \
                patch.object(jobs.secrets, "token_hex", return_value="b" * 16), \
                patch.object(jobs, "compose") as compose:
            jid = jobs.launch_job(self.instance, ["option", "get", "siteurl"])

        self.assertEqual(jid, "b" * 16)
        compose.assert_called_once()
        args = compose.call_args.args
        self.assertEqual(args[:4], ("run", "-d", "--name", jobs._job_name(self.instance, jid)))
        self.assertEqual(args[4:8], ("--entrypoint", "sh", "wpcli", "-c"))
        self.assertIn("/var/www/vhosts/localhost/html/.sb-jobs", args[8])
        self.assertTrue(self._paths(jid)[0].exists())
        self.assertEqual(json.loads((self.job_dir / f"job_{jid}.receipt").read_text())["launcher"], "run")

    def test_request_id_replay_returns_existing_job_without_second_launch(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_wp_has_builtin_cli", return_value=True), \
                patch.object(jobs.secrets, "token_hex", side_effect=["a" * 16, "b" * 8, "c" * 8, "d" * 8]), \
                patch.object(jobs, "compose") as compose:
            first = jobs.launch_job(self.instance, ["option", "get", "siteurl"],
                                    request_id="wp-request-1")
            replay = jobs.launch_job(self.instance, ["option", "get", "siteurl"],
                                     request_id="wp-request-1")

        self.assertEqual(first, "a" * 16)
        self.assertEqual(replay, first)
        compose.assert_called_once()
        request_digest = hashlib.sha256(b"wp-request-1").hexdigest()
        record = json.loads(
            (self.root / ".sb-jobs" / f"request_{request_digest}.json").read_text()
        )
        self.assertEqual(record["job_id"], first)
        self.assertNotIn("wp-request-1", json.dumps(record))
        self.assertNotIn("option", json.dumps(record))

    def test_request_id_conflict_refuses_different_command_without_launch(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_wp_has_builtin_cli", return_value=True), \
                patch.object(jobs.secrets, "token_hex", side_effect=["d" * 16, "e" * 8, "f" * 8, "g" * 8]), \
                patch.object(jobs, "compose") as compose:
            jobs.launch_job(self.instance, ["option", "get", "siteurl"],
                            request_id="wp-request-2")
            with self.assertRaises(jobs.RequestIdConflict):
                jobs.launch_job(self.instance, ["option", "get", "home"],
                                request_id="wp-request-2")

        compose.assert_called_once()

    def test_invalid_request_id_is_rejected_before_job_state_access(self):
        with patch.object(jobs, "_job_dir") as job_dir:
            with self.assertRaisesRegex(ValueError, "request id is invalid"):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"],
                                request_id="../unsafe")
        job_dir.assert_not_called()

    def test_unknown_request_acceptance_replays_reserved_job_without_relaunch(self):
        timeout = subprocess.TimeoutExpired(["docker", "compose"], 15)
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs.secrets, "token_hex", side_effect=["1" * 16, "2" * 8, "3" * 8]), \
                patch.object(jobs, "_launch_job", side_effect=timeout) as launch:
            with self.assertRaises(subprocess.TimeoutExpired):
                jobs.launch_job(self.instance, ["option", "get", "siteurl"],
                                request_id="wp-request-unknown")
            replay = jobs.launch_job(self.instance, ["option", "get", "siteurl"],
                                     request_id="wp-request-unknown")

        self.assertEqual(replay, "1" * 16)
        launch.assert_called_once()
        request_digest = hashlib.sha256(
            b"wp-request-unknown"
        ).hexdigest()
        request_path = self.root / ".sb-jobs" / f"request_{request_digest}.json"
        record = json.loads(request_path.read_text())
        self.assertEqual(record["status"], "unknown")

    def test_docker_db_job_keeps_mysql_client_fallback(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_wp_has_builtin_cli", side_effect=AssertionError("db must not probe web cli")), \
                patch.object(jobs.secrets, "token_hex", return_value="e" * 16), \
                patch.object(jobs, "compose") as compose:
            jid = jobs.launch_job(self.instance, ["db", "query", "SELECT 1"])

        self.assertEqual(jid, "e" * 16)
        self.assertEqual(compose.call_args.args[:4],
                         ("run", "-d", "--name", jobs._job_name(self.instance, jid)))

    def test_docker_wrapper_is_valid_shell_and_quotes_argv(self):
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_wp_has_builtin_cli", return_value=True), \
                patch.object(jobs.secrets, "token_hex", return_value="f" * 16), \
                patch.object(jobs, "compose") as compose:
            jobs.launch_job(self.instance, ["eval", "echo 'quoted; value'"])

        wrapper = compose.call_args.args[8]
        checked = subprocess.run(["sh", "-n", "-c", wrapper],
                                 capture_output=True, text=True, check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_status_probes_shared_container_pid_for_exec_launcher(self):
        jid = "c" * 16
        log, status, pid = self._paths(jid)
        log.touch()
        pid.write_text("4242")
        self.job_dir.mkdir(parents=True, exist_ok=True)
        (self.job_dir / f"job_{jid}.launcher").write_text("web-exec\n")
        result = SimpleNamespace(returncode=0, stdout="", stderr="")
        (self.job_dir / f"job_{jid}.receipt").write_text(json.dumps({
            "job_id": jid, "status": "accepted", "launcher": "web-exec",
            "acceptance_ms": 12.5,
        }))
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "compose", return_value=result) as compose, \
                patch.object(jobs, "_docker_job_running", side_effect=AssertionError("used container lookup")):
            state = jobs.job_status(self.instance, jid)

        self.assertEqual(state["status"], "running")
        self.assertEqual(state["acceptance_ms"], 12.5)
        self.assertEqual(state["launcher"], "web-exec")
        self.assertEqual(compose.call_args.args[:6],
                         ("exec", "-T", "wp", "sh", "-c", "kill -0 4242"))

    def test_shared_container_transport_failure_stays_unknown(self):
        jid = "e" * 16
        _, _, pid = self._paths(jid)
        pid.write_text("4242")
        result = SimpleNamespace(returncode=1, stdout="",
                                  stderr="Cannot connect to the Docker daemon")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "compose", return_value=result):
            self.assertIsNone(jobs._docker_exec_job_running(self.instance, jid, pid))

    def test_malformed_acceptance_receipt_is_ignored(self):
        jid = "a" * 16
        self._paths(jid)[0].touch()
        (self.job_dir / f"job_{jid}.receipt").write_text(json.dumps({
            "job_id": jid, "status": "accepted", "launcher": "web-exec",
            "acceptance_ms": 10 ** 1000,
        }))
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=None):
            self.assertIsNone(jobs._read_acceptance_receipt(self.instance, jid))

    def test_kill_shared_container_job_signals_wrapper_and_verifies_exit(self):
        jid = "d" * 16
        log, status, pid = self._paths(jid)
        log.touch()
        pid.write_text("4242")
        (self.job_dir / f"job_{jid}.launcher").write_text("web-exec\n")
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_kill_docker_exec_job", return_value=True) as kill, \
                patch.object(jobs, "_docker_exec_job_running", side_effect=[True, False, False]), \
                patch.object(jobs.time, "sleep"):
            result = jobs.kill_job(self.instance, jid)

        kill.assert_called_once_with(self.instance, jid, pid)
        self.assertTrue(result["killed"])
        self.assertEqual(status.read_text(), "143")

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
                patch.object(jobs, "_herd_group_running", side_effect=[True, False, False]), \
                patch.object(jobs.os, "killpg") as killpg:
            result = jobs.kill_job(self.instance, jid)

        killpg.assert_called_once_with(4242, jobs.signal.SIGTERM)
        self.assertTrue(result["killed"])
        self.assertEqual(status.read_text(), "143")

    def test_kill_verifies_docker_container_removal_before_recording_cancelled(self):
        jid = "e" * 16
        log, status, _ = self._paths(jid)
        log.touch()
        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", side_effect=[True, False]), \
                patch.object(jobs.subprocess, "run", return_value=SimpleNamespace(returncode=0)):
            result = jobs.kill_job(self.instance, jid)

        self.assertTrue(result["killed"])
        self.assertEqual(status.read_text(), "143")

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
        terminal_launcher = self.job_dir / f"job_{terminal}.launcher"
        terminal_launcher.write_text("web-exec\n")
        terminal_receipt = self.job_dir / f"job_{terminal}.receipt"
        terminal_receipt.write_text(json.dumps({
            "job_id": terminal, "status": "accepted", "launcher": "web-exec",
            "acceptance_ms": 4.0,
        }))
        for path in terminal_paths:
            path.write_text("0" if path.suffix == ".status" else "old")
        running_paths[0].write_text("still running")
        old = time.time() - jobs._JOB_MAX_AGE - 1
        for path in (*terminal_paths, terminal_launcher, terminal_receipt, running_paths[0]):
            os.utime(path, (old, old))

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=None):
            removed = jobs.prune_jobs(self.instance)

        self.assertEqual(removed, 1)
        self.assertFalse(any(path.exists() for path in terminal_paths))
        self.assertFalse(terminal_launcher.exists())
        self.assertFalse(terminal_receipt.exists())
        self.assertTrue(running_paths[0].exists())

    def test_prune_removes_request_record_with_old_terminal_group(self):
        jid = "2" * 16
        log, status, pid = self._paths(jid)
        log.write_text("done")
        status.write_text("0")
        pid.write_text("100")
        request = self.job_dir / "request_fixture.json"
        request.write_text(json.dumps({
            "version": 1, "job_id": jid, "command_digest": "a" * 64,
            "status": "accepted",
        }))
        old = time.time() - jobs._JOB_MAX_AGE - 1
        for path in (log, status, pid, request):
            os.utime(path, (old, old))

        with patch.object(jobs, "wp_dir", return_value=self.root), \
                patch.object(jobs, "_is_herd_instance", return_value=False), \
                patch.object(jobs, "_docker_job_running", return_value=False):
            self.assertEqual(jobs.prune_jobs(self.instance), 1)

        self.assertFalse(request.exists())

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
