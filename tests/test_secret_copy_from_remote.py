"""`secrets copy --from-remote`: owner-granted egress into a local source."""
from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import secrets as command
from sandbox.config.secrets import normalize_secret_config
from sandbox.secrets.audit import SecretAudit
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.remote_copy import (
    EGRESS_NONCE_BYTES, RemoteEgressAnswer, RemoteGetRequest, SshRemoteSecretSink,
    egress_fingerprint, parse_egress_answer, remote_egress_command,
)
from sandbox.secrets.service import SecretService
from sandbox.secrets.sources import SourceRegistry
from tests.subprocess_support import run_test_process

REPO = Path(__file__).resolve().parents[1]
CANARY = "SB_SYNTHETIC_EGRESS_PRIVATE_9c41d2"
OTHER = "SB_SYNTHETIC_EGRESS_OTHER_PRIVATE_3a7e"
REMOTE_FIXTURE = (b"GRANTED=" + CANARY.encode() + b"\n"
                  b"WITHHELD=" + OTHER.encode() + b"\n")
NONCE = bytes(range(EGRESS_NONCE_BYTES))


def _service(root: Path, sources: dict, personal: Path) -> SecretService:
    return SecretService(
        SourceRegistry(root, sources, personal_path=personal),
        SecretAudit(root / "runtime/audit.jsonl"),
        revision_key_path=root / "runtime/revision.key",
    )


class InProcessOrigin:
    """Stands in for the SSH channel: calls the remote broker's own egress."""

    def __init__(self, remote_service, *, tamper=False):
        self.remote = remote_service
        self.tamper = tamper
        self.calls = []

    def get(self, request, nonce):
        self.calls.append((request, len(nonce)))
        captured = {}
        self.remote.egress(request.source, request.key, nonce,
                           lambda value, fingerprint: captured.update(v=value, f=fingerprint))
        fingerprint = "0" * 64 if self.tamper else captured["f"]
        return RemoteEgressAnswer(captured["v"], fingerprint)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.temp.name))
        self.remote_root = base / "remote"
        self.local_root = base / "local"
        for root in (self.remote_root, self.local_root):
            root.mkdir()
        source = self.remote_root / ".env.app"
        source.write_bytes(REMOTE_FIXTURE)
        source.chmod(0o600)
        self.personal = self.local_root / ".personal"
        self.personal.write_bytes(b"EXISTING=keep\n")
        self.personal.chmod(0o600)
        self.remote = _service(self.remote_root, {
            "app-env": {"path": ".env.app", "egressKeys": ["GRANTED"]},
            "plain": {"path": ".env.app"},
        }, self.remote_root / ".remote-personal")
        self.local = _service(self.local_root, {}, self.personal)
        self.request = RemoteGetRequest(remote="example-remote", source="app-env", key="GRANTED")
        # Egress grants count only from the Sandbox home's own registration.
        home = patch("sandbox.core._paths.BASE", self.remote_root)
        home.start()
        self.addCleanup(home.stop)

    def tearDown(self):
        self.temp.cleanup()

    def audit(self, root):
        path = root / "runtime/audit.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_value_free(self, *items):
        rendered = json.dumps(items, default=str)
        for root in (self.remote_root, self.local_root):
            path = root / "runtime/audit.jsonl"
            if path.exists():
                rendered += path.read_text()
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn(OTHER, rendered)

    def personal_has(self, key, value):
        document = self.local._document("personal")[1]
        record = document.entries.get(key)
        return record is not None and record.value == value


