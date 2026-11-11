"""Bounded byte capture definitions; process creation is always synthetic."""
import io
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from sandbox.hosting import host_exec
from sandbox.hosting.target_resolution import ResolvedTarget
from tests.subprocess_support import synthetic_environment


class CaptureCompletionTests(unittest.TestCase):
    def request(self):
        target = ResolvedTarget("box", "project", "dev", "stack", "/synthetic/source",
                                "named", "a" * 32)
        return host_exec.ExecRequest(target, "web", ("synthetic",), capture=True)

    def test_complete_capture_keeps_arbitrary_bytes_and_redactor_looking_canary(self):
        content = b"\x00\xff\xfe\n" + b"AIza" + b"A" * 40
        process = Mock(stdout=io.BytesIO(content), stderr=io.BytesIO(b"\x80\x00"))
        process.wait.return_value = 0
        with patch.object(host_exec.subprocess, "Popen", return_value=process):
            outcome = host_exec.run(["synthetic"], self.request(), stdin=-3,
                                    env=synthetic_environment())
        self.assertEqual(outcome.stdout, content)
        self.assertEqual(outcome.stderr, b"\x80\x00")
        self.assertEqual(outcome.code, host_exec.OK)

    def test_eof_after_bounded_truncation_is_complete_but_not_full_output(self):
        buffer = host_exec._BoundedBuffer(3)
        buffer.drain(io.BytesIO(b"abcdef"))
        self.assertEqual(bytes(buffer.data), b"abc")
        self.assertTrue(buffer.truncated)
        self.assertTrue(buffer.complete)

    def test_reader_failure_cannot_be_reported_as_complete_capture(self):
        pipe = Mock()
        pipe.read.side_effect = OSError("synthetic capture read failure")
        buffer = host_exec._BoundedBuffer(3)
        buffer.drain(pipe)
        self.assertFalse(buffer.complete)
        pipe.close.assert_called_once()

    def test_zero_child_exit_without_reader_eof_is_unknown(self):
        process = Mock(stdout=io.BytesIO(b"not read"), stderr=io.BytesIO(b""))
        process.wait.return_value = 0
        thread = Mock()
        thread.is_alive.return_value = False
        with patch.object(host_exec.subprocess, "Popen", return_value=process), \
                patch.object(host_exec.threading, "Thread", return_value=thread):
            outcome = host_exec.run(["synthetic"], self.request(), stdin=-3,
                                    env=synthetic_environment())
        self.assertEqual(outcome.code, host_exec.WATCHDOG_EXPIRED)
        self.assertEqual(outcome.exit_code, host_exec.EXIT_TRANSPORT)
        self.assertFalse(outcome.ok)

    def test_known_local_command_exit_255_is_not_an_ssh_transport_failure(self):
        process = Mock(stdout=io.BytesIO(b"synthetic output"), stderr=io.BytesIO(b"known failure"))
        process.wait.return_value = 255
        # The process/held authority is tested in the command assembly tests;
        # this synthetic outcome fixture grants no effects and starts no child.
        request = replace(self.request(), owner_selection=object())
        with patch.object(host_exec.subprocess, "Popen", return_value=process):
            outcome = host_exec.run(["synthetic"], request, stdin=-3, env=synthetic_environment())
        self.assertEqual(outcome.exit_code, 255)
        self.assertEqual(outcome.code, host_exec.FAILED)
        self.assertEqual(outcome.stdout, b"synthetic output")


class CaptureAssemblyTests(unittest.TestCase):
    def setUp(self):
        import argparse
        from sandbox import cli
        from sandbox.application import command_selection
        token = command_selection.bind_cli_command_parser(argparse.ArgumentParser(), lambda values: values)
        self.addCleanup(command_selection.reset_cli_command_parser, token)
        class ParserCaptured(Exception):
            pass
        def capture(parser, *args, **kwargs):
            raise ParserCaptured()
        # Capture the actual installed parser before configuration or dispatch.
        with patch.object(argparse.ArgumentParser, 'parse_args', capture), \
                patch.object(cli.sys, 'argv', ['sb']), self.assertRaises(ParserCaptured):
            cli.main()

    def test_committed_parser_custody_and_actual_handler_contract_assemble(self):
        from sandbox.application.server_cli_exec import capture_contract_available
        self.assertTrue(capture_contract_available())

    def test_absent_handler_contract_never_advertises_capture(self):
        from sandbox.application.server_cli_exec import capture_contract_available
        from sandbox.commands import host_exec as command
        with patch.object(command, "SERVER_CLI_EXEC_CAPTURE_VERSION", None):
            self.assertFalse(capture_contract_available())
