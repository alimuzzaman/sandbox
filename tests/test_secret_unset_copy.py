"""Spec 071: secrets unset, secrets copy and set --skip-unchanged."""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import secrets as command
from sandbox.secrets.audit import SecretAudit
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.remote_copy import (
    RemoteSetRequest, RemoteSetResult, SshRemoteSecretSink, parse_remote_answer,
    remote_set_command,
)
from sandbox.secrets.service import SecretService
from sandbox.secrets.sources import SourceRegistry


CANARY = "SB_SYNTHETIC_COPY_PRIVATE_5e1f"
OTHER = "SB_SYNTHETIC_OTHER_PRIVATE_77ab"
FIXTURE = (
    b"# preserved comment\n"
    b"KEY_A=alpha-public\n"
    b"KEY_B=" + CANARY.encode() + b"\n"
    b"KEY_C='" + OTHER.encode() + b"'\n"
)


class FakeSink:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result or RemoteSetResult(action="created", revision="r1_" + "a" * 32)
        self.error = error

    def put(self, request, value):
        self.calls.append((request, value))
        if self.error is not None:
            raise self.error
        return self.result


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temp.name))
        self.source = self.root / ".env.fixture"
        self.source.write_bytes(FIXTURE)
        self.source.chmod(0o600)
        (self.root / "data.json").write_text('{"KEY_A": "x"}')
        (self.root / "data.json").chmod(0o600)
        self.registry = SourceRegistry(
            self.root, {
                "fixture": {"path": ".env.fixture"},
                "structured": {"path": "data.json", "format": "json"},
                "vault": {"type": "infisical", "key": "VAULT_KEY"},
            }, personal_path=self.root / ".personal",
        )
        self.audit_path = self.root / "runtime/audit.jsonl"
        self.service = SecretService(
            self.registry, SecretAudit(self.audit_path),
            revision_key_path=self.root / "runtime/revision.key",
        )

    def tearDown(self):
        self.temp.cleanup()

    def events(self):
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]

    def assert_value_free(self, *items):
        rendered = json.dumps(items, default=str) + (
            self.audit_path.read_text() if self.audit_path.exists() else "")
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn(OTHER, rendered)

    def _revision_from_set(self):
        from sandbox.secrets.writer import load_revision_key, opaque_revision
        return opaque_revision(load_revision_key(self.root / "runtime/revision.key"),
                               self.source.read_bytes())


