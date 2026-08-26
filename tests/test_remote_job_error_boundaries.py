import argparse
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands.jobs_runtime import (
    cmd_job_cancel,
    cmd_job_list,
    cmd_job_output,
    configure_cancel_parser,
    configure_list_parser,
    configure_output_parser,
)
from sandbox.transports.remote_jobs import RemoteJobTransport, RemoteJobTransportError, _error_detail


class RemoteJobErrorBoundaryTests(unittest.TestCase):
    def test_malformed_control_stdout_is_not_repeated_as_error_detail(self):
        retained = '{"ok":true,"jobs":[{"job_id":"retained-job-output"}]}'
        result = SimpleNamespace(returncode=1, stdout=retained, stderr="")

        detail = _error_detail(None, result)

        self.assertEqual(detail, "remote exit code 1")
        self.assertNotIn("retained-job-output", detail)

    def test_remote_job_list_json_failure_is_a_bounded_envelope(self):
        parser = argparse.ArgumentParser()
        configure_list_parser(parser)
        args = parser.parse_args(["--remote", "scaleway-sandbox", "--limit", "200", "--json"])
        output = StringIO()
        with patch(
                "sandbox.commands.jobs_runtime.durable_job_dependencies",
                return_value={"target_service": None}), \
                patch(
                    "sandbox.transports.remote_jobs.RemoteJobTransport.list",
                    side_effect=RemoteJobTransportError(
                        "remote job control operation failed: remote exit code 1")), \
                redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                cmd_job_list(None, args)

        self.assertEqual(raised.exception.code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["operation"], "job-list")
        self.assertNotIn("retained", output.getvalue())

    def test_remote_job_cancel_json_failure_is_a_bounded_envelope(self):
        parser = argparse.ArgumentParser()
        configure_cancel_parser(parser)
        args = parser.parse_args(["a" * 32, "--remote", "scaleway-sandbox", "--json"])
        output = StringIO()
        with patch(
                "sandbox.transports.remote_jobs.RemoteJobTransport.cancel",
                side_effect=RemoteJobTransportError(
                    "remote job control operation failed: remote exit code 1")), \
                redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                cmd_job_cancel(None, args)

        self.assertEqual(raised.exception.code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["operation"], "job-cancel")
        self.assertNotIn("traceback", output.getvalue().lower())

    def test_remote_job_output_json_failure_is_a_bounded_envelope(self):
        parser = argparse.ArgumentParser()
        configure_output_parser(parser)
        args = parser.parse_args(["a" * 32, "--remote", "scaleway-sandbox", "--json"])
        output = StringIO()
        with patch(
                "sandbox.transports.remote_jobs.RemoteJobTransport.read_output",
                side_effect=RemoteJobTransportError(
                    "remote output read failed: remote exit code 1")), \
                redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                cmd_job_output(None, args)

        self.assertEqual(raised.exception.code, 1)
        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["operation"], "job-output")
        self.assertNotIn("traceback", output.getvalue().lower())

    def test_transport_list_never_leaks_truncated_job_page(self):
        retained = '{"ok":true,"jobs":[{"job_id":"retained-job-output"}]}'[:-4]
        transport = RemoteJobTransport(
            deploy=lambda *_: {},
            ssh_run=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1, stdout=retained, stderr=""),
            remote_lookup=lambda _name: {"provisioned": True},
        )

        with self.assertRaises(RemoteJobTransportError) as raised:
            transport.list("scaleway-sandbox", limit=200)

        self.assertNotIn("retained-job-output", str(raised.exception))
        self.assertIn("remote exit code 1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
