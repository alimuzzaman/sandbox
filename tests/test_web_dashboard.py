import sys
import time
import threading
import tempfile
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

from sandbox.core._dash import _start_job, _job_snapshot, _web_do_action
from sandbox.core._paths import _web_lock, _WEB_STREAM
import sandbox.core._bridge as bridge


class TestWebDashboardRemoteSummaries(unittest.TestCase):
    def test_remote_summary_rows_are_lightweight_and_secret_free(self):
        from sandbox.commands.ui_dash import _remote_summary_rows

        with patch(
            "sandbox.core._remote.list_remotes",
            return_value={
                "scaleway-sandbox": {
                    "provisioned": True,
                    "control_url": "https://control.example.test",
                    "bearer_token": "token-value-must-not-escape",
                    "ssh": "ssh://operator@example.test",
                },
                "offline": {
                    "provisioned": False,
                    "control_url": "",
                    "bearer_token": "",
                },
            },
        ):
            rows = _remote_summary_rows()

        self.assertEqual(
            rows,
            [
                {"name": "offline", "provisioned": False, "control_ready": False},
                {"name": "scaleway-sandbox", "provisioned": True, "control_ready": True},
            ],
        )
        self.assertNotIn("bearer_token", str(rows))
        self.assertNotIn("ssh", str(rows))


# How long a job with no lock gets to reach its body or install its stream. Only the bug's
# detection depends on this: correct code passes however slow the machine is.
OVERLAP_WINDOW = 0.5


