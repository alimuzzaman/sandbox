"""Admission boundary checks only; these do not prove a Linux owner run."""
from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import publisher_proof as command
from sandbox.hosting.publisher_proof import CONTRACT


class PublisherProofAdmissionTest(unittest.TestCase):
    def args(self, extra=(), request="proof"):
        return SimpleNamespace(remote="proof", request_id=request, json=True,
            _server_cli_argv=["publisher-proof", "--remote", "proof",
                              "--request-id", request, "--json", *extra])

    def test_unrelated_or_duplicate_selectors_refuse_before_owner_or_transport(self):
        for extra in (("--instance", "unrelated"), ("--label", "unrelated"),
                      ("--remote", "proof"), ("--json",), ("--json=true",)):
            with self.subTest(extra=extra), patch(
                    "sandbox.application.server_cli.server_cli_context_if_selected") as owner, patch(
                    "sandbox.application.server_cli.dispatch_server_cli") as transport:
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as error:
                    command.dispatch(self.args(extra))
                self.assertEqual(error.exception.code, 1)
                self.assertEqual(json.loads(output.getvalue())["status"], "unavailable")
                owner.assert_not_called()
                transport.assert_not_called()

    def test_invalid_request_is_not_echoed(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            command.dispatch(self.args(request="invalid/request"))
        self.assertIsNone(json.loads(output.getvalue())["request_id"])

    def test_submission_requires_capability_and_fixed_budget(self):
        receipt = {"ok": True, "status": "accepted", "job_id": "a" * 32,
                   "request_id": "proof"}
        with patch("sandbox.application.server_cli.server_cli_context_if_selected", return_value=None), patch(
                "sandbox.application.server_cli.dispatch_server_cli", return_value=receipt) as transport:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertTrue(command.dispatch(self.args()))
            self.assertEqual(json.loads(output.getvalue()), receipt)
            self.assertEqual(transport.call_args.kwargs["required_capabilities"], (CONTRACT,))
            self.assertEqual(transport.call_args.kwargs["timeout_seconds"], 180)
            self.assertEqual(transport.call_args.kwargs["request_id"], "proof")

    def test_unknown_submission_does_not_retry_or_emit_raw_error(self):
        with patch("sandbox.application.server_cli.server_cli_context_if_selected", return_value=None), patch(
                "sandbox.application.server_cli.dispatch_server_cli",
                side_effect=RuntimeError("untrusted diagnostic text")) as transport:
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit):
                command.dispatch(self.args())
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "unconfirmed")
            self.assertNotIn("untrusted", output.getvalue())
            self.assertEqual(transport.call_count, 1)


if __name__ == "__main__":
    unittest.main()
