"""Normal CLI composition; SDK ports are synthetic and no job is submitted."""
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.application import server_cli as sdk
from sandbox.application import server_cli_exec as execute
from sandbox.hosting import server_cli_dispatch as dispatch
from sandbox.hosting import server_cli_exec_client as client


class ExecClientTests(unittest.TestCase):
    def setUp(self):
        self.args = Namespace(action="exec", remote="box", name="named", target=None,
            environment=None, project_dir="/not/a/local/checkout", service="web",
            exec_argv=["printf", "--", "hello"], timeout=30, json=True,
            request_id="original-exec", _server_cli_argv=("host", "exec", "--remote", "box",
                "--name", "named", "--service", "web", "--", "printf", "--", "hello"))
        self.context = self.enterContext(patch.object(sdk, "server_cli_context_if_selected", return_value=None))
        self.lookup = self.enterContext(patch("sandbox.core._remote.get_remote", return_value={
            "joined": True, "control_transport": "tailscale"}))
        self.observe = self.enterContext(patch(
            "sandbox.application.server_cli_targets.observe_server_cli_target", return_value={"synthetic": True}))
        self.receipt = {"job_id": "a" * 32, "request_id": "original-exec",
            "execution_policy": {"provenance": {"server_cli.absolute_deadline_at":
                (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()}}}
        self.submit = self.enterContext(patch.object(execute, "submit_server_cli_exec", return_value=self.receipt))
        self.outcome = {"ok": True, "code": "ok", "exit_code": 0,
            "remote": "box", "target": "project/dev", "service": "web", "duration_ms": 1,
            "stdout_bytes": b"hello\n", "stderr_bytes": b"", "output_encoding": "artifact-bytes",
            "stdout_truncated": False, "stderr_truncated": False}
        self.read = self.enterContext(patch.object(execute, "read_server_cli_exec_result",
            return_value={"status": "succeeded", "result": self.outcome}))
        self.enterContext(patch("sandbox.hosting.host_exec.stdin_source", return_value=-3))
        self.sleep = self.enterContext(patch.object(client.time, "sleep"))
        self.enterContext(patch("sandbox.core._hosting.load_host_state", side_effect=AssertionError("local history")))
        self.enterContext(patch("sandbox.hosting.stack_registry.read", side_effect=AssertionError("local registry")))
        self.legacy_submit = self.enterContext(patch.object(sdk, "dispatch_server_cli",
            side_effect=AssertionError("source staging")))

    def invoke(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertTrue(dispatch.dispatch_host_if_selected(self.args))
        return json.loads(output.getvalue())

    def test_normal_joined_exec_uses_server_selector_original_command_and_receipt(self):
        result = self.invoke()
        self.observe.assert_called_once_with("box", target=None, name="named", environment=None)
        kwargs = self.submit.call_args.kwargs
        self.assertEqual(kwargs["argv"], ["printf", "--", "hello"])
        self.assertEqual(kwargs["request_id"], "original-exec")
        self.assertEqual(kwargs["output_mode"], "artifact")
        self.assertEqual(result["stdout"], "hello\n")
        self.assertEqual(result["job_id"], "a" * 32)
        self.assertEqual(result["request_id"], "original-exec")
        self.read.assert_called_once()
        self.legacy_submit.assert_not_called()

    def test_original_pipe_is_handed_once_to_the_supported_input_port(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"synthetic original input\x00\xff")
            os.close(write_fd)
            write_fd = None
            captured = []
            def submit(*args, **kwargs):
                captured.append(os.read(kwargs["stdin_fd"], 1024))
                return self.receipt
            self.submit.side_effect = submit
            with patch("sandbox.hosting.host_exec.stdin_source", return_value=read_fd):
                self.invoke()
            self.assertEqual(captured, [b"synthetic original input\x00\xff"])
            self.assertEqual(os.read(read_fd, 1), b"")
            self.submit.assert_called_once()
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_tty_or_missing_stdin_is_a_real_empty_descriptor(self):
        inputs = []
        def submit(*args, **kwargs):
            inputs.append(os.read(kwargs["stdin_fd"], 1))
            return self.receipt
        self.submit.side_effect = submit
        self.invoke()
        self.assertEqual(inputs, [b""])

    def test_running_result_is_not_published_and_only_original_receipt_is_observed(self):
        self.read.side_effect = [{"status": "running", "result": None},
            {"status": "succeeded", "result": self.outcome}]
        self.invoke()
        self.assertEqual(self.read.call_count, 2)
        for call in self.read.call_args_list:
            self.assertEqual(call.args, ("box", self.receipt))
            self.assertLessEqual(call.kwargs["budget_seconds"], 15)
        self.submit.assert_called_once()

    def test_plain_capture_and_underlying_nonzero_exit_are_preserved(self):
        self.args.json = False
        self.outcome.update(ok=False, code="host_exec_failed", exit_code=7, stderr_bytes=b"notice\n")
        stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        stderr = io.TextIOWrapper(stderr_bytes, encoding="utf-8")
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as failure:
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(failure.exception.code, 7)
        self.assertEqual(stdout_bytes.getvalue(), b"hello\n")
        self.assertEqual(stderr_bytes.getvalue(), b"notice\n")
        self.submit.assert_called_once()

    def test_verified_artifact_bytes_are_written_exactly_to_plain_output(self):
        self.args.json = False
        content = b"\x00\xff\xfe" + b"AIza" + b"A" * 40
        self.outcome.update(stdout_bytes=content, stderr_bytes=b"\x80\x00", output_encoding="artifact-bytes", exit_code=7,
                            ok=False, code="host_exec_failed")
        stdout_bytes, stderr_bytes = io.BytesIO(), io.BytesIO()
        stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
        stderr = io.TextIOWrapper(stderr_bytes, encoding="utf-8")
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as failure:
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(failure.exception.code, 7)
        self.assertEqual(stdout_bytes.getvalue(), content)
        self.assertEqual(stderr_bytes.getvalue(), b"\x80\x00")
        self.submit.assert_called_once()

    def test_user_json_default_keeps_utf8_projection_of_verified_artifact_bytes(self):
        self.outcome.update(stdout_bytes=b"\x00\xff", stderr_bytes=b"\x80", output_encoding="artifact-bytes")
        result = self.invoke()
        self.assertEqual(result["stdout"], "\x00\ufffd")
        self.assertEqual(result["stderr"], "\ufffd")
        self.assertNotIn("stdout_bytes", result)
        self.assertEqual(result["job_id"], "a" * 32)

    def test_missing_byte_stream_refuses_with_original_handle(self):
        self.outcome.update(stdout_bytes=b"synthetic", output_encoding="artifact-bytes")
        self.outcome.pop("stderr_bytes")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["code"], "server_cli_exec_observation_unavailable")
        self.assertEqual(result["job_id"], "a" * 32)

    def test_byte_fields_without_the_verified_artifact_contract_are_not_rendered(self):
        self.outcome.update(stdout_bytes=b"synthetic", stderr_bytes=b"")
        self.outcome.pop("output_encoding")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(json.loads(output.getvalue())["code"], "server_cli_exec_observation_unavailable")
        self.assertNotIn("synthetic", output.getvalue())

    def test_artifact_request_does_not_downgrade_to_utf8_log_output(self):
        for key in ("stdout_bytes", "stderr_bytes", "output_encoding"):
            self.outcome.pop(key)
        self.outcome.update(stdout="unverified UTF8 fallback", stderr="")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["code"], "server_cli_exec_observation_unavailable")
        self.assertEqual(result["job_id"], "a" * 32)
        self.assertNotIn("unverified UTF8", output.getvalue())

    def test_retained_pre_effect_refusal_requires_no_byte_artifacts(self):
        self.read.return_value = {"status": "failed", "result": {"ok": False,
            "code": "anonymous_stdin_capacity_unavailable", "exit_code": 1,
            "error": "no job effect was dispatched because the server slot was occupied"}}
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as failure:
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(failure.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "anonymous_stdin_capacity_unavailable")
        self.submit.assert_called_once()

    def test_lost_acceptance_preserves_request_and_never_observes_or_replays(self):
        self.submit.side_effect = execute.ServerCLIExecAcceptanceUnknown("original-exec")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["acceptance"], "unknown")
        self.assertEqual(result["request_id"], "original-exec")
        self.assertNotIn("job_id", result)
        self.submit.assert_called_once()
        self.read.assert_not_called()

    def test_observation_failure_preserves_known_job_and_does_not_become_acceptance_unknown(self):
        self.read.side_effect = execute.ServerCLIExecObservationUnavailable(self.receipt)
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        result = json.loads(output.getvalue())
        self.assertEqual(result["acceptance"], "accepted")
        self.assertEqual(result["job_id"], "a" * 32)
        self.assertEqual(result["code"], "server_cli_exec_observation_unavailable")
        self.submit.assert_called_once()

    def test_preacceptance_capability_failure_is_closed_and_never_reads_output(self):
        self.submit.side_effect = sdk.ServerCLIUnavailable("untrusted raw provider response")
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(json.loads(output.getvalue())["acceptance"], "not_accepted")
        self.assertNotIn("untrusted raw", output.getvalue())
        self.read.assert_not_called()

    def test_missing_service_or_ambiguous_target_refuses_before_metadata_or_submission(self):
        for change in ({"service": None}, {"target": "other/dev"}, {"exec_argv": []},
                       {"tailnet_hostname": "unexpected"}, {"image_action": "up"}):
            with self.subTest(change=change), patch.dict(self.args.__dict__, change), \
                    redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as failure:
                dispatch.dispatch_host_if_selected(self.args)
            self.assertEqual(failure.exception.code, 2)
        self.observe.assert_not_called()
        self.submit.assert_not_called()

    def test_receiver_uses_actual_handler_before_controller_setup_and_never_redispatches(self):
        self.context.return_value = SimpleNamespace(origin_remote_name="box", request_id="original-exec", job_id="a" * 32)
        with patch("sandbox.commands.host_exec.cmd_host_exec") as handler:
            self.assertTrue(dispatch.dispatch_host_if_selected(self.args))
        handler.assert_called_once_with(self.args)
        self.lookup.assert_not_called()
        self.observe.assert_not_called()
        self.submit.assert_not_called()

    def test_revoked_selected_receiver_never_falls_back(self):
        self.context.side_effect = sdk.ServerCLIUnavailable("revoked selected job")
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        self.lookup.assert_not_called()
        self.submit.assert_not_called()

    def test_other_alias_nonjoined_and_explicit_personal_keep_legacy_path(self):
        self.context.return_value = SimpleNamespace(origin_remote_name="another-alias")
        self.assertFalse(dispatch.dispatch_host_if_selected(self.args))
        self.context.return_value = None
        self.lookup.return_value = {"provisioned": True}
        self.assertFalse(dispatch.dispatch_host_if_selected(self.args))
        self.args.credential_scope = "personal-local"
        self.assertFalse(dispatch.dispatch_host_if_selected(self.args))
        self.observe.assert_not_called()
        self.submit.assert_not_called()

    def test_interrupt_during_wait_keeps_known_job_without_cancel_or_resubmit(self):
        self.read.side_effect = KeyboardInterrupt()
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as failure:
            dispatch.dispatch_host_if_selected(self.args)
        result = json.loads(output.getvalue())
        self.assertEqual(failure.exception.code, 130)
        self.assertEqual(result["job_id"], "a" * 32)
        self.assertEqual(result["acceptance"], "accepted")
        self.submit.assert_called_once()

    def test_wait_deadline_keeps_original_job_and_does_not_cancel_or_resubmit(self):
        self.receipt["execution_policy"]["provenance"]["server_cli.absolute_deadline_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.read.return_value = {"status": "running", "result": None}
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            dispatch.dispatch_host_if_selected(self.args)
        self.assertEqual(json.loads(output.getvalue())["job_id"], "a" * 32)
        self.submit.assert_called_once()
        self.read.assert_called_once()
        self.sleep.assert_not_called()