class GrantConfigTests(unittest.TestCase):
    def normalize(self, descriptor):
        return normalize_secret_config({"secrets": {"sources": {"app-env": descriptor}}})

    def test_grant_is_sorted_and_absent_grant_keeps_shape(self):
        config = self.normalize({"path": ".env.app", "egressKeys": ["B_KEY", "A_KEY"]})
        self.assertEqual(config["sources"]["app-env"]["egressKeys"], ["A_KEY", "B_KEY"])
        config = self.normalize({"path": ".env.app"})
        self.assertNotIn("egressKeys", config["sources"]["app-env"])

    def test_invalid_grants_refuse(self):
        for descriptor in (
            {"path": ".env.app", "egressKeys": []},
            {"path": ".env.app", "egressKeys": ["A", "A"]},
            {"path": ".env.app", "egressKeys": ["*"]},
            {"path": ".env.app", "egressKeys": "A_KEY"},
            {"path": "c.json", "format": "json", "egressKeys": ["A_KEY"]},
        ):
            with self.subTest(descriptor=descriptor), self.assertRaises(ValueError):
                self.normalize(descriptor)


class EgressServiceTests(Base):
    def test_granted_key_reaches_only_the_writer_with_nonce_fingerprint(self):
        delivered = []
        payload = self.remote.egress("app-env", "GRANTED", NONCE,
                                     lambda value, fp: delivered.append((value, fp)))
        self.assertEqual(delivered, [(CANARY, egress_fingerprint(NONCE, CANARY))])
        self.assertEqual(payload["operation"], "egress")
        events = self.audit(self.remote_root)
        self.assertEqual([(e["operation"], e["phase"], e["decision"]) for e in events],
                         [("egress", "intent", "requested"), ("egress", "outcome", "succeeded")])
        self.assertEqual(events[0]["destination"], "controller")
        self.assert_value_free(payload)

    def test_ungranted_key_and_ungranted_source_refuse_without_writing(self):
        for source, key in (("app-env", "WITHHELD"), ("plain", "GRANTED")):
            with self.subTest(source=source):
                with self.assertRaises(SecretBrokerError) as caught:
                    self.remote.egress(source, key, NONCE, lambda *_: self.fail("value written"))
                self.assertEqual(caught.exception.code, "egress_not_granted")
        self.assertEqual({e["reason_code"] for e in self.audit(self.remote_root)
                          if e["phase"] == "outcome"}, {"egress_not_granted"})
        self.assert_value_free()

    def test_a_project_registration_cannot_grant_egress(self):
        # Fable review 10: a project config is agent-writable, so its egressKeys
        # never let a value leave the host.
        with patch("sandbox.core._paths.BASE", self.local_root):
            with self.assertRaises(SecretBrokerError) as caught:
                self.remote.egress("app-env", "GRANTED", NONCE, lambda *_: self.fail("value written"))
        self.assertEqual(caught.exception.code, "egress_not_granted")
        self.assert_value_free()

    def test_personal_bad_nonce_and_non_cli_refuse_before_audit(self):
        cases = (
            (("personal", "GRANTED", NONCE), {}, "egress_denied"),
            (("app-env", "GRANTED", b"short"), {}, "input_invalid"),
            (("app-env", "GRANTED", NONCE), {"surface": "mcp"}, "egress_denied"),
        )
        for arguments, kwargs, code in cases:
            with self.subTest(code=code), self.assertRaises(SecretBrokerError) as caught:
                self.remote.egress(*arguments, lambda *_: self.fail("value written"), **kwargs)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.audit(self.remote_root), [])


