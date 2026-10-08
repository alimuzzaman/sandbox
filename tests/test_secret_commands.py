from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from tests.subprocess_support import synthetic_environment
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from sandbox.commands import secrets as command
from sandbox.secrets import SecretBrokerError


class SecretCommandTests(unittest.TestCase):
    def parser(self):
        parser = argparse.ArgumentParser()
        command.configure_parser(parser)
        return parser

    def test_default_inspection_is_keys_only(self):
        args = self.parser().parse_args(["inspect", "--source", "fixture"])
        self.assertEqual((args.action, args.mode, args.keys, args.project_dir),
                         ("inspect", "keys", None, "."))

    def test_run_supports_repeatable_key_destination_bindings(self):
        args = self.parser().parse_args([
            "run", "--source", "fixture", "--secret", "ACCESS_KEY=AWS_ACCESS_KEY_ID",
            "--secret", "ACCESS_SECRET=AWS_SECRET_ACCESS_KEY", "--", "child",
        ])
        self.assertEqual(args.secrets, [
            "ACCESS_KEY=AWS_ACCESS_KEY_ID", "ACCESS_SECRET=AWS_SECRET_ACCESS_KEY",
        ])
        self.assertIsNone(args.key)

    def test_source_info_defaults_to_bucketed_metadata(self):
        args = self.parser().parse_args(["source-info", "--source", "fixture"])
        self.assertEqual(
            (args.action, args.source, args.exact_size, args.json, args.project_dir),
            ("source-info", "fixture", False, False, "."),
        )

    def test_parser_has_no_plaintext_value_or_reveal_json_flags(self):
        help_text = self.parser().format_help()
        self.assertNotIn("--value", help_text)
        reveal = self.parser().parse_args(["reveal", "--source", "fixture", "--key", "TOKEN"])
        self.assertFalse(hasattr(reveal, "json"))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser().parse_args(["reveal", "--source", "fixture", "--key", "TOKEN", "--json"])

    def test_protected_stdin_removes_only_one_terminal_newline(self):
        fake_stdin = type("Input", (), {"buffer": io.BytesIO(b"fixture-material\r\n")})()
        with patch.object(command.sys, "stdin", fake_stdin):
            self.assertEqual(command._stdin_secret(), "fixture-material")
        for candidate in (b"", b"one\ntwo\n", b"bad\x00input\n"):
            fake_stdin = type("Input", (), {"buffer": io.BytesIO(candidate)})()
            with patch.object(command.sys, "stdin", fake_stdin), self.assertRaises(SecretBrokerError):
                command._stdin_secret()

        fake_stdin = type("Input", (), {"buffer": io.BytesIO(b"\xff\n")})()
        with patch.object(command.sys, "stdin", fake_stdin), \
             self.assertRaises(SecretBrokerError) as raised:
            command._stdin_secret()
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_feature_skill_is_discoverable_and_forbids_pasted_secrets(self):
        body = (Path(__file__).parent.parent / "skills/secret-inspection/SKILL.md").read_text()
        self.assertIn("secrets inspect", body)
        self.assertIn("secrets run", body)
        self.assertIn("outside every\n   agent-captured", body)
        self.assertNotIn("--value", body)

    def test_every_action_bypasses_legacy_runtime_reconciliation(self):
        cli = (Path(__file__).parent.parent / "sandbox/cli.py").read_text()
        self.assertIn('args.cmd != "secrets"', cli)

    def test_reveal_writes_only_to_confirmed_controlling_tty(self):
        class Tty:
            def __init__(self):
                self.output = io.StringIO()
            def fileno(self):
                return 9
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def close(self):
                pass
            def write(self, value):
                return self.output.write(value)
            def flush(self):
                pass
            def readline(self):
                return "API_TOKEN\n"
            def getvalue(self):
                return self.output.getvalue()

        tty = Tty()
        class Service:
            def reveal(self, source, key, consumer, *, confirmed):
                self.call = (source, key, confirmed)
                if confirmed:
                    consumer("SyntheticTtyOnly")
        service = Service()
        args = SimpleNamespace(
            action="reveal", source="fixture", key="API_TOKEN", project_dir=".",
        )
        stdout = io.StringIO()
        with patch.object(command, "_service", return_value=service), \
             patch("builtins.open", return_value=tty), \
             patch.object(command.os, "isatty", return_value=True), \
             redirect_stdout(stdout):
            command.cmd_secrets({}, args)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(service.call, ("fixture", "API_TOKEN", True))
        rendered = tty.getvalue()
        self.assertIn("WARNING", rendered)
        self.assertIn("SyntheticTtyOnly", rendered)

    def test_unknown_cli_failure_never_returns_exception_detail_or_traceback(self):
        canary = "SB_SYNTHETIC_SECRET_CANARY_7f34"
        args = SimpleNamespace(
            action="inspect", source="fixture", keys=None, mode="keys",
            exact_length=False, json=False, project_dir=".",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(command, "_service", side_effect=RuntimeError(canary)), \
             redirect_stdout(stdout), redirect_stderr(stderr), \
             self.assertRaises(SystemExit):
            command.cmd_secrets({}, args)
        rendered = stdout.getvalue() + stderr.getvalue()
        self.assertIn("operation_failed", rendered)
        self.assertNotIn(canary, rendered)
        self.assertNotIn("Traceback", rendered)

    def test_run_propagates_trusted_child_failure(self):
        args = SimpleNamespace(
            action="run", source="fixture", key="API_TOKEN", secrets=None, project_dir=".",
            destination="API_TOKEN", timeout_seconds=5, command=["--", "child"],
        )
        service = SimpleNamespace(run_many=lambda *args, **kwargs: {
            "ok": True,
            "operation": "run",
            "result": {
                "termination": "exited",
                "exit_code": 11,
                "output": "child failed\n",
            },
        })
        stderr = io.StringIO()
        stdout = io.StringIO()
        with patch.object(command, "_service", return_value=service), \
             redirect_stdout(stdout), redirect_stderr(stderr), \
             self.assertRaises(SystemExit) as raised:
            command.cmd_secrets({}, args)
        self.assertEqual(raised.exception.code, 11)
        self.assertIn("child_failed", stderr.getvalue())
        self.assertIn("exit_code=11", stdout.getvalue())

    def test_run_many_passes_pair_without_nested_child_invocation(self):
        args = SimpleNamespace(
            action="run", source="fixture", key=None, secrets=[
                "ACCESS_KEY=AWS_ACCESS_KEY_ID", "ACCESS_SECRET=AWS_SECRET_ACCESS_KEY",
            ], project_dir=".", destination="SANDBOX_SECRET", timeout_seconds=5,
            command=["--", "child"],
        )
        class Service:
            def __init__(self):
                self.calls = []

            def run_many(self, *call_args, **call_kwargs):
                self.calls.append((call_args, call_kwargs))
                return {
                    "ok": True, "operation": "run", "keys": ["ACCESS_KEY", "ACCESS_SECRET"],
                    "result": {"termination": "exited", "exit_code": 0, "output": ""},
                }

        service = Service()
        with patch.object(command, "_service", return_value=service), \
             patch.object(command, "_emit") as emit:
            command.cmd_secrets({}, args)
        bindings = service.calls[0][0][1]
        self.assertEqual(bindings, [
            ("ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
            ("ACCESS_SECRET", "AWS_SECRET_ACCESS_KEY"),
        ])
        emit.assert_called_once()

    def test_isolated_live_cli_flow_never_prints_fixture_value(self):
        repository = Path(__file__).parent.parent
        fixture_value = "sk_test_" + "SyntheticOnly1234567Qx9Z"
        replacement = "sk_test_" + "ReplacementOnly123456Mn4P"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir(mode=0o700)
            (root / "compose.yaml").write_text("services: {}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {"file": "compose.yaml", "service": "web",
                            "internal_port": 80, "health_path": "/"},
                "secrets": {"sources": {"fixture": {
                    "path": ".env.fixture",
                    "mcpModes": [
                        "source_info", "keys", "metadata", "validate", "masked", "use",
                    ],
                }, "gcp-fixture": {
                    "path": "gcp-credentials.json", "format": "json",
                    "mcpModes": ["source_info", "keys", "metadata"],
                }}},
            }))
            source = root / ".env.fixture"
            source.write_text(f"API_TOKEN={fixture_value}\nOTHER_NAME=identifier\n")
            source.chmod(0o600)
            structured = root / "gcp-credentials.json"
            structured.write_text(json.dumps({
                "type": "service_account",
                "client_email": "synthetic@project.invalid",
                "private_key": "SB_SYNTHETIC_PRIVATE_KEY_NOT_REAL",
            }))
            structured.chmod(0o600)
            environment = synthetic_environment({"SANDBOX_HOME": str(home),
                "SANDBOX_PROJECT_ROOTS": str(root.parent),
            })

            def invoke(*arguments, input_text=None):
                result = subprocess.run(
                    [str(repository / "sb"), "secrets", arguments[0],
                     "--project-dir", str(root), *arguments[1:]],
                    cwd=repository, env=environment, input=input_text,
                    text=True, capture_output=True, timeout=15,
                )
                combined = result.stdout + result.stderr
                self.assertNotIn(fixture_value, combined)
                self.assertNotIn(replacement, combined)
                return result

            listed = invoke("inspect", "--source", "fixture", "--json")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout)["keys"], ["API_TOKEN", "OTHER_NAME"])
            source_info = invoke("source-info", "--source", "fixture", "--json")
            source_payload = json.loads(source_info.stdout)
            self.assertTrue(source_payload["exists"])
            self.assertEqual(source_payload["file_type"], "regular_file")
            self.assertEqual(source_payload["content_state"], "nonempty")
            self.assertNotIn("size_bytes", source_payload)
            exact_info = invoke(
                "source-info", "--source", "fixture", "--exact-size", "--json",
            )
            self.assertEqual(json.loads(exact_info.stdout)["size_bytes"], source.stat().st_size)
            structured_list = invoke("inspect", "--source", "gcp-fixture", "--json")
            self.assertEqual(
                json.loads(structured_list.stdout)["keys"],
                ["/client_email", "/private_key", "/type"],
            )
            parser_canary = "SB_SYNTHETIC_SECRET_CANARY_91ac"
            structured.write_text('{"private_key":"' + parser_canary + '",')
            malformed = invoke("inspect", "--source", "gcp-fixture", "--json")
            self.assertNotEqual(malformed.returncode, 0)
            malformed_output = malformed.stdout + malformed.stderr
            self.assertIn("syntax_unsupported", malformed_output)
            self.assertNotIn(parser_canary, malformed_output)
            self.assertNotIn("Traceback", malformed_output)
            structured.write_text(json.dumps({
                "type": "service_account",
                "client_email": "synthetic@project.invalid",
                "private_key": "SB_SYNTHETIC_PRIVATE_KEY_NOT_REAL",
            }))
            checked = invoke("validate", "--source", "fixture", "--key", "API_TOKEN",
                             "--profile", "stripe-secret-v1", "--json")
            self.assertEqual(json.loads(checked.stdout)["validation"]["syntax"], "pass")
            masked = invoke("inspect", "--source", "fixture", "--key", "API_TOKEN",
                            "--mode", "masked", "--json")
            self.assertIn("<redacted>", json.loads(masked.stdout)["entries"][0]["masked"])
            used = invoke("run", "--source", "fixture", "--key", "API_TOKEN",
                          "--destination", "API_TOKEN", "--", sys.executable, "-c",
                          "import os; print(os.environ['API_TOKEN'])")
            self.assertEqual(used.returncode, 0, used.stderr)
            self.assertIn("[REDACTED]", used.stdout)
            used_default = invoke("run", "--source", "fixture", "--key", "API_TOKEN",
                                  "--", sys.executable, "-c",
                                  "import os; print('BOUND_DEFAULT=' + str('API_TOKEN' in os.environ and 'SANDBOX_SECRET' not in os.environ))")
            self.assertEqual(used_default.returncode, 0, used_default.stderr)
            self.assertIn("BOUND_DEFAULT=True", used_default.stdout)
            used_custom = invoke("run", "--source", "fixture", "--key", "API_TOKEN",
                                 "--destination", "CUSTOM_VAR", "--", sys.executable, "-c",
                                 "import os; print('CUSTOM_VAR=' + str('CUSTOM_VAR' in os.environ))")
            self.assertEqual(used_custom.returncode, 0, used_custom.stderr)
            self.assertIn("CUSTOM_VAR=True", used_custom.stdout)
            updated = invoke("set", "--source", "fixture", "API_TOKEN", "--stdin", "--json",
                             input_text=replacement + "\n")
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(json.loads(updated.stdout)["action"], "updated")
            self.assertIn(replacement, source.read_text())
            refused = invoke("reveal", "--source", "fixture", "--key", "API_TOKEN")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("tty_required", refused.stderr)
            self.assertFalse((home / "runtime/compose").exists())


