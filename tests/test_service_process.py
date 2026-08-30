import os
from pathlib import Path
import signal
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from sandbox.services.process import BoundedProcessRunner


class TestBoundedProcessRunner(unittest.TestCase):
    @patch("sandbox.services.process.subprocess.Popen")
    def test_none_environment_preserves_native_inheritance_without_materializing_it(self, popen):
        process = MagicMock()
        process.stdout.read.return_value = b""
        process.stderr.read.return_value = b""
        process.wait.return_value = 0
        process.returncode = 0
        popen.return_value = process

        BoundedProcessRunner().run(("fixture",), env=None, timeout=1)

        self.assertIsNone(popen.call_args.kwargs["env"])

    @patch("sandbox.services.process.subprocess.Popen")
    def test_explicit_environment_is_complete_and_never_merged(self, popen):
        process = MagicMock()
        process.stdout.read.return_value = b""
        process.stderr.read.return_value = b""
        process.wait.return_value = 0
        process.returncode = 0
        popen.return_value = process
        sentinel = "explicit-environment-sentinel"
        supplied = {"ONLY_SYNTHETIC": sentinel}

        BoundedProcessRunner().run(("fixture",), env=supplied, timeout=1)

        child_environment = popen.call_args.kwargs["env"]
        self.assertEqual(child_environment, supplied)
        self.assertNotIn("UNRELATED_SYNTHETIC", child_environment)
        self.assertNotIn(sentinel, repr(child_environment))
        self.assertNotIn(sentinel, str(child_environment))
        self.assertNotIn(sentinel, repr(popen.call_args))

    def test_process_result_repr_excludes_captured_streams(self):
        from sandbox.services.process import ProcessResult

        rendered = repr(ProcessResult(("fixture",), 1, "stdout-sentinel", "stderr-sentinel"))
        self.assertNotIn("stdout-sentinel", rendered)
        self.assertNotIn("stderr-sentinel", rendered)

    @staticmethod
    def _linux_process_is_running(pid):
        stat_path = Path(f"/proc/{pid}/stat")
        if not Path("/proc").is_dir():
            return None
        try:
            remainder = stat_path.read_text().rsplit(")", 1)[1].strip()
        except (FileNotFoundError, PermissionError):
            remainder = ""
        if remainder and remainder.split(maxsplit=1)[0] in {"Z", "X"}:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_argument_list_cwd_environment_redaction_and_limit(self):
        secret = "recovery-secret-sentinel"
        runner = BoundedProcessRunner(max_output=20, secret_values=(secret,))
        result = runner.run([
            sys.executable, "-c",
            "import os; print(os.getcwd()); print(os.environ['FIXTURE_SECRET']); print('x'*100)",
        ], cwd="/tmp", env={"FIXTURE_SECRET": secret}, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout)
        self.assertLessEqual(len(result.stdout), 20)

    def test_shell_string_is_rejected(self):
        with self.assertRaises(ValueError):
            BoundedProcessRunner().run("echo unsafe")

    def test_timeout_returns_a_redacted_bounded_result(self):
        secret = "timeout-secret-sentinel"
        runner = BoundedProcessRunner(secret_values=(secret,))
        result = runner.run(
            [sys.executable, "-c", f"import time; print('{secret}'); time.sleep(1)"],
            timeout=0.01,
        )
        self.assertEqual(result.returncode, 124)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn("timed out", result.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_timeout_terminates_descendants_that_inherit_output_pipes(self):
        grandchild = (
            "import os,time; "
            "print(f'grandchild={os.getpid()}', flush=True); "
            "time.sleep(5)"
        )
        child = (
            "import os,subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
            "print(f'child={os.getpid()}', flush=True); "
            "time.sleep(5)"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "time.sleep(5)"
        )
        started = time.monotonic()
        result = BoundedProcessRunner().run(
            [sys.executable, "-c", parent],
            timeout=0.4,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 124)
        self.assertLess(elapsed, 1.5)
        self.assertIn("child=", result.stdout)
        self.assertIn("grandchild=", result.stdout)
        pids = [
            int(line.split("=", 1)[1])
            for line in result.stdout.splitlines()
            if "=" in line
        ]
        if Path("/proc").is_dir():
            deadline = time.monotonic() + 1
            while (any(self._linux_process_is_running(pid) for pid in pids)
                   and time.monotonic() < deadline):
                time.sleep(0.01)
            self.assertFalse(any(self._linux_process_is_running(pid) for pid in pids))

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_reaped_leader_with_escaped_pipe_holder_never_signals_old_group(self):
        # Fork only after the interpreter has started, so the group leader can
        # deterministically exit inside the deadline even when the full suite
        # is competing for CPU. The detached child keeps the inherited pipes
        # open long enough to exercise the post-reap drain timeout.
        parent = """
import os
import time

if os.fork() == 0:
    os.setsid()
    time.sleep(1.5)
    os._exit(0)
os._exit(0)
"""
        started = time.monotonic()
        with patch.object(
            BoundedProcessRunner,
            "_terminate_process_tree",
            side_effect=AssertionError("reaped leader's PID/PGID was signaled"),
        ):
            result = BoundedProcessRunner().run(
                [sys.executable, "-c", parent],
                timeout=0.5,
            )
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 124)
        self.assertLess(elapsed, 1.5)
        self.assertIn("timed out", result.stderr)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    @patch("sandbox.services.process.time.sleep")
    @patch("sandbox.services.process.os.killpg")
    def test_posix_group_signals_precede_the_only_reap(self, killpg, sleep):
        events = []
        killpg.side_effect = lambda pid, sig: events.append(("signal", pid, sig))
        sleep.side_effect = lambda seconds: events.append(("sleep", seconds))

        class GroupLeader:
            pid = 123

            def wait(self, *, timeout):
                events.append(("wait", timeout))

        BoundedProcessRunner._terminate_process_tree(GroupLeader())

        self.assertEqual(events, [
            ("signal", 123, signal.SIGTERM),
            ("sleep", BoundedProcessRunner._TERMINATION_GRACE),
            ("signal", 123, signal.SIGKILL),
            ("wait", BoundedProcessRunner._TERMINATION_GRACE),
        ])

    @patch("sandbox.services.process.time.sleep")
    @patch("sandbox.services.process.os.name", "nt")
    def test_non_posix_timeout_fallback_is_immediate_process_only(self, sleep):
        class ImmediateProcess:
            pid = 123

            def __init__(self):
                self.calls = []

            def terminate(self):
                self.calls.append("terminate")

            def kill(self):
                self.calls.append("kill")

            def wait(self, *, timeout):
                self.calls.append(("wait", timeout))

        process = ImmediateProcess()
        BoundedProcessRunner._terminate_process_tree(process)

        self.assertEqual(
            process.calls,
            ["terminate", "kill", ("wait", BoundedProcessRunner._TERMINATION_GRACE)],
        )
        sleep.assert_called_once_with(BoundedProcessRunner._TERMINATION_GRACE)

    def test_large_output_is_drained_without_exceeding_the_bound(self):
        runner = BoundedProcessRunner(max_output=128)
        result = runner.run([sys.executable, "-c", "import sys; sys.stdout.write('x' * 10_000_000)"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), 128)
        self.assertEqual(result.stderr, "")

    def test_unicode_edge_capture_stays_utf8_bounded_after_redaction(self):
        secret = "unicode-process-secret-sentinel"
        runner = BoundedProcessRunner(max_output=128, secret_values=(secret,))
        script = (
            "import sys; "
            f"sys.stdout.write('stdout-head-' + '😀' * 200 + '-stdout-tail-{secret}'); "
            f"sys.stderr.write('stderr-head-' + '🧪' * 200 + '-stderr-tail-{secret}')"
        )
        result = runner.run([sys.executable, "-c", script])
        self.assertEqual(result.returncode, 0)
        for value, head, tail in (
                (result.stdout, "stdout-head-", "-stdout-tail-[REDACTED]"),
                (result.stderr, "stderr-head-", "-stderr-tail-[REDACTED]")):
            self.assertLessEqual(len(value.encode("utf-8")), 128)
            self.assertTrue(value.startswith(head))
            self.assertTrue(value.endswith(tail))
            self.assertNotIn(secret, value)

    def test_redaction_expansion_stays_within_utf8_bound(self):
        runner = BoundedProcessRunner(max_output=128, secret_values=("x",))
        rendered = runner._redact("x" + "😀" * 200 + "-tail")
        self.assertLessEqual(len(rendered.encode("utf-8")), 128)
        self.assertTrue(rendered.startswith("[REDACTED]"))
        self.assertTrue(rendered.endswith("-tail"))
        self.assertNotIn("x", rendered)

    def test_constructor_rejects_invalid_bounds_and_secret_types(self):
        with self.assertRaises(ValueError):
            BoundedProcessRunner(max_output=-1)
        with self.assertRaises(ValueError):
            BoundedProcessRunner(max_output=True)
        with self.assertRaises(ValueError):
            BoundedProcessRunner(secret_values=(123,))

    def test_run_rejects_unsafe_arguments_environment_and_timeout(self):
        runner = BoundedProcessRunner()
        invalid_calls = (
            (("echo", "bad\x00arg"), {}, None),
            (("echo",), {"BAD\x00KEY": "value"}, None),
            (("echo",), {"BAD": "bad\x00value"}, None),
            (("echo",), {}, float("nan")),
            (("echo",), {}, -1),
            (("echo",), {}, True),
        )
        for argv, env, timeout in invalid_calls:
            with self.subTest(argv=argv, env=env, timeout=timeout), self.assertRaises(ValueError):
                runner.run(argv, env=env, timeout=timeout)


if __name__ == "__main__":
    unittest.main()