class CopyFromRemoteServiceTests(Base):
    def test_creates_then_converges_with_metadata_only(self):
        origin = InProcessOrigin(self.remote)
        first = self.local.copy_from_remote("personal", "ESCROWED", self.request, origin)
        self.assertEqual((first["action"], first["key"], first["remote_key"]),
                         ("created", "ESCROWED", "GRANTED"))
        self.assertEqual(first["verification"]["fingerprint_match"], True)
        self.assertEqual(first["verification"]["local"],
                         {"state": "present", "length_bucket": "32-63"})
        self.assertTrue(self.personal_has("ESCROWED", CANARY))
        self.assertTrue(self.personal_has("EXISTING", "keep"))
        second = self.local.copy_from_remote("personal", "ESCROWED", self.request, origin)
        self.assertEqual(second["action"], "already_current")
        events = self.audit(self.local_root)
        self.assertEqual(events[0]["destination"], "remote:example-remote/app-env/GRANTED")
        self.assertEqual(events[0]["input_channel"], "remote_ssh")
        self.assertEqual(origin.calls[0][1], EGRESS_NONCE_BYTES)
        self.assert_value_free(first, second)

    def test_create_only_refuses_a_different_existing_value(self):
        with self.assertRaises(SecretBrokerError) as caught:
            self.local.copy_from_remote("personal", "EXISTING", self.request,
                                        InProcessOrigin(self.remote))
        self.assertEqual(caught.exception.code, "intent_conflict")
        self.assertTrue(self.personal_has("EXISTING", "keep"))

    def test_fingerprint_mismatch_writes_nothing(self):
        before = self.personal.read_bytes()
        with self.assertRaises(SecretBrokerError) as caught:
            self.local.copy_from_remote("personal", "ESCROWED", self.request,
                                        InProcessOrigin(self.remote, tamper=True))
        self.assertEqual(caught.exception.code, "transfer_mismatch")
        self.assertEqual(self.personal.read_bytes(), before)
        self.assert_value_free()

    def test_remote_refusal_passes_through_and_nothing_is_written(self):
        request = RemoteGetRequest(remote="example-remote", source="app-env", key="WITHHELD")
        before = self.personal.read_bytes()
        with self.assertRaises(SecretBrokerError) as caught:
            self.local.copy_from_remote("personal", "ESCROWED", request, InProcessOrigin(self.remote))
        self.assertEqual(caught.exception.code, "egress_not_granted")
        self.assertEqual(self.personal.read_bytes(), before)

    def test_invalid_inputs_refuse_before_audit(self):
        for arguments, code in (
            (("personal", "bad key", self.request, InProcessOrigin(self.remote)), "key_invalid"),
            (("personal", "K", object(), InProcessOrigin(self.remote)), "input_invalid"),
            (("personal", "K", self.request, object()), "input_invalid"),
        ):
            with self.subTest(code=code), self.assertRaises(SecretBrokerError) as caught:
                self.local.copy_from_remote(*arguments)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.audit(self.local_root), [])


