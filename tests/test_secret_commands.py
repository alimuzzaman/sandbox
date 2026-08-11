from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
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
                    "mcpModes": ["keys", "metadata", "validate", "masked", "use"],
                }}},
            }))
            source = root / ".env.fixture"
            source.write_text(f"API_TOKEN={fixture_value}\nOTHER_NAME=identifier\n")
            source.chmod(0o600)
            environment = {
                **os.environ,
                "SANDBOX_HOME": str(home),
                "SANDBOX_PROJECT_ROOTS": str(root.parent),
            }

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
            updated = invoke("set", "--source", "fixture", "API_TOKEN", "--stdin", "--json",
                             input_text=replacement + "\n")
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(json.loads(updated.stdout)["action"], "updated")
            self.assertIn(replacement, source.read_text())
            refused = invoke("reveal", "--source", "fixture", "--key", "API_TOKEN")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("tty_required", refused.stderr)
            self.assertFalse((home / "runtime/compose").exists())


if __name__ == "__main__":
    unittest.main()
