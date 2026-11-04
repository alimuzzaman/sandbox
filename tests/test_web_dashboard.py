import sys
import time
import threading
import tempfile
import shutil
from pathlib import Path
import unittest
from unittest import mock
from unittest.mock import patch

from sandbox.core._dash import (
    _start_job, _job_snapshot, _web_do_action, _web_claims, _web_jobs, _claim,
)
from sandbox.core._paths import _web_lock, _WEB_STREAM, _web_jobs_lock
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


class InstanceClaims(JobCaptureIsolation):
    def setUp(self):
        super().setUp()
        _web_claims.clear()
        self.addCleanup(_web_claims.clear)
        if _web_lock.locked():
            try:
                _web_lock.release()
            except RuntimeError:
                pass

    def tearDown(self):
        _web_claims.clear()
        super().tearDown()

    def test_second_mutation_on_a_busy_instance_is_refused_before_effects(self):
        import sandbox.core._dash as dashboard

        restore_started = threading.Event()
        release_restore = threading.Event()
        self.addCleanup(release_restore.set)

        def holding_restore(cfg, args):
            restore_started.set()
            release_restore.wait(timeout=10.0)
            return True

        snap_called = []
        with patch.object(dashboard, "load_config", return_value={}), \
                patch("sandbox.commands.data.cmd_restore", side_effect=holding_restore), \
                patch("sandbox.commands.data.cmd_snapshot", side_effect=lambda *a, **kw: snap_called.append(True)):
            res_hold = _web_do_action({
                "action": "restore", "instance": "one", "name": "snap1", "confirm": True,
            })
            self.assertTrue(res_hold.get("ok"))
            restore_job_id = res_hold["job_id"]
            self.assertTrue(restore_started.wait(timeout=10.0), "restore did not start within 10s")

            count_before = len(_web_jobs)
            res = _web_do_action({"action": "snapshot", "instance": "one", "name": "snap2"})
            self.assertFalse(res.get("ok"))
            self.assertEqual(res.get("code"), "instance_busy")
            self.assertIn("one is busy", res.get("output", ""))
            self.assertNotIn("job_id", res)
            self.assertEqual(len(_web_jobs), count_before)
            self.assertEqual(len(snap_called), 0)

            release_restore.set()
            self._wait_for_job(restore_job_id)

    def test_wp_and_term_are_refused_on_a_busy_instance(self):
        import sandbox.core._dash as dashboard

        restore_started = threading.Event()
        release_restore = threading.Event()
        self.addCleanup(release_restore.set)

        def holding_restore(cfg, args):
            restore_started.set()
            release_restore.wait(timeout=10.0)
            return True

        with patch.object(dashboard, "load_config", return_value={}), \
                patch("sandbox.commands.data.cmd_restore", side_effect=holding_restore):
            res_hold = _web_do_action({
                "action": "restore", "instance": "one", "name": "snap1", "confirm": True,
            })
            self.assertTrue(res_hold.get("ok"))
            hold_id = res_hold["job_id"]
            self.assertTrue(restore_started.wait(timeout=10.0), "holder did not start within 10s")

            wp_mock = mock.MagicMock()
            compose_mock = mock.MagicMock()
            with patch("sandbox.commands.wp.cmd_wp", wp_mock), \
                    patch.object(dashboard, "compose", compose_mock):
                cases = [
                    ("wp", {"action": "wp", "instance": "one", "args": "eval '1;'"}),
                    ("term", {"action": "term", "instance": "one", "cmd": "id"}),
                ]
                for action, payload in cases:
                    with self.subTest(action=action):
                        count_before = len(_web_jobs)
                        res = _web_do_action(payload)
                        self.assertFalse(res.get("ok"))
                        self.assertEqual(res.get("code"), "instance_busy")
                        self.assertIn("one is busy", res.get("output", ""))
                        self.assertNotIn("job_id", res)
                        self.assertEqual(len(_web_jobs), count_before)

            wp_mock.assert_not_called()
            compose_mock.assert_not_called()
            release_restore.set()
            self._wait_for_job(hold_id)

    def test_read_actions_still_queue(self):
        import sandbox.core._dash as dashboard

        read_ops = [
            ("logs", "sandbox.core._dash._compose_no_follow_logs"),
            ("status", "sandbox.commands.lifecycle.cmd_status"),
            ("doctor", "sandbox.commands.lifecycle.cmd_doctor"),
            ("introspect", "sandbox.commands.debug.cmd_introspect"),
        ]
        for act, target_patch in read_ops:
            with self.subTest(action=act):
                started = threading.Event()
                release = threading.Event()
                self.addCleanup(release.set)

                def holding_restore(cfg, args):
                    started.set()
                    release.wait(timeout=10.0)
                    return True

                fn_called = threading.Event()

                def stub_read(*a, **kw):
                    fn_called.set()
                    return True

                with patch.object(dashboard, "load_config", return_value={}), \
                        patch("sandbox.commands.data.cmd_restore", side_effect=holding_restore), \
                        patch(target_patch, side_effect=stub_read):
                    res_hold = _web_do_action({
                        "action": "restore", "instance": "one", "name": "snap1", "confirm": True,
                    })
                    self.assertTrue(res_hold.get("ok"))
                    hold_id = res_hold["job_id"]
                    self.assertTrue(started.wait(timeout=10.0), "holder did not start")

                    res_read = _web_do_action({"action": act, "instance": "one"})
                    self.assertTrue(res_read.get("ok"))
                    self.assertIn("job_id", res_read)
                    read_id = res_read["job_id"]

                    # While holder runs, read op is queued and hasn't called stub
                    self.assertFalse(fn_called.is_set())

                    release.set()
                    self._wait_for_job(hold_id)

                    snap = self._wait_for_job(read_id)
                    self.assertTrue(snap.get("done"))
                    self.assertTrue(snap.get("ok"))
                    self.assertTrue(fn_called.is_set())

    def test_another_instance_is_not_refused(self):
        import sandbox.core._dash as dashboard

        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def holding_restore(cfg, args):
            started.set()
            release.wait(timeout=10.0)
            return True

        two_called = threading.Event()

        def stub_snap(cfg, args):
            two_called.set()
            return True

        with patch.object(dashboard, "load_config", return_value={}), \
                patch("sandbox.commands.data.cmd_restore", side_effect=holding_restore), \
                patch("sandbox.commands.data.cmd_snapshot", side_effect=stub_snap):
            res_one = _web_do_action({
                "action": "restore", "instance": "one", "name": "snap1", "confirm": True,
            })
            self.assertTrue(res_one.get("ok"))
            job_one = res_one["job_id"]
            self.assertTrue(started.wait(timeout=10.0), "holder did not start")

            # Mutation on "two" is accepted and waits on lock
            res_two = _web_do_action({"action": "snapshot", "instance": "two", "name": "snap2"})
            self.assertTrue(res_two.get("ok"))
            self.assertIn("job_id", res_two)
            job_two = res_two["job_id"]

            self.assertFalse(two_called.is_set())

            release.set()
            self._wait_for_job(job_one)

            snap_two = self._wait_for_job(job_two)
            self.assertTrue(snap_two.get("done"))
            self.assertTrue(snap_two.get("ok"))
            self.assertTrue(two_called.is_set())

    def test_sweep_conflicts_both_ways(self):
        import sandbox.core._dash as dashboard

        # 1. start-all held refuses stop on one, and refuses stop-all
        sweep_started = threading.Event()
        release_sweep = threading.Event()
        self.addCleanup(release_sweep.set)

        def holding_up(cfg, args):
            sweep_started.set()
            release_sweep.wait(timeout=10.0)
            return True

        stop_called = []
        with patch.object(dashboard, "load_config", return_value={}), \
                patch.object(dashboard, "resolve_instances", return_value={"one": {}}), \
                patch("sandbox.commands.lifecycle.cmd_up", side_effect=holding_up), \
                patch("sandbox.commands.lifecycle.cmd_down", side_effect=lambda *a, **kw: stop_called.append(True)):
            res_sweep = _web_do_action({"action": "start-all"})
            self.assertTrue(res_sweep.get("ok"))
            sweep_id = res_sweep["job_id"]
            self.assertTrue(sweep_started.wait(timeout=10.0), "sweep did not start")

            # Refuses stop on "one"
            res_stop = _web_do_action({"action": "stop", "instance": "one"})
            self.assertFalse(res_stop.get("ok"))
            self.assertEqual(res_stop.get("code"), "instance_busy")
            self.assertIn("one is busy: start-all", res_stop.get("output", ""))
            self.assertEqual(len(stop_called), 0)

            # Refuses stop-all
            res_stop_all = _web_do_action({"action": "stop-all"})
            self.assertFalse(res_stop_all.get("ok"))
            self.assertEqual(res_stop_all.get("code"), "instance_busy")
            self.assertIn("* is busy: start-all", res_stop_all.get("output", ""))

            release_sweep.set()
            self._wait_for_job(sweep_id)

        # 2. With one held, start-all is refused
        _web_claims["one"] = "restore"
        with patch.object(dashboard, "load_config", return_value={}):
            res_sweep_refused = _web_do_action({"action": "start-all"})
            self.assertFalse(res_sweep_refused.get("ok"))
            self.assertEqual(res_sweep_refused.get("code"), "instance_busy")
            self.assertIn("* is busy: restore", res_sweep_refused.get("output", ""))

    def test_inline_action_is_refused_and_releases(self):
        import sandbox.core._dash as dashboard

        # start on a busy instance is refused with its stub not called
        _web_claims["one"] = "held-action"
        up_called = []
        with patch.object(dashboard, "load_config", return_value={}), \
                patch("sandbox.commands.lifecycle.cmd_up", side_effect=lambda *a, **kw: up_called.append(True)):
            res = _web_do_action({"action": "start", "instance": "one"})
            self.assertFalse(res.get("ok"))
            self.assertEqual(res.get("code"), "instance_busy")
            self.assertIn("one is busy: held-action", res.get("output", ""))
            self.assertEqual(len(up_called), 0)

        _web_claims.clear()

        # A start whose _run_cmd_capture raises leaves _web_claims empty
        with patch.object(dashboard, "_run_cmd_capture", side_effect=RuntimeError("capture crash")):
            with self.assertRaises(RuntimeError):
                _web_do_action({"action": "start", "instance": "one"})
        self.assertNotIn("one", _web_claims)
        self.assertEqual(len(_web_claims), 0)

    def test_claim_is_gone_when_the_job_reads_done(self):
        def fn_false():
            return False

        def fn_val_err():
            raise ValueError("boom")

        def fn_sys_exit():
            sys.exit(1)

        def fn_kb_interrupt():
            raise KeyboardInterrupt()

        cases = [
            ("returns_false", fn_false),
            ("raises_value_error", fn_val_err),
            ("raises_system_exit", fn_sys_exit),
            ("raises_keyboard_interrupt", fn_kb_interrupt),
        ]
        for name, fn in cases:
            with self.subTest(case=name):
                with patch.object(threading, "excepthook"):
                    job_id = _start_job(f"job-{name}", fn, instance="one")
                    snap = self._wait_for_job(job_id)
                    self.assertTrue(snap.get("done"))
                    self.assertNotIn("one", _web_claims)

    def test_failed_thread_start_releases_the_claim(self):
        import sandbox.core._dash as dashboard

        # Job path
        for exc in (RuntimeError("spawn fail"), KeyboardInterrupt("interrupt")):
            with self.subTest(job_exc=type(exc).__name__):
                with patch.object(threading.Thread, "start", side_effect=exc):
                    with self.assertRaises(type(exc)):
                        _start_job("job-fail", lambda: True, instance="one")
                self.assertNotIn("one", _web_claims)
                self.assertEqual(len(_web_claims), 0)
                job = list(_web_jobs.values())[-1]
                self.assertTrue(job["done"])
                self.assertFalse(job["ok"])
                self.assertEqual(job["status"], "job-fail ✗")

        # Sweep path
        for exc in (RuntimeError("spawn fail"), KeyboardInterrupt("interrupt")):
            with self.subTest(sweep_exc=type(exc).__name__):
                with patch.object(dashboard, "load_config", return_value={}):
                    with patch.object(threading.Thread, "start", side_effect=exc):
                        with self.assertRaises(type(exc)):
                            _web_do_action({"action": "start-all"})
                self.assertNotIn("*", _web_claims)
                self.assertEqual(len(_web_claims), 0)
                sweep_job = list(_web_jobs.values())[-1]
                self.assertTrue(sweep_job["done"])
                self.assertFalse(sweep_job["ok"])
                self.assertEqual(sweep_job["status"], "start-all ✗")

    def test_sweep_that_cannot_load_config_ends(self):
        import sandbox.core._dash as dashboard

        with patch.object(dashboard, "load_config", side_effect=RuntimeError("config unreadable")):
            res = _web_do_action({"action": "start-all"})
            self.assertTrue(res.get("ok"))
            job_id = res["job_id"]
            snap = self._wait_for_job(job_id)
            self.assertTrue(snap.get("done"))
            self.assertFalse(snap.get("ok"))
            self.assertIn("config unreadable", snap.get("chunk", ""))
            self.assertNotIn("*", _web_claims)
            self.assertEqual(len(_web_claims), 0)

    def test_create_is_exempt(self):
        import sandbox.core._dash as dashboard

        root = Path.home() / ".sandbox-test-web-create-exempt"
        root.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))

        _web_claims["one"] = "held-mutation"
        called = []
        with patch.object(dashboard, "load_config", return_value={}), \
                patch("sandbox.commands.instances_cmd.cmd_ensure",
                      side_effect=lambda cfg, args: called.append((cfg, args))):
            res = _web_do_action({
                "action": "create", "project_dir": str(root), "label": "review",
            })
            self.assertTrue(res.get("ok"))
            self.assertIn("job_id", res)
            job_id = res["job_id"]
            snap = self._wait_for_job(job_id)
            self.assertTrue(snap.get("done"))
            self.assertEqual(len(called), 1)

    def test_no_jobs_lock_is_held_while_waiting_for_the_execution_lock(self):
        import sandbox.core._dash as dashboard

        _web_lock.acquire()
        self.addCleanup(lambda: _web_lock.release() if _web_lock.locked() else None)

        job_done = threading.Event()
        inline_done = threading.Event()

        def run_job():
            jid = _start_job("test-job", lambda: True, instance="one")
            self._wait_for_job(jid)
            job_done.set()

        def run_inline():
            with patch.object(dashboard, "load_config", return_value={}), \
                    patch("sandbox.commands.instances_cmd.cmd_focus", return_value=True):
                _web_do_action({"action": "unfocus", "instance": "two"})
            inline_done.set()

        t1 = threading.Thread(target=run_job, daemon=True)
        t2 = threading.Thread(target=run_inline, daemon=True)
        t1.start()
        t2.start()

        time.sleep(OVERLAP_WINDOW)

        acquired = _web_jobs_lock.acquire(timeout=5.0)
        self.assertTrue(acquired, "_web_jobs_lock was held while waiting for _web_lock")
        if acquired:
            _web_jobs_lock.release()

        _web_lock.release()
        self.assertTrue(job_done.wait(timeout=10.0), "job did not finish")
        self.assertTrue(inline_done.wait(timeout=10.0), "inline action did not finish")


if __name__ == "__main__":
    unittest.main()