class TransportTests(unittest.TestCase):
    def answer(self, **fields):
        return (json.dumps({"ok": True, "value": CANARY,
                            "fingerprint": egress_fingerprint(NONCE, CANARY), **fields}) + "\n").encode()

    def test_command_is_value_free_and_quoted(self):
        command_text = remote_egress_command(RemoteGetRequest(
            remote="r1", source="app-env", key="GRANTED", source_project_dir="/srv/my project"))
        self.assertIn("secrets egress --project-dir '/srv/my project' --source app-env "
                      "--key GRANTED --json", command_text)
        default = remote_egress_command(RemoteGetRequest(remote="r1", source="app-env", key="K"))
        self.assertIn('--project-dir "$home"', default)

    def test_answers_and_exits_map_to_bounded_results(self):
        answer = parse_egress_answer(0, b"noise\n" + self.answer())
        self.assertNotIn(CANARY, repr(answer))
        self.assertEqual(answer.take(), CANARY)
        self.assertEqual(answer.take(), "")
        refusal = json.dumps({"ok": False, "error": {"code": "egress_not_granted"}}).encode()
        for returncode, stdout, code in (
            (255, b"", "remote_unreachable"),
            (2, b"usage: sb secrets ...", "remote_unsupported"),
            (1, b"", "remote_failed"),
            (1, refusal, "egress_not_granted"),
            (0, self.answer(fingerprint="xyz"), "remote_failed"),
            (0, self.answer(value=""), "remote_failed"),
        ):
            with self.subTest(code=code), self.assertRaises(SecretBrokerError) as caught:
                parse_egress_answer(returncode, stdout)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn(CANARY, str(caught.exception))

    def test_largest_escaped_value_still_parses(self):
        value = "\u00e9" * 32_768  # 64 KiB of UTF-8, sixfold as JSON escapes
        stdout = (json.dumps({"ok": True, "value": value,
                              "fingerprint": egress_fingerprint(NONCE, value)}) + "\n").encode()
        self.assertGreater(len(stdout), 2 * 65_536)
        self.assertEqual(parse_egress_answer(0, stdout).take(), value)

    def test_nonce_only_on_stdin_and_timeout_is_bounded(self):
        calls = []

        def runner(entry, command_text, *, timeout, input_data):
            calls.append((command_text, input_data))
            return SimpleNamespace(returncode=0, stdout=self.answer())

        sink = SshRemoteSecretSink({"provisioned": True}, runner=runner)
        request = RemoteGetRequest(remote="r1", source="app-env", key="GRANTED")
        self.assertEqual(sink.get(request, NONCE).take(), CANARY)
        self.assertEqual(calls[0][1], NONCE.hex().encode() + b"\n")
        self.assertNotIn(NONCE.hex(), calls[0][0])

        def slow(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("ssh", 1, output=self.answer())

        with self.assertRaises(SecretBrokerError) as caught:
            SshRemoteSecretSink({"provisioned": True}, runner=slow).get(request, NONCE)
        self.assertEqual(caught.exception.code, "remote_timeout")
        self.assertIsNone(caught.exception.__context__)


class RealCliEgressTests(Base):
    """Both halves through the real `sb secrets egress` CLI in a child process."""

    def setUp(self):
        super().setUp()
        if not Path("/proc/self/comm").exists():
            self.skipTest("egress identifies its SSH session through /proc")
        # The grant lives in the remote Sandbox home's own config (owner level).
        self.home = self.remote_root / "sandbox-home"
        self.home.mkdir()
        (self.home / "sandbox.config.json").write_text(json.dumps({"secrets": {"sources": {
            "app-env": {"path": ".env.app", "egressKeys": ["GRANTED"]}}}}))
        source = self.home / ".env.app"
        source.write_bytes(REMOTE_FIXTURE)
        source.chmod(0o600)
        (self.home / "sb-src").symlink_to(REPO)
        # A copy of sh named sshd stands in for the SSH server that runs the
        # session's command; its child subshell execs sb, so sb's parent is "sshd".
        self.sshd = self.remote_root / "bin" / "sshd"
        self.sshd.parent.mkdir()
        self.sshd.write_bytes(Path("/bin/sh").read_bytes())
        self.sshd.chmod(0o755)

    def env(self, *, ssh=True):
        env = {"HOME": str(self.home), "SANDBOX_HOME": str(self.home), "PATH": os.environ["PATH"],
               "SANDBOX_PROJECT_ROOTS": str(self.home), "SANDBOX_AUTO_UPDATE": "0",
               "SANDBOX_SECRETS_FILE": str(self.home / ".none"), "PYTHONDONTWRITEBYTECODE": "1"}
        if ssh:
            env["SSH_CONNECTION"] = "192.0.2.1 50000 192.0.2.2 22"
        return env

    def runner(self, entry, command_text, *, timeout, input_data):
        return run_test_process([str(self.sshd), "-c", "(" + command_text + ")"], input=input_data,
                                capture_output=True, env=self.env(), timeout=60)

    def test_real_egress_lands_in_local_source_and_refuses_ungranted(self):
        origin = SshRemoteSecretSink({"provisioned": True}, runner=self.runner)
        request = RemoteGetRequest(remote="example-remote", source="app-env", key="GRANTED")
        payload = self.local.copy_from_remote("personal", "ESCROWED", request, origin)
        self.assertEqual((payload["action"], payload["verification"]["fingerprint_match"]),
                         ("created", True))
        self.assertTrue(self.personal_has("ESCROWED", CANARY))
        withheld = RemoteGetRequest(remote="example-remote", source="app-env", key="WITHHELD")
        with self.assertRaises(SecretBrokerError) as caught:
            self.local.copy_from_remote("personal", "OTHER_ESCROW", withheld, origin)
        self.assertEqual(caught.exception.code, "egress_not_granted")
        self.assert_value_free(payload)

    def test_real_egress_refuses_without_json_and_with_bad_nonce(self):
        base = [str(REPO / "sb"), "secrets", "egress", "--project-dir",
                str(self.home), "--source", "app-env", "--key", "GRANTED"]
        session = lambda argv: [str(self.sshd), "-c", "(exec " + " ".join(map(shlex.quote, argv)) + ")"]
        for argv, stdin, code, env in (
                (session(base), NONCE.hex() + "\n", "egress_requires_transport", self.env()),
                (session(base + ["--json"]), "not-a-nonce\n", "input_invalid", self.env()),
                # Fable review 10: a host-side process piping a nonce in is not an
                # SSH session's command, with or without SSH_CONNECTION set.
                (base + ["--json"], NONCE.hex() + "\n", "egress_requires_transport", self.env()),
                (session(base + ["--json"]), NONCE.hex() + "\n", "egress_requires_transport",
                 self.env(ssh=False))):
            with self.subTest(code=code, argv=argv[0]):
                result = run_test_process(argv, input=stdin.encode(), capture_output=True, env=env)
                self.assertNotEqual(result.returncode, 0)
                output = (result.stdout + result.stderr).decode("utf-8", "replace")
                self.assertIn(code, output)
                self.assertNotIn(CANARY, output)


class CommandTests(Base):
    def invoke(self, *arguments, origin=None):
        parser = argparse.ArgumentParser()
        command.configure_parser(parser)
        args = parser.parse_args([arguments[0], "--project-dir", str(self.local_root), *arguments[1:]])
        stdout = io.StringIO()
        code = 0
        with patch.object(command, "_service", return_value=self.local), \
                patch.object(command, "_copy_sink", return_value=origin or InProcessOrigin(self.remote)), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            try:
                command.cmd_secrets({}, args)
            except SystemExit as exc:
                code = exc.code
        self.assertNotIn(CANARY, stdout.getvalue())
        return code, stdout.getvalue()

    def test_from_remote_json_and_human_output(self):
        code, out = self.invoke("copy", "--from-remote", "example-remote", "--source", "app-env",
                                "--key", "GRANTED", "--to", "personal", "--as", "ESCROWED", "--json")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual((payload["action"], payload["remote_source"]), ("created", "app-env"))
        code, out = self.invoke("copy", "--from-remote", "example-remote", "--source", "app-env",
                                "--key", "GRANTED", "--to", "personal", "--as", "ESCROWED")
        self.assertEqual(code, 0, out)
        self.assertIn("already_current: remote:example-remote/app-env/GRANTED -> personal/ESCROWED", out)
        self.assertIn("fingerprint_match=True", out)

    def test_flag_combinations_refuse(self):
        for arguments in (
            ("copy", "--from-remote", "r1", "--source", "app-env", "--key", "GRANTED", "--json"),
            ("copy", "--from-remote", "r1", "--source", "app-env", "--key", "GRANTED",
             "--to", "personal", "--remote", "r1", "--json"),
            ("copy", "--source", "personal", "--key", "K", "--remote", "r1",
             "--target-source", "app-env", "--to", "personal", "--json"),
            ("copy", "--source", "personal", "--key", "K", "--json"),
        ):
            with self.subTest(arguments=arguments):
                code, out = self.invoke(*arguments)
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(out)["error"]["code"], "input_invalid")
        self.assertIn("egress", command.ACTIONS)


if __name__ == "__main__":
    unittest.main()
