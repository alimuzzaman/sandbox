"""`sb test` must never let a queued remote job read as a passing run."""

import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import debug


class RemoteTestReportingTests(unittest.TestCase):
    def test_announce_names_remote_and_local_escape_before_push(self):
        err = StringIO()
        with redirect_stderr(err):
            debug._announce_remote_test("vps", cli_json=False)
        self.assertIn("remote 'vps'", err.getvalue())
        self.assertIn("--local", err.getvalue())

    def test_announce_is_silent_for_json(self):
        err = StringIO()
        with redirect_stderr(err):
            debug._announce_remote_test("vps", cli_json=True)
        self.assertEqual(err.getvalue(), "")

    def test_queued_job_says_it_is_not_a_result_and_keeps_stdout_clean(self):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            debug._report_remote_test("a" * 32, "vps", cli_json=False, wait=False,
                                      transport=None)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("NOT a test result", err.getvalue())
        self.assertIn("job-output " + "a" * 32 + " --remote vps --follow", err.getvalue())

    def test_wait_exits_nonzero_when_remote_job_fails(self):
        transport = SimpleNamespace(status=lambda _r, _j: {"lifecycle": "failed", "exit_code": 1})
        with patch("sandbox.commands.jobs_runtime.cmd_job_output") as follow, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                self.assertRaises(SystemExit) as raised:
            debug._report_remote_test("b" * 32, "vps", cli_json=False, wait=True,
                                      transport=transport)
        self.assertNotEqual(raised.exception.code, 0)
        follow_args = follow.call_args[0][1]
        self.assertTrue(follow_args.follow)
        self.assertEqual(follow_args.remote, "vps")

    def test_wait_returns_normally_when_remote_job_succeeds(self):
        transport = SimpleNamespace(status=lambda _r, _j: {"lifecycle": "succeeded", "exit_code": 0})
        with patch("sandbox.commands.jobs_runtime.cmd_job_output"), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            debug._report_remote_test("c" * 32, "vps", cli_json=False, wait=True,
                                      transport=transport)

    def test_transport_failure_is_rendered_without_traceback(self):
        from sandbox.transports.remote_jobs import RemoteJobTransportError

        def boom():
            raise RemoteJobTransportError("supervisor_launch_failed")
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                self.assertRaises(SystemExit) as raised:
            debug._submit_remote_test(boom, "vps", cli_json=False)
        self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
