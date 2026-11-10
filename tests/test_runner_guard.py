"""A test that reaches os._exit or os.exec* fails instead of ending the run."""

import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

from tests.runner_guard import RunnerExitError
from tests.subprocess_support import run_test_process

ROOT = Path(__file__).resolve().parent.parent


class TestRunnerGuard(unittest.TestCase):
    def test_exit_and_exec_raise_in_the_runner_process(self):
        calls = (
            lambda: os._exit(0),
            lambda: os.execv(sys.executable, [sys.executable, "-c", "pass"]),
            lambda: os.execve(sys.executable, [sys.executable, "-c", "pass"], {}),
            lambda: os.execl(sys.executable, sys.executable, "-c", "pass"),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index), self.assertRaises(RunnerExitError):
                call()

    @unittest.skipUnless(hasattr(os, "fork"), "needs os.fork")
    def test_forked_child_keeps_the_real_exit(self):
        pid = os.fork()
        if pid == 0:
            try:
                os._exit(7)
            finally:
                os._exit.__wrapped__(3)
        _pid, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 7)

    def test_unittest_run_reports_the_exit_and_keeps_running(self):
        fixture = textwrap.dedent("""\
            import os
            import unittest
            import tests  # installs the runner guard, as any tests.* import does

            class Fixture(unittest.TestCase):
                def test_a_exits_in_process(self):
                    os._exit(0)

                def test_b_still_runs(self):
                    pass
            """)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_exit_fixture.py").write_text(fixture)
            result = run_test_process(
                (sys.executable, "-m", "unittest", "discover", "-s", directory, "-v"),
                env={"PYTHONPATH": str(ROOT)}, cwd=directory,
                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("RunnerExitError: os._exit was called", result.stderr)
        self.assertIn("test_b_still_runs", result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)
        self.assertIn("FAILED (failures=1)", result.stderr)


if __name__ == "__main__":
    unittest.main()