class FakeStdout:
    """Terminal-like stdout with a byte buffer, for session-mode tests."""

    def __init__(self, tty=True):
        self.tty = tty
        self.buffer = io.BytesIO()

    def isatty(self):
        return self.tty

    def write(self, text):
        self.buffer.write(text.encode())
        return len(text)

    def flush(self):
        pass

    def text(self):
        return self.buffer.getvalue().decode()


class SessionService:
    def __init__(self, end_reason="child_exited", exit_code=0):
        self.end_reason = end_reason
        self.exit_code = exit_code
        self.calls = []
        self.run_many_calls = []

    def run_session(self, source, bindings, argv, **kwargs):
        self.calls.append((source, bindings, argv, kwargs))
        kwargs["on_start"](1_700_000_000.0)
        kwargs["display"](b"child line\n")
        return {
            "ok": True, "operation": "run_session", "source": source, "key": "API_TOKEN",
            "reason_code": self.end_reason, "correlation_id": "c0ffee",
            "result": {
                "end_reason": self.end_reason,
                "exit_code": self.exit_code if self.end_reason == "child_exited" else None,
                "elapsed_class": "1_to_10s", "dropped_chunks": 0,
                "lifetime_seconds": kwargs["lifetime_seconds"],
            },
        }

    def run_many(self, *args, **kwargs):
        self.run_many_calls.append((args, kwargs))
        return {"ok": True, "operation": "run",
                "result": {"termination": "exited", "exit_code": 0, "output": ""}}


