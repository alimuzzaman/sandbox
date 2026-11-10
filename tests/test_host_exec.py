"""Spec 072: `sb host exec` runs one argv in a recorded target's service."""
from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sandbox.core._hosting as hosting
from sandbox.commands import hosting as hosting_cmd
from sandbox.commands import host_exec as exec_cmd
from sandbox.hosting import host_exec
from sandbox.hosting.host_exec import ExecOutcome, ExecRefusal, ExecRequest
from sandbox.hosting.recovery.repository import RecoveryRepository
from sandbox.hosting.target_resolution import ResolvedTarget

REPO = Path(__file__).resolve().parents[1]

PLAIN = """version: 1
project: example-site
environments:
  production:
    compose:
      files: [compose.yml]
      service: web
      container_port: 8080
    healthcheck:
      path: /
      statuses: [200, 399]
    deploy:
      allowed_branches: [main]
      require_clean: true
    host:
      primary: site.example.test
    cloudflare:
      proxied: true
      tls: origin-ca
      ssl_mode: strict
"""

# Stands in for `ssh <host> <command>`: records what it saw, copies stdin to
# a file outside the Sandbox home, and exits with the code it is given.
CHILD = r"""
import json, os, sys
out_dir, exit_code, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3]
data = sys.stdin.buffer.read()
with open(os.path.join(out_dir, "stdin.bin"), "wb") as handle:
    handle.write(data)
with open(os.path.join(out_dir, "environ.json"), "w") as handle:
    json.dump(dict(os.environ), handle)
if mode == "echo":
    sys.stdout.buffer.write(data)
elif mode == "flood":
    sys.stdout.write("x" * 100)
    sys.stderr.write("y" * 100)
else:
    sys.stdout.write(f"received {len(data)} bytes\n")
sys.stderr.write("child stderr\n")
sys.stdout.flush()
sys.exit(exit_code)
"""

TARGET = ResolvedTarget(remote="myvps", project="example-site", environment="production",
                        kind="plain", project_dir="/checkout")


def _request(**overrides) -> ExecRequest:
    values = dict(target=TARGET, service="web", argv=("sh", "-c", "id -u"))
    values.update(overrides)
    return ExecRequest(**values)


