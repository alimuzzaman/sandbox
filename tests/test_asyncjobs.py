"""Unit tests for the generic host-level async job runner
(sandbox/core/_asyncjobs.py) — used by run_e2e/ci_run's async wrapping since
neither is scoped to a single instance (commands/jobs.py's model only runs one
wp-cli command inside one instance's container). Real subprocesses, no
docker — a trivial `sh -c` command and a real SIGTERM kill of a sleeper.

Run from the repo root:
    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.core as core  # noqa: E402
import sandbox.core._asyncjobs as asyncjobs  # noqa: E402


def _wait_until(predicate, timeout=10, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestAsyncJobs(unittest.TestCase):
    def setUp(self):
        self._tmp_runtime = tempfile.mkdtemp(prefix="sb-asyncjobs-")
        # RUNTIME_DIR is back-filled by VALUE at package-import time into
        # every sandbox.core submodule's OWN namespace — it is NOT a live
        # reference back to sandbox.core's attribute, so patching `core.
        # RUNTIME_DIR` (or sandbox.core._paths.RUNTIME_DIR) does nothing to
        # what `_asyncjobs.py`'s functions actually look up. Patch the name
        # directly on the module whose function bodies resolve it.
        self._orig_runtime_dir = asyncjobs.RUNTIME_DIR
        asyncjobs.RUNTIME_DIR = Path(self._tmp_runtime)

    def tearDown(self):
        asyncjobs.RUNTIME_DIR = self._orig_runtime_dir
        import shutil
        shutil.rmtree(self._tmp_runtime, ignore_errors=True)

    def test_valid_job_id(self):
        self.assertTrue(core.valid_async_job_id("a" * 16))
        self.assertFalse(core.valid_async_job_id("not-hex"))
        self.assertFalse(core.valid_async_job_id(""))
        self.assertFalse(core.valid_async_job_id(None))

    def test_launch_and_poll_to_completion(self):
        jid = core.launch_background_job(["sh", "-c", "echo hello; exit 0"],
                                          cwd=Path.cwd())
        self.assertTrue(core.valid_async_job_id(jid))
        ok = _wait_until(lambda: core.background_job_status(jid)["status"] == "completed")
        self.assertTrue(ok, "job never completed")
        s = core.background_job_status(jid)
        self.assertEqual(s["exit_code"], 0)
        self.assertIn("hello", s["stdout"])

    def test_nonzero_exit_code_captured(self):
        jid = core.launch_background_job(["sh", "-c", "exit 7"], cwd=Path.cwd())
        _wait_until(lambda: core.background_job_status(jid)["status"] == "completed")
        s = core.background_job_status(jid)
        self.assertEqual(s["exit_code"], 7)

    def test_incremental_offset_reads(self):
        jid = core.launch_background_job(
            ["sh", "-c", "echo one; sleep 1; echo two"], cwd=Path.cwd())
        # Read WHILE still running — only "one" should be on disk yet.
        got_first = _wait_until(
            lambda: "one" in core.background_job_status(jid)["stdout"], timeout=5)
        self.assertTrue(got_first, "'one' never appeared before completion")
        first = core.background_job_status(jid)
        self.assertIn("one", first["stdout"])
        self.assertNotIn("two", first["stdout"])
        _wait_until(lambda: core.background_job_status(jid)["status"] == "completed",
                   timeout=5)
        second = core.background_job_status(jid, offset=first["bytes_read"])
        self.assertNotIn("one", second["stdout"])
        self.assertIn("two", second["stdout"])

    def test_not_found_for_unknown_job(self):
        s = core.background_job_status("f" * 16)
        self.assertEqual(s["status"], "not_found")

    def test_kill_running_job(self):
        jid = core.launch_background_job(["sh", "-c", "sleep 30"], cwd=Path.cwd())
        started = _wait_until(
            lambda: (asyncjobs.RUNTIME_DIR / "async-jobs" / jid / "pid").exists())
        self.assertTrue(started, "job never wrote its pid file")
        r = core.kill_background_job(jid)
        self.assertTrue(r["killed"])
        self.assertEqual(r["status"], "completed")
        s = core.background_job_status(jid)
        self.assertEqual(s["status"], "completed")
        self.assertEqual(s["exit_code"], 143)

    def test_kill_already_completed_job_is_a_noop(self):
        jid = core.launch_background_job(["sh", "-c", "exit 0"], cwd=Path.cwd())
        _wait_until(lambda: core.background_job_status(jid)["status"] == "completed")
        r = core.kill_background_job(jid)
        self.assertFalse(r["killed"])
        self.assertEqual(r["status"], "completed")

    def test_kill_unknown_job(self):
        r = core.kill_background_job("e" * 16)
        self.assertEqual(r["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
