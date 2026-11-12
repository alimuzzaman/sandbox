"""Admission boundary checks only; these do not prove a Linux owner run."""
from contextlib import redirect_stdout
import argparse
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import publisher_proof as command
from sandbox.hosting.publisher_proof import CONTRACT, contract_available


class PublisherProofAdmissionTest(unittest.TestCase):
    def test_capability_requires_the_composed_parser(self):
        from sandbox.application.command_selection import bind_cli_command_parser, reset_cli_command_parser
        from sandbox.registry import COMMAND_SPECS, compose_missing_parsers
        from sandbox.cli import _selection_argv
        parser = argparse.ArgumentParser()
        commands = parser.add_subparsers(dest="cmd")
        commands.add_parser("fixture")
        token = bind_cli_command_parser(parser, _selection_argv)
        try:
            self.assertFalse(contract_available())
            compose_missing_parsers(commands, (COMMAND_SPECS.get("publisher-proof"),))
            self.assertTrue(contract_available())
        finally:
            reset_cli_command_parser(token)

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