class RequestAndCommandTests(unittest.TestCase):
    def _prefix(self) -> str:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sandbox.hosting.yml").write_text(PLAIN)
            Path(directory, "compose.yml").write_text("services: {}\n")
            validated = hosting.validate_manifest(directory, "production")
        runtime = "/srv/sandbox/runtime/hosts/example-site/production"
        return hosting_cmd._compose_prefix(
            validated, "/srv/sandbox/deploy-src/hosts/example-site",
            f"{runtime}/compose.override.yml", f"{runtime}/environment.env")

    def test_remote_command_uses_apply_prefix_and_no_user_override(self):
        prefix = self._prefix()
        argv = ("sh", "-c", "echo 'a b'; id -u")
        command = host_exec.remote_command(prefix, _request(service="hub", argv=argv))
        tokens = shlex.split(command)
        self.assertEqual(tokens[:5], ["timeout", "--kill-after=10", "300", "env",
                                      "SANDBOX_HOST_ENV_FILE=/srv/sandbox/runtime/hosts/"
                                      "example-site/production/environment.env"])
        self.assertIn(prefix, command)
        self.assertIn("-p sandbox-host-example-site-production", command)
        exec_at = tokens.index("exec")
        self.assertEqual(tokens[exec_at - 2:exec_at], ["--profile", "*"])
        self.assertEqual(tokens[exec_at:exec_at + 3], ["exec", "-T", "hub"])
        self.assertEqual(tuple(tokens[exec_at + 3:]), argv)
        for flag in ("-u", "--user", "-w", "--workdir", "-e", "--env", "--privileged"):
            self.assertNotIn(flag, tokens[:exec_at + 3])

    def test_wp_cli_on_wordpress_service(self):
        command = host_exec.remote_command(
            self._prefix(), _request(service="wordpress", argv=("wp", "option", "get", "siteurl")))
        self.assertTrue(command.endswith(" exec -T wordpress wp option get siteurl"))

    def test_timeout_bounds(self):
        self.assertEqual(host_exec.parse_timeout(None), 300)
        self.assertEqual(host_exec.parse_timeout(3600), 3600)
        for value in (0, 3601, -1, True, "60"):
            with self.subTest(value=value), self.assertRaises(ExecRefusal) as caught:
                host_exec.parse_timeout(value)
            self.assertEqual(caught.exception.exit_code, 2)

    def test_request_requires_argv_and_service(self):
        for overrides in (dict(argv=()), dict(argv=["sh"]), dict(service="")):
            with self.subTest(overrides=overrides), self.assertRaises(ExecRefusal):
                _request(**overrides)

    def test_split_passthrough(self):
        cases = [
            (["host", "exec", "--remote", "R", "--", "sh", "-c", "x"],
             (["host", "exec", "--remote", "R"], ["sh", "-c", "x"])),
            (["--label", "qa", "host", "exec", "--", "a", "--", "b"],
             (["--label", "qa", "host", "exec"], ["a", "--", "b"])),
            (["host", "exec", "--json", "--"], (["host", "exec", "--json"], [])),
            (["exec", "--remote", "host", "--", "ls"], None),
            (["wp", "--", "host"], None),
            (["host", "logs", "--remote", "R"], None),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                head, tail = host_exec.split_passthrough(argv)
                if expected is None:
                    self.assertEqual((head, tail), (argv, None))
                else:
                    self.assertEqual((head, tail), expected)

    def test_classify_exit_status(self):
        self.assertEqual(host_exec.classify(0, 1, 300), (0, "ok"))
        self.assertEqual(host_exec.classify(3, 1, 300), (3, "host_exec_failed"))
        self.assertEqual(host_exec.classify(124, 1, 300), (124, "host_exec_failed"))
        self.assertEqual(host_exec.classify(124, 300, 300), (124, "host_exec_timeout"))
        self.assertEqual(host_exec.classify(137, 311, 300), (137, "host_exec_timeout"))
        self.assertEqual(host_exec.classify(137, 5, 300), (137, "host_exec_failed"))
        self.assertEqual(host_exec.classify(255, 1, 300), (255, "host_exec_transport_failed"))
        self.assertEqual(host_exec.classify(-9, 1, 300), (137, "host_exec_failed"))
        self.assertEqual(host_exec.classify(0, 1, 300, locally_killed=True),
                         (255, "host_exec_watchdog_expired"))

    def test_envelope_ok_only_on_exit_zero(self):
        request = _request(capture=True)
        failed = ExecOutcome(exit_code=3, code="host_exec_failed", duration_ms=5)
        self.assertEqual(failed.envelope(request)["ok"], False)
        self.assertEqual(failed.envelope(request)["exit_code"], 3)
        passed = ExecOutcome(exit_code=0, code="ok", duration_ms=5, stdout=b"hi")
        self.assertEqual(passed.envelope(request)["ok"], True)
        self.assertEqual(passed.envelope(request)["stdout"], "hi")


class BusyTests(unittest.TestCase):
    def test_busy_reasons(self):
        self.assertIsNone(host_exec.busy_reason({}, None))
        self.assertIsNone(host_exec.busy_reason({}, "succeeded"))
        for record, lifecycle in (({}, "running"), ({}, "queued"),
                                  ({"active_operation": {"action": "apply"}}, None),
                                  ({"recovery_uncertainty": {"x": 1}}, None),
                                  ({"image_activation": {"active": {"id": 1}}}, None)):
            with self.subTest(record=record, lifecycle=lifecycle):
                self.assertIsNotNone(host_exec.busy_reason(record, lifecycle))

    def test_held_effect_lock_reads_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            def repository():
                return RecoveryRepository(state_path=Path(directory, "hosts.json"),
                                          lock_dir=Path(directory, "locks"))
            key = "myvps/example-site/production"
            self.assertTrue(host_exec.effect_lock_free(key, repository()))
            with repository().effect_lock(key):
                self.assertFalse(host_exec.effect_lock_free(key, repository()))
            self.assertTrue(host_exec.effect_lock_free(key, repository()))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _child(self, exit_code=0, mode="count", script=CHILD):
        path = self.out / "child.py"
        path.write_text(script)
        return [sys.executable, str(path), str(self.out), str(exit_code), mode]

    def test_capture_is_bounded_and_flagged(self):
        with patch.object(host_exec, "CAPTURE_LIMIT", 10):
            outcome = host_exec.run(self._child(mode="flood"), _request(capture=True),
                                    stdin=subprocess.DEVNULL)
        self.assertEqual((len(outcome.stdout), outcome.stdout_truncated), (10, True))
        self.assertTrue(outcome.stderr_truncated)
        self.assertEqual(outcome.exit_code, 0)

    def test_local_watchdog_kills_and_reports_unconfirmed_deadline(self):
        sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
        with patch.object(host_exec, "KILL_AFTER_SECONDS", 0), \
                patch.object(host_exec, "LOCAL_GRACE_SECONDS", 0):
            outcome = host_exec.run(sleeper, _request(timeout_seconds=1, capture=True),
                                    stdin=subprocess.DEVNULL)
        self.assertEqual((outcome.exit_code, outcome.code), (255, "host_exec_watchdog_expired"))
        self.assertFalse(outcome.ok)

    def test_ssh_that_cannot_start_is_a_transport_failure(self):
        outcome = host_exec.run([str(self.out / "no-such-ssh")], _request(capture=True),
                                stdin=subprocess.DEVNULL)
        self.assertEqual((outcome.exit_code, outcome.code), (255, "host_exec_transport_failed"))
        self.assertIn(b"could not start ssh", outcome.stderr)

    def test_audit_log_closes_once_and_refuses_writes_after_close(self):
        path = self.out / "audit" / "audit.jsonl"
        outcome = ExecOutcome(exit_code=0, code="ok", duration_ms=1)
        with host_exec.AuditLog(path) as audit:
            audit.write(_request(), outcome)
        audit.close()
        with self.assertRaises(OSError):
            audit.write(_request(), outcome)
        self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_tty_or_closed_stdin_becomes_devnull(self):
        class Closed:
            def fileno(self):
                raise ValueError("closed")
        self.assertEqual(host_exec.stdin_source(Closed()), subprocess.DEVNULL)
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        os.close(write_fd)
        with os.fdopen(os.dup(read_fd), "rb") as pipe:
            self.assertEqual(host_exec.stdin_source(pipe), pipe.fileno())


def _exec_args(**overrides) -> SimpleNamespace:
    values = dict(action="exec", remote="myvps", target="example-site/production", name=None,
                  service="web", timeout=None, json=True, project_dir=None, environment=None,
                  exec_argv=["sh", "-c", "head -c 8 >/dev/null; echo ok"])
    values.update(overrides)
    return SimpleNamespace(**values)


class CommandTests(unittest.TestCase):
    """cmd_host_exec end to end, with `ssh` replaced by a local child."""

    CANARY = b"canary-7f3a9c1e5b2d4086-stdin-only"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name, "home")
        self.runtime = self.home / "runtime"
        self.runtime.mkdir(parents=True)
        self.checkout = Path(self._tmp.name, "checkout")
        self.checkout.mkdir()
        (self.checkout / "sandbox.hosting.yml").write_text(PLAIN)
        (self.checkout / "compose.yml").write_text("services: {}\n")
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)
        self.out = Path(self._outside.name)
        (self.out / "child.py").write_text(CHILD)
        self.ssh_calls: list[tuple[dict, str, bool]] = []
        self.child_exit = 0
        self.child_mode = "count"
        self.state = {"hosts": {"myvps/example-site/production": {"commit": "a" * 40}}}
        self.services = "web\nworker\n"
        patches = [
            patch("sandbox.core._paths.RUNTIME_DIR", self.runtime),
            patch("sandbox.core._remote.get_remote",
                  return_value={"provisioned": True, "ssh": "deploy@203.0.113.10"}),
            patch("sandbox.core._remote.resolve_sandbox_home", return_value="/srv/sandbox"),
            patch("sandbox.core._hosting.load_host_state", side_effect=lambda: self.state),
            patch("sandbox.hosting.stack_registry.read",
                  return_value={"version": 1, "stacks": {}}),
            patch.object(hosting_cmd, "_remote_checked", side_effect=lambda *a, **k: self.services),
            patch.object(host_exec, "effect_lock_free", return_value=True),
            patch("sandbox.core._remote.ssh_command_args", side_effect=self._fake_ssh),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def _fake_ssh(self, entry, command, *, multiplex=True):
        self.ssh_calls.append((entry, command, multiplex))
        return [sys.executable, str(self.out / "child.py"), str(self.out),
                str(self.child_exit), self.child_mode]

    def _run(self, args, stdin: bytes | None = None) -> tuple[int, str, str]:
        if stdin is None:
            source = subprocess.DEVNULL
        else:
            read_fd, write_fd = os.pipe()
            os.write(write_fd, stdin)
            os.close(write_fd)
            self.addCleanup(os.close, read_fd)
            source = read_fd
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch.object(host_exec, "stdin_source", return_value=source), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                hosting_cmd.cmd_host({}, SimpleNamespace(project_dir=str(self.checkout), **{
                    k: v for k, v in vars(args).items() if k != "project_dir"}))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()

    def _tree_contains(self, root: Path, needle: bytes) -> list[str]:
        hits = []
        for path in root.rglob("*"):
            if path.is_file() and needle in path.read_bytes():
                hits.append(str(path))
        return hits

    def test_stdin_canary_reaches_only_the_child(self):
        code, out, err = self._run(_exec_args(), stdin=self.CANARY)
        self.assertEqual(code, 0, err)
        self.assertEqual((self.out / "stdin.bin").read_bytes(), self.CANARY)
        canary = self.CANARY.decode()
        payload = json.loads(out)
        self.assertEqual((payload["ok"], payload["exit_code"]), (True, 0))
        self.assertEqual(payload["stdout"], f"received {len(self.CANARY)} bytes\n")
        self.assertNotIn(canary, out)
        self.assertNotIn(canary, err)
        entry, command, multiplex = self.ssh_calls[0]
        self.assertNotIn(canary, command)
        self.assertNotIn(canary, json.dumps(entry))
        self.assertFalse(multiplex)
        self.assertNotIn(canary, (self.out / "environ.json").read_text())
        self.assertNotIn(canary, json.dumps(dict(os.environ)))
        self.assertEqual(self._tree_contains(self.home, self.CANARY), [])
        self.assertEqual(self._tree_contains(self.checkout, self.CANARY), [])
        audit = (self.runtime / "host-exec" / "audit.jsonl").read_text().splitlines()
        self.assertEqual(len(audit), 1)
        record = json.loads(audit[0])
        self.assertEqual(sorted(record), ["argv", "at", "code", "duration_ms", "exit_code",
                                          "remote", "service", "target"])
        self.assertEqual(record["argv"], ["sh", "-c", "head -c 8 >/dev/null; echo ok"])
        self.assertEqual((record["target"], record["service"], record["exit_code"]),
                         ("example-site/production", "web", 0))
        self.assertEqual(os.stat(self.runtime / "host-exec" / "audit.jsonl").st_mode & 0o777, 0o600)

    def test_child_printing_stdin_is_the_only_place_it_appears(self):
        self.child_mode = "echo"
        code, out, _err = self._run(_exec_args(), stdin=self.CANARY)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["stdout"], self.CANARY.decode())
        self.assertEqual(self._tree_contains(self.home, self.CANARY), [])

    def test_child_failure_propagates_exit_code(self):
        self.child_exit = 3
        code, out, _err = self._run(_exec_args())
        self.assertEqual(code, 3)
        payload = json.loads(out)
        self.assertEqual((payload["ok"], payload["exit_code"], payload["code"]),
                         (False, 3, "host_exec_failed"))

    def test_plain_mode_failure_and_transport_exit_nonzero(self):
        self.child_exit = 255
        code, out, err = self._run(_exec_args(json=False))
        self.assertEqual(code, 255)
        self.assertIn("host_exec_transport_failed", err)
        record = json.loads((self.runtime / "host-exec" / "audit.jsonl").read_text())
        self.assertEqual(record["code"], "host_exec_transport_failed")

    def test_ssh_launch_failure_is_audited_and_exits_255(self):
        with patch("sandbox.core._remote.ssh_command_args",
                   return_value=[str(self.out / "no-such-ssh")]):
            code, out, _err = self._run(_exec_args())
        self.assertEqual(code, 255)
        self.assertEqual(json.loads(out)["code"], "host_exec_transport_failed")
        record = json.loads((self.runtime / "host-exec" / "audit.jsonl").read_text())
        self.assertEqual((record["exit_code"], record["code"]), (255, "host_exec_transport_failed"))

    def test_unknown_service_is_refused_before_running(self):
        code, out, _err = self._run(_exec_args(service="db"))
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["code"], "host_exec_unknown_service")
        self.assertIn("web, worker", payload["error"])
        self.assertEqual(self.ssh_calls, [])
        self.assertFalse((self.runtime / "host-exec" / "audit.jsonl").exists())

    def test_unknown_target_is_refused(self):
        code, out, _err = self._run(_exec_args(target="other-site/production"))
        self.assertEqual((code, json.loads(out)["code"]), (1, "host_target_unknown"))
        self.assertEqual(self.ssh_calls, [])

    def test_busy_target_is_refused_not_waited_for(self):
        self.state["hosts"]["myvps/example-site/production"]["active_operation"] = {"action": "apply"}
        code, out, _err = self._run(_exec_args())
        self.assertEqual((code, json.loads(out)["code"]), (1, "host_target_busy"))
        self.state["hosts"]["myvps/example-site/production"].pop("active_operation")
        with patch.object(host_exec, "effect_lock_free", return_value=False):
            code, out, _err = self._run(_exec_args())
        self.assertEqual((code, json.loads(out)["code"]), (1, "host_target_busy"))
        self.assertEqual(self.ssh_calls, [])

    def test_stack_apply_in_flight_is_busy(self):
        stack_dir = self.runtime / "stacks" / "demo"
        stack_dir.mkdir(parents=True)
        (stack_dir / "sandbox.hosting.yml").write_text("rendered\n")
        registry = {"version": 1, "stacks": {"myvps/demo": {
            "remote": "myvps", "slug": "demo", "project": "example-site",
            "environment": "production", "manifest": str(stack_dir / "sandbox.hosting.yml"),
            "last_apply": {"job_id": "job-1"}}}}
        with patch("sandbox.hosting.stack_registry.read", return_value=registry), \
                patch("sandbox.commands.host_stack.job_view", return_value={"lifecycle": "running"}):
            code, out, _err = self._run(_exec_args(name="demo", target=None))
        self.assertEqual((code, json.loads(out)["code"]), (1, "host_target_busy"))

    def test_usage_errors_exit_two(self):
        for overrides in (dict(exec_argv=[]), dict(exec_argv=None), dict(timeout=0),
                          dict(timeout=3601), dict(service=None)):
            with self.subTest(overrides=overrides):
                code, out, _err = self._run(_exec_args(**overrides))
                self.assertEqual((code, json.loads(out)["code"]), (2, "host_exec_usage"))
        self.assertEqual(self.ssh_calls, [])

    def test_unprovisioned_and_unknown_remote(self):
        with patch("sandbox.core._remote.get_remote", return_value=None):
            code, out, _err = self._run(_exec_args())
        self.assertEqual((code, json.loads(out)["code"]), (1, "remote_unknown"))
        with patch("sandbox.core._remote.get_remote", return_value={"provisioned": False}):
            code, out, _err = self._run(_exec_args())
        self.assertEqual((code, json.loads(out)["code"]), (1, "remote_not_provisioned"))

    def test_other_host_actions_reject_passthrough_argv(self):
        buffer = io.StringIO()
        with redirect_stderr(buffer), self.assertRaises(SystemExit) as caught:
            hosting_cmd.cmd_host({}, SimpleNamespace(action="logs", exec_argv=["x"]))
        self.assertEqual(caught.exception.code, 1)