class SecretSessionCommandTests(unittest.TestCase):
    def parser(self):
        parser = argparse.ArgumentParser()
        command.configure_parser(parser)
        return parser

    def args(self, **overrides):
        values = dict(
            action="run", source="fixture", key="API_TOKEN", secrets=None, project_dir=".",
            destination=None, timeout_seconds=None, session=True, lifetime_seconds=None,
            command=["--", "child"],
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def invoke(self, args, service, *, terminal_ok=True):
        stdout = FakeStdout()
        stderr = io.StringIO()
        exit_code = 0
        patches = [patch.object(command, "_service", return_value=service),
                   patch.object(command.sys, "stdout", stdout)]
        if terminal_ok:
            patches.append(patch.object(command, "_require_foreground_terminal"))
        with redirect_stderr(stderr):
            for item in patches:
                item.start()
            try:
                command.cmd_secrets({}, args)
            except SystemExit as raised:
                exit_code = raised.code
            finally:
                for item in reversed(patches):
                    item.stop()
        return exit_code, stdout.text(), stderr.getvalue()

    def test_parser_accepts_session_flags_and_timeout_defaults_to_none(self):
        args = self.parser().parse_args([
            "run", "--session", "--lifetime-seconds", "60", "--source", "fixture",
            "--key", "API_TOKEN", "--", "child",
        ])
        self.assertTrue(args.session)
        self.assertEqual(args.lifetime_seconds, "60")
        self.assertIsNone(args.timeout_seconds)
        plain = self.parser().parse_args(["run", "--source", "fixture", "--key", "K", "--", "c"])
        self.assertFalse(plain.session)
        self.assertIsNone(plain.lifetime_seconds)
        self.assertIsNone(plain.timeout_seconds)

    def test_ordinary_run_still_uses_300_second_default(self):
        service = SessionService()
        args = self.args(session=False)
        with patch.object(command, "_service", return_value=service), \
             redirect_stdout(io.StringIO()):
            command.cmd_secrets({}, args)
        self.assertEqual(service.run_many_calls[0][1]["timeout_seconds"], 300)
        self.assertEqual(service.calls, [])

    def test_session_prints_start_and_end_lines(self):
        service = SessionService()
        code, out, err = self.invoke(self.args(), service)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertRegex(lines[0], (
            r"^secrets session: started source=fixture keys=API_TOKEN "
            r"lifetime=28800s \(8h\) ends_at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$"))
        self.assertEqual(lines[1], "child line")
        self.assertEqual(lines[2], (
            "secrets session: ended end_reason=child_exited exit_code=0 "
            "elapsed=1_to_10s dropped_chunks=0"))
        source, bindings, argv, kwargs = service.calls[0]
        self.assertEqual((source, bindings, argv), ("fixture", [("API_TOKEN", "API_TOKEN")], ["child"]))
        self.assertEqual(kwargs["lifetime_seconds"], 28_800)
        self.assertIsNotNone(kwargs["signals"])

    def test_session_lifetime_and_multiple_keys_in_start_line(self):
        service = SessionService()
        args = self.args(key=None, secrets=["A=X", "B=Y"], lifetime_seconds="1900")
        code, out, _err = self.invoke(args, service)
        self.assertEqual(code, 0)
        self.assertIn("keys=A,B lifetime=1900s (31m40s)", out)
        self.assertEqual(service.calls[0][3]["lifetime_seconds"], 1_900)

    def test_child_exit_mapping_matches_run(self):
        for child, expected in ((0, 0), (11, 11), (-9, 1), (137, 1)):
            with self.subTest(child=child):
                code, out, err = self.invoke(self.args(), SessionService("child_exited", child))
                self.assertEqual(code, expected)
                if expected:
                    self.assertIn("error: child_failed: secret use command failed", err)
                self.assertIn(f"exit_code={child}", out)

    def test_non_child_end_exit_codes(self):
        for reason, expected in (("lifetime_expired", 0), ("interrupted", 130), ("hangup", 129)):
            with self.subTest(reason=reason):
                code, out, err = self.invoke(self.args(), SessionService(reason))
                self.assertEqual(code, expected)
                self.assertIn(f"end_reason={reason} exit_code=None", out)
                self.assertNotIn("child_failed", err)

    def test_lifetime_invalid_refused_before_service(self):
        for raw in ("0", "43201", "1.5", "+5", " 5", "5s", "abc", "", "\u0665"):
            with self.subTest(raw=raw):
                service = SessionService()
                code, _out, err = self.invoke(self.args(lifetime_seconds=raw), service,
                                              terminal_ok=False)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("error: lifetime_invalid: "), err)
                self.assertEqual(service.calls, [])

    def test_option_conflicts_refused_before_service(self):
        for overrides in ({"timeout_seconds": 60},
                          {"session": False, "lifetime_seconds": "60"}):
            with self.subTest(overrides=overrides):
                service = SessionService()
                code, _out, err = self.invoke(self.args(**overrides), service,
                                              terminal_ok=False)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("error: option_conflict: "), err)
                self.assertEqual(service.calls, [])
                self.assertEqual(service.run_many_calls, [])

    def run_terminal_case(self, *, stdout_tty=True, open_error=False, fd_tty=True,
                          foreground=True):
        service = SessionService()
        stdout = FakeStdout(tty=stdout_tty)
        stderr = io.StringIO()
        opener = patch.object(command.os, "open", side_effect=OSError("no tty")) if open_error \
            else patch.object(command.os, "open", return_value=9)
        with patch.object(command, "_service", return_value=service), \
             patch.object(command.sys, "stdout", stdout), opener, \
             patch.object(command.os, "close"), \
             patch.object(command.os, "isatty", return_value=fd_tty), \
             patch.object(command.os, "tcgetpgrp", return_value=100), \
             patch.object(command.os, "getpgrp", return_value=100 if foreground else 200), \
             redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            command.cmd_secrets({}, self.args())
        return raised.exception.code, stderr.getvalue(), service

    def test_terminal_and_foreground_required(self):
        for case in ({"stdout_tty": False}, {"open_error": True}, {"fd_tty": False},
                     {"foreground": False}):
            with self.subTest(case=case):
                code, err, service = self.run_terminal_case(**case)
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith("error: tty_required: "), err)
                self.assertEqual(service.calls, [])

    def test_foreground_terminal_passes_probe_and_closes_descriptor(self):
        with patch.object(command.sys, "stdout", FakeStdout()), \
             patch.object(command.os, "open", return_value=9) as opened, \
             patch.object(command.os, "close") as closed, \
             patch.object(command.os, "isatty", return_value=True), \
             patch.object(command.os, "tcgetpgrp", return_value=100), \
             patch.object(command.os, "getpgrp", return_value=100):
            command._require_foreground_terminal()
        self.assertEqual(opened.call_args[0][0], "/dev/tty")
        closed.assert_called_once_with(9)

    def test_run_help_marks_session_operator_only(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit):
            self.parser().parse_args(["run", "--help"])
        help_text = " ".join(stdout.getvalue().split())
        self.assertIn("--session", help_text)
        self.assertIn("operator-only", help_text)
        self.assertIn("foreground terminal", help_text)
        self.assertIn("1-43200", help_text)
        self.assertIn("agents", help_text)


if __name__ == "__main__":
    unittest.main()