class JobCaptureIsolation(unittest.TestCase):
    def tearDown(self):
        if _web_lock.locked():
            try:
                _web_lock.release()
            except RuntimeError:
                pass

    def _wait_for_job(self, job_id: str, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            snap = _job_snapshot(job_id)
            if snap and snap.get("done"):
                return snap
            time.sleep(0.01)
        self.fail(f"job {job_id} did not finish within {timeout}s deadline")

    def _wait_for_output(self, job_id: str, substring: str, timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            snap = _job_snapshot(job_id)
            if snap and substring in snap.get("chunk", ""):
                return snap
            time.sleep(0.01)
        self.fail(f"job {job_id} output did not contain {substring!r} within {timeout}s deadline")

    def _overlap(self):
        """Start job B while job A is mid-output, then let A finish. Returns both snapshots."""
        orig_stdout, orig_stderr = sys.stdout, sys.stderr

        def restore():
            # Code with the bug leaves the streams wrong: keep that out of later tests.
            sys.stdout, sys.stderr = orig_stdout, orig_stderr
            _WEB_STREAM[0] = False
        self.addCleanup(restore)

        a_started = threading.Event()
        release_a = threading.Event()
        a_printed = threading.Event()
        b_entered = threading.Event()
        self.addCleanup(release_a.set)
        self.b_stream_active = []

        def fn_a():
            print("A1")
            a_started.set()
            if not release_a.wait(timeout=10.0):
                return False
            print("A2")
            a_printed.set()
            return True

        def fn_b():
            b_entered.set()
            # With the lock, A is already done. Without it, this holds B's redirect open
            # while A prints.
            a_printed.wait(timeout=10.0)
            self.b_stream_active.append(_WEB_STREAM[0])
            print("B1")
            return True

        job_a_id = _start_job("Job A", fn_a)
        self.assertTrue(a_started.wait(timeout=10.0), "Job A did not start within 10s")
        job_b_id = _start_job("Job B", fn_b)
        b_entered.wait(timeout=OVERLAP_WINDOW)
        release_a.set()
        return self._wait_for_job(job_a_id), self._wait_for_job(job_b_id)

    def test_two_jobs_keep_their_own_output(self):
        snap_a, snap_b = self._overlap()
        out_a = snap_a.get("chunk", "")
        out_b = snap_b.get("chunk", "")

        self.assertIn("A1", out_a)
        self.assertIn("A2", out_a)
        self.assertNotIn("B1", out_a)

        self.assertIn("B1", out_b)
        self.assertNotIn("A1", out_b)
        self.assertNotIn("A2", out_b)

        self.assertEqual(self.b_stream_active, [True])
        self.assertTrue(snap_a.get("ok"))
        self.assertTrue(snap_b.get("ok"))

    def test_streams_are_restored_after_overlapping_jobs(self):
        orig_stdout, orig_stderr = sys.stdout, sys.stderr
        self._overlap()
        self.assertIs(sys.stdout, orig_stdout)
        self.assertIs(sys.stderr, orig_stderr)
        self.assertFalse(_WEB_STREAM[0])

    def test_two_dashboard_actions_keep_their_own_output(self):
        """The dashboard's own path: a queued action must not take the running one's output."""
        import sandbox.core._dash as dashboard

        orig_stdout, orig_stderr = sys.stdout, sys.stderr

        def restore():
            sys.stdout, sys.stderr = orig_stdout, orig_stderr
            _WEB_STREAM[0] = False
        self.addCleanup(restore)

        one_started = threading.Event()
        release_one = threading.Event()
        self.addCleanup(release_one.set)

        def status(_cfg, args):
            if args.resolved_instance == "one":
                print("A1")
                one_started.set()
                release_one.wait(timeout=10.0)
                print("A2")
            else:
                print("B1")

        with patch.object(dashboard, "load_config", return_value={}), \
                patch("sandbox.commands.lifecycle.cmd_status", side_effect=status):
            job_one = _web_do_action({"action": "status", "instance": "one"})["job_id"]
            self.assertTrue(one_started.wait(timeout=10.0), "first action did not start within 10s")
            job_two = _web_do_action({"action": "status", "instance": "two"})["job_id"]
            # The second action never reaches its body while the first holds the lock, so
            # there is no event to wait on. This window only gives it time to redirect.
            time.sleep(OVERLAP_WINDOW)
            release_one.set()
            out_one = self._wait_for_job(job_one).get("chunk", "")
            out_two = self._wait_for_job(job_two).get("chunk", "")

        self.assertIn("A2", out_one)
        self.assertNotIn("A2", out_two)
        self.assertIn("B1", out_two)
        self.assertNotIn("B1", out_one)

    def test_a_queued_job_says_it_is_waiting(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def fn_a():
            started.set()
            release.wait(timeout=10.0)
            return True

        job_a_id = _start_job("Job A", fn_a)
        self.assertTrue(started.wait(timeout=10.0), "Job A did not start within 10s")
        job_b_id = _start_job("Job B", lambda: True)
        self._wait_for_output(job_b_id, "waiting for another dashboard action to finish")
        release.set()
        self._wait_for_job(job_a_id)
        self.assertTrue(self._wait_for_job(job_b_id).get("ok"))

    def test_an_interrupted_job_still_ends(self):
        def fn_interrupted():
            raise KeyboardInterrupt()

        with patch.object(threading, "excepthook"):  # the thread re-raises it by design
            job_id = _start_job("interrupted", fn_interrupted)
            snap = self._wait_for_job(job_id)
        self.assertFalse(snap.get("ok"))
        self.assertTrue(_web_lock.acquire(blocking=False), "lock was not released")
        _web_lock.release()

    def test_failed_job_releases_the_lock(self):
        def fn_value_error():
            raise ValueError("test failure")

        def fn_system_exit():
            sys.exit(1)

        def fn_returns_false():
            return False

        cases = [
            ("value_error", fn_value_error),
            ("system_exit", fn_system_exit),
            ("returns_false", fn_returns_false),
        ]

        for name, fn in cases:
            with self.subTest(case=name):
                job_id = _start_job(f"fail_{name}", fn)
                snap = self._wait_for_job(job_id)
                self.assertFalse(snap.get("ok"))

                acquired = _web_lock.acquire(blocking=False)
                self.assertTrue(acquired, f"lock was not released after {name}")
                if acquired:
                    _web_lock.release()

                following_id = _start_job("following", lambda: True)
                f_snap = self._wait_for_job(following_id)
                self.assertTrue(f_snap.get("done"))
                self.assertTrue(f_snap.get("ok"))

    def test_bridge_job_waits_for_a_dashboard_job(self):
        tok = "secrettoken123"
        tmp_dir = Path(tempfile.mkdtemp(prefix="sb-bridge-test-"))
        stub_calls = []

        def stub_cmd_snapshot(cfg, args):
            stub_calls.append((cfg, args))
            return True

        a_started = threading.Event()
        release_a = threading.Event()

        def fn_a():
            print("A1")
            a_started.set()
            if not release_a.wait(timeout=10.0):
                return False
            print("A2")
            return True

        try:
            with patch.object(bridge, "_bridge_token_for", lambda inst: tok), \
                 patch.object(bridge, "_is_herd_instance", lambda inst: False), \
                 patch.object(bridge, "snapshots_dir", lambda inst: tmp_dir), \
                 patch.object(bridge, "load_config", lambda: {}), \
                 patch("sandbox.commands.data.cmd_snapshot", side_effect=stub_cmd_snapshot):

                job_a_id = _start_job("Job A", fn_a)
                self.assertTrue(a_started.wait(timeout=10.0), "Job A did not start within 10s")

                code, data = bridge._bridge_handle(
                    "POST", "demo", "/snapshot", {"name": "test-snap"}, f"Bearer {tok}"
                )
                self.assertEqual(code, 202)
                self.assertTrue(data.get("ok"))
                bridge_job_id = data["job_id"]

                # Unserialised, the snapshot runs at once. This window only gives it time to.
                deadline = time.time() + OVERLAP_WINDOW
                while time.time() < deadline and not stub_calls:
                    time.sleep(0.01)
                self.assertEqual(len(stub_calls), 0, "cmd_snapshot was called while Job A was holding lock")

                release_a.set()

                self._wait_for_job(job_a_id)
                snap_bridge = self._wait_for_job(bridge_job_id)

                self.assertTrue(snap_bridge.get("done"))
                self.assertEqual(len(stub_calls), 1, "cmd_snapshot was not called after Job A finished")
        finally:
            release_a.set()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_no_action_callback_takes_the_lock_itself(self):
        import sandbox.core._dash as dashboard

        cases = [
            ("server",
             {"action": "server", "instance": "demo", "server": "nginx"},
             "sandbox.commands.net.cmd_server"),
            ("delete",
             {"action": "delete", "instance": "demo", "confirm": "demo"},
             "sandbox.commands.instances_cmd.cmd_instance"),
            ("status",
             {"action": "status", "instance": "demo"},
             "sandbox.commands.lifecycle.cmd_status"),
        ]

        for action_name, payload, patch_target in cases:
            with self.subTest(action=action_name):
                called = []
                with patch.object(dashboard, "load_config", return_value={}), \
                     patch(patch_target, side_effect=lambda *a, **kw: called.append(True)):
                    res = _web_do_action(payload)
                    self.assertTrue(res.get("ok"), f"action {action_name} failed: {res}")
                    job_id = res["job_id"]
                    snap = self._wait_for_job(job_id)
                    self.assertTrue(snap.get("done"))
                    self.assertEqual(len(called), 1)


if __name__ == "__main__":
    unittest.main()