class CliParseTests(unittest.TestCase):
    """The real parser: options before `--` are Sandbox's, the rest is argv."""

    def _main(self, *argv: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "SANDBOX_HOME": home, "PATH": os.environ.get("PATH", ""),
                   "PYTHONPATH": str(REPO)}
            code = ("import sys; sys.argv = ['sb', *sys.argv[1:]]\n"
                    "from sandbox.cli import main\nmain()\n")
            return subprocess.run([sys.executable, "-c", code, *argv], cwd=home, env=env,
                                  capture_output=True, text=True, timeout=120, check=False)

    def test_exec_argv_after_double_dash_reaches_handler(self):
        result = self._main("host", "exec", "--remote", "nosuch", "--target", "a/b",
                            "--service", "web", "--json", "--", "sh", "-c", "--remote x")
        line = [item for item in result.stdout.splitlines() if item.startswith("{")][-1]
        self.assertEqual(json.loads(line)["code"], "remote_unknown", result.stderr)
        self.assertEqual(result.returncode, 1)

    def test_other_host_actions_still_reject_trailing_argv(self):
        result = self._main("host", "logs", "--remote", "nosuch", "--", "extra")
        self.assertEqual(result.returncode, 2, result.stderr)


if __name__ == "__main__":
    unittest.main()