class UnsetServiceTests(Base):
    def test_removes_one_key_and_preserves_other_bytes_and_mode(self):
        result = self.service.unset("fixture", "KEY_B")
        self.assertEqual(result["action"], "removed")
        self.assertEqual(result["key"], "KEY_B")
        self.assertTrue(result["revision"].startswith("r1_"))
        self.assertEqual(
            self.source.read_bytes(),
            b"# preserved comment\nKEY_A=alpha-public\nKEY_C='" + OTHER.encode() + b"'\n",
        )
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["revision"], self._revision_from_set())
        events = self.events()
        self.assertEqual([e["phase"] for e in events], ["intent", "outcome"])
        self.assertEqual({e["operation"] for e in events}, {"unset"})
        self.assertEqual(events[-1]["decision"], "succeeded")
        self.assertEqual(events[-1]["keys"], ["KEY_B"])
        self.assertNotIn("destination", events[-1])
        self.assert_value_free(result)

    def test_rerun_refuses_key_missing_and_changes_nothing(self):
        self.service.unset("fixture", "KEY_B")
        before = self.source.read_bytes()
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.unset("fixture", "KEY_B")
        self.assertEqual(caught.exception.code, "key_missing")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.events()[-1]["decision"], "refused")
        self.assertEqual(self.events()[-1]["reason_code"], "key_missing")

    def test_stale_revision_refuses(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.unset("fixture", "KEY_B", expected_revision="r1_" + "0" * 32)
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.source.read_bytes(), FIXTURE)

    def test_matching_revision_is_accepted(self):
        revision = self._revision_from_set()
        result = self.service.unset("fixture", "KEY_A", expected_revision=revision)
        self.assertEqual(result["action"], "removed")

    def test_provider_source_refused_before_audit(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.unset("vault", "VAULT_KEY")
        self.assertEqual(caught.exception.code, "update_denied")
        self.assertEqual(self.events(), [])

    def test_invalid_key_refused_before_audit(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.unset("fixture", "bad key")
        self.assertEqual(caught.exception.code, "key_invalid")
        self.assertEqual(self.events(), [])

    def test_non_dotenv_refused(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.unset("structured", "/KEY_A")
        self.assertEqual(caught.exception.code, "update_unsupported")
        self.assertEqual(self.events()[-1]["decision"], "refused")

    def test_non_cli_surface_refused(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.unset("fixture", "KEY_B", surface="mcp")
        self.assertEqual(caught.exception.code, "update_denied")

    def test_source_changed_during_removal_maps_to_revision_conflict(self):
        from sandbox.secrets import writer

        def changed(*_args, **_kwargs):
            raise SecretBrokerError("source_changed", "source changed before cleanup")
        with patch.object(writer, "remove_keys", changed):
            with self.assertRaises(SecretBrokerError) as caught:
                self.service.unset("fixture", "KEY_B")
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(self.source.read_bytes(), FIXTURE)

    def test_broad_permissions_refused(self):
        self.source.chmod(0o644)
        with self.assertRaises(SecretBrokerError):
            self.service.unset("fixture", "KEY_B")
        self.assertEqual(self.source.read_bytes(), FIXTURE)


class SkipUnchangedTests(Base):
    def test_identical_value_is_already_current_without_write(self):
        before = self.source.stat()
        result = self.service.set("fixture", "KEY_B", CANARY, skip_unchanged=True,
                                  input_channel="stdin")
        self.assertEqual(result["action"], "already_current")
        self.assertEqual(result["revision"], self._revision_from_set())
        after = self.source.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assert_value_free(result)

    def test_identical_quoted_value_is_already_current(self):
        result = self.service.set("fixture", "KEY_C", OTHER, skip_unchanged=True)
        self.assertEqual(result["action"], "already_current")

    def test_create_only_with_identical_value_converges(self):
        result = self.service.set("fixture", "KEY_B", CANARY, intent="create",
                                  skip_unchanged=True)
        self.assertEqual(result["action"], "already_current")

    def test_create_only_with_different_value_still_conflicts(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.set("fixture", "KEY_B", "different-value", intent="create",
                             skip_unchanged=True)
        self.assertEqual(caught.exception.code, "intent_conflict")

    def test_different_value_updates(self):
        result = self.service.set("fixture", "KEY_B", "different-value", skip_unchanged=True)
        self.assertEqual(result["action"], "updated")
        self.assertIn(b"KEY_B=different-value\n", self.source.read_bytes())

    def test_without_flag_identical_value_still_reports_updated(self):
        result = self.service.set("fixture", "KEY_B", CANARY)
        self.assertEqual(result["action"], "updated")

    def test_from_reference_honours_flag(self):
        result = self.service.copy_reference("fixture", "KEY_D", "fixture", "KEY_B",
                                             skip_unchanged=True)
        self.assertEqual(result["action"], "created")
        again = self.service.copy_reference("fixture", "KEY_D", "fixture", "KEY_B",
                                            skip_unchanged=True)
        self.assertEqual(again["action"], "already_current")


class CopyServiceTests(Base):
    def request(self, **overrides):
        fields = dict(remote="example-remote", target_source="personal",
                      target_key="TARGET_KEY")
        fields.update(overrides)
        return RemoteSetRequest(**fields)

    def test_value_reaches_only_the_sink(self):
        sink = FakeSink()
        result = self.service.copy_to_remote("fixture", "KEY_B", self.request(), sink)
        self.assertEqual(len(sink.calls), 1)
        self.assertEqual(sink.calls[0][1], CANARY)
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["target_key"], "TARGET_KEY")
        self.assertEqual(result["remote_revision"], "r1_" + "a" * 32)
        events = self.events()
        self.assertEqual([e["phase"] for e in events], ["intent", "outcome"])
        self.assertEqual({e["operation"] for e in events}, {"copy"})
        self.assertEqual({e["destination"] for e in events},
                         {"remote:example-remote/personal/TARGET_KEY"})
        self.assertEqual({e["input_channel"] for e in events}, {"remote_stdin"})
        self.assertEqual(events[-1]["reason_code"], "created")
        self.assertIsNone(events[-1]["revision"])
        self.assert_value_free(result)

    def test_already_current_and_updated_pass_through(self):
        for action in ("already_current", "updated"):
            sink = FakeSink(RemoteSetResult(action=action))
            result = self.service.copy_to_remote("fixture", "KEY_B", self.request(), sink)
            self.assertEqual(result["action"], action)
            self.assertEqual(self.events()[-1]["reason_code"], action)

    def test_remote_refusal_code_passes_through_and_is_audited(self):
        sink = FakeSink(error=SecretBrokerError("intent_conflict", "the remote refused the secret update"))
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.copy_to_remote("fixture", "KEY_B", self.request(intent="create"), sink)
        self.assertEqual(caught.exception.code, "intent_conflict")
        self.assertEqual(self.events()[-1]["decision"], "refused")
        self.assert_value_free()

    def test_missing_local_key_never_reaches_sink(self):
        sink = FakeSink()
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.copy_to_remote("fixture", "KEY_Z", self.request(), sink)
        self.assertEqual(caught.exception.code, "key_missing")
        self.assertEqual(sink.calls, [])

    def test_multiline_or_oversize_value_refused_before_sink(self):
        from sandbox.secrets.models import MAX_VALUE_BYTES
        sink = FakeSink()
        for bad in ("line1\nline2", "a\rb", "a\x00b", "x" * (MAX_VALUE_BYTES + 1)):
            with patch.object(SecretService, "_entry_value", staticmethod(lambda record, v=bad: v)):
                with self.assertRaises(SecretBrokerError) as caught:
                    self.service.copy_to_remote("fixture", "KEY_B", self.request(), sink)
            self.assertEqual(caught.exception.code, "input_invalid")
        self.assertEqual(sink.calls, [])

    def test_provider_source_refused_before_audit(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.copy_to_remote("vault", "VAULT_KEY", self.request(), FakeSink())
        self.assertEqual(caught.exception.code, "update_denied")
        self.assertEqual(self.events(), [])

    def test_invalid_request_fields_refused(self):
        for overrides in ({"remote": "Bad Name"}, {"target_source": "-x"},
                          {"target_key": "bad key"}, {"intent": "upsert"},
                          {"target_project_dir": "relative/dir"},
                          {"target_project_dir": "/a/../b"},
                          {"target_project_dir": "/a\nb"}):
            with self.assertRaises(SecretBrokerError, msg=str(overrides)):
                self.request(**overrides)

    def test_invalid_sink_answer_is_a_failure(self):
        sink = FakeSink(SimpleNamespace(action="deleted", revision=None))
        with self.assertRaises(SecretBrokerError) as caught:
            self.service.copy_to_remote("fixture", "KEY_B", self.request(), sink)
        self.assertEqual(caught.exception.code, "remote_failed")


class SshSinkTests(unittest.TestCase):
    def entry(self):
        return {"ssh": "deploy@203.0.113.10", "provisioned": True}

    def runner(self, returncode=0, stdout=b"", raise_exc=None):
        calls = []

        def run(entry, command_text, timeout=30, input_data=None):
            calls.append(SimpleNamespace(entry=entry, command=command_text,
                                         timeout=timeout, input_data=input_data))
            if raise_exc is not None:
                raise raise_exc
            return subprocess.CompletedProcess(args=["ssh"], returncode=returncode,
                                               stdout=stdout, stderr=b"remote stderr " + CANARY.encode())
        return run, calls

    def request(self, **overrides):
        fields = dict(remote="example-remote", target_source="personal", target_key="TARGET_KEY")
        fields.update(overrides)
        return RemoteSetRequest(**fields)

    def test_value_only_on_stdin_and_command_is_value_free(self):
        answer = json.dumps({"ok": True, "operation": "set", "action": "created",
                             "revision": "r1_" + "b" * 32}).encode()
        run, calls = self.runner(stdout=answer + b"\n")
        result = SshRemoteSecretSink(self.entry(), runner=run).put(self.request(), CANARY)
        self.assertEqual(result, RemoteSetResult(action="created", revision="r1_" + "b" * 32))
        self.assertEqual(calls[0].input_data, CANARY.encode() + b"\n")
        self.assertNotIn(CANARY, calls[0].command)
        self.assertIn("--stdin", calls[0].command)
        self.assertIn("--skip-unchanged", calls[0].command)
        self.assertIn('exec "$home/sb-src/sb"', calls[0].command)
        self.assertIn('--project-dir "$home"', calls[0].command)
        self.assertEqual(calls[0].timeout, 60)

    def test_command_flags_and_explicit_project_dir(self):
        create = remote_set_command(self.request(intent="create",
                                                 target_project_dir="/srv/example project"))
        self.assertIn("--create-only", create)
        self.assertIn("--project-dir '/srv/example project'", create)
        self.assertIn("--replace-only", remote_set_command(self.request(intent="replace")))
        either = remote_set_command(self.request())
        self.assertNotIn("--create-only", either)
        self.assertNotIn("--replace-only", either)

    def test_exit_codes_and_timeouts_map_to_bounded_errors(self):
        cases = [
            (dict(returncode=255), "remote_unreachable"),
            (dict(returncode=2, stdout=b"usage: sb ... unrecognized arguments\n"), "remote_unsupported"),
            (dict(returncode=1, stdout=b"not json\n"), "remote_failed"),
            (dict(returncode=0, stdout=b""), "remote_failed"),
            (dict(raise_exc=subprocess.TimeoutExpired(["ssh"], 60)), "remote_timeout"),
            (dict(raise_exc=OSError("no ssh")), "remote_unreachable"),
            (dict(returncode=1, stdout=json.dumps({"ok": False, "error": {
                "code": "intent_conflict", "message": "x"}}).encode()), "intent_conflict"),
            (dict(returncode=1, stdout=json.dumps({"ok": False, "error": {
                "code": "Bad Code!"}}).encode()), "remote_failed"),
            (dict(returncode=0, stdout=json.dumps({"ok": True, "action": "removed"}).encode()),
             "remote_failed"),
        ]
        for kwargs, code in cases:
            run, _ = self.runner(**kwargs)
            with self.assertRaises(SecretBrokerError, msg=str(kwargs)) as caught:
                SshRemoteSecretSink(self.entry(), runner=run).put(self.request(), CANARY)
            self.assertEqual(caught.exception.code, code, str(kwargs))
            rendered = json.dumps(caught.exception.as_dict())
            self.assertNotIn(CANARY, rendered)
            self.assertNotIn("stderr", rendered)
            self.assertIsNone(caught.exception.__cause__)

    def test_answer_after_noise_lines_and_bad_revision_dropped(self):
        stdout = b"notice: migrated state\n" + json.dumps(
            {"ok": True, "action": "already_current", "revision": "nope"}).encode() + b"\n"
        result = parse_remote_answer(0, stdout)
        self.assertEqual(result, RemoteSetResult(action="already_current", revision=None))

    def test_for_remote_refuses_unknown_and_unprovisioned(self):
        with self.assertRaises(SecretBrokerError) as caught:
            SshRemoteSecretSink.for_remote("example-remote", lookup=lambda name: None)
        self.assertEqual(caught.exception.code, "remote_unknown")
        with self.assertRaises(SecretBrokerError) as caught:
            SshRemoteSecretSink.for_remote("example-remote", lookup=lambda name: {"ssh": "x"})
        self.assertEqual(caught.exception.code, "remote_not_provisioned")
        with self.assertRaises(SecretBrokerError) as caught:
            SshRemoteSecretSink.for_remote("Bad Name", lookup=lambda name: self.entry())
        self.assertEqual(caught.exception.code, "input_invalid")


class CommandTests(Base):
    def parser(self):
        parser = argparse.ArgumentParser()
        command.configure_parser(parser)
        return parser

    def invoke(self, *arguments, input_text=None, sink=None):
        args = self.parser().parse_args([arguments[0], "--project-dir", str(self.root), *arguments[1:]])
        stdout, stderr = io.StringIO(), io.StringIO()
        stdin = SimpleNamespace(buffer=io.BytesIO((input_text or "").encode()))
        code = 0
        with patch.object(command, "_service", return_value=self.service), \
                patch.object(command, "_copy_sink", return_value=sink or FakeSink()), \
                patch.object(command.sys, "stdin", stdin), \
                patch.object(command, "_controlling_tty", side_effect=OSError("no tty")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                command.cmd_secrets({}, args)
            except SystemExit as exc:
                code = exc.code
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(CANARY, combined)
        self.assertNotIn(OTHER, combined)
        return SimpleNamespace(returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())

    def test_parser_shapes(self):
        args = self.parser().parse_args(["unset", "--source", "fixture", "KEY_B", "--if-revision", "r1_x"])
        self.assertEqual((args.source, args.key, args.if_revision), ("fixture", "KEY_B", "r1_x"))
        args = self.parser().parse_args([
            "copy", "--source", "fixture", "--key", "KEY_B", "--remote", "example-remote",
            "--target-source", "personal", "--as", "NEW_KEY", "--create-only"])
        self.assertEqual((args.target_key, args.create_only, args.replace_only), ("NEW_KEY", True, False))
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.parser().parse_args(["copy", "--source", "fixture", "--key", "K", "--remote", "r",
                                      "--target-source", "p", "--create-only", "--replace-only"])
        self.assertIn("unset", command.ACTIONS)
        self.assertIn("copy", command.ACTIONS)

    def test_unset_json_and_rerun(self):
        first = self.invoke("unset", "--source", "fixture", "KEY_B", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual((payload["operation"], payload["action"]), ("unset", "removed"))
        second = self.invoke("unset", "--source", "fixture", "KEY_B", "--json")
        self.assertEqual(second.returncode, 1)
        self.assertEqual(json.loads(second.stdout)["error"]["code"], "key_missing")

    def test_unset_human_output(self):
        result = self.invoke("unset", "--source", "fixture", "KEY_A")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secrets unset: ok", result.stdout)
        self.assertIn("removed: KEY_A", result.stdout)

    def test_set_skip_unchanged_flag(self):
        result = self.invoke("set", "--source", "fixture", "KEY_B", "--stdin", "--skip-unchanged",
                             "--json", input_text=CANARY + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["action"], "already_current")

    def test_copy_defaults_target_key_and_reports_action(self):
        sink = FakeSink(RemoteSetResult(action="updated", revision="r1_" + "c" * 32))
        result = self.invoke("copy", "--source", "fixture", "--key", "KEY_B", "--remote",
                             "example-remote", "--target-source", "personal", "--json", sink=sink)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((payload["action"], payload["target_key"]), ("updated", "KEY_B"))
        request, value = sink.calls[0]
        self.assertEqual((request.target_key, request.intent), ("KEY_B", "either"))
        self.assertEqual(value, CANARY)

    def test_copy_human_output_and_rename(self):
        sink = FakeSink(RemoteSetResult(action="already_current"))
        result = self.invoke("copy", "--source", "fixture", "--key", "KEY_B", "--remote",
                             "example-remote", "--target-source", "personal", "--as", "NEW_KEY",
                             "--replace-only", sink=sink)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already_current: KEY_B -> remote:example-remote/personal/NEW_KEY", result.stdout)
        self.assertEqual(sink.calls[0][0].intent, "replace")

    def test_copy_unknown_remote_refused_before_audit(self):
        args = self.parser().parse_args([
            "copy", "--project-dir", str(self.root), "--source", "fixture", "--key", "KEY_B",
            "--remote", "example-remote", "--target-source", "personal", "--json"])
        stdout = io.StringIO()
        with patch.object(command, "_service", return_value=self.service), \
                patch("sandbox.core._remote.get_remote", return_value=None), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                command.cmd_secrets({}, args)
        self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "remote_unknown")
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
