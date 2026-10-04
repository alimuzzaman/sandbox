from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock


class TestCommandDisplayRedaction(unittest.TestCase):
    def test_redacts_inline_secret_assignments(self):
        from sandbox.core import _ui

        rendered = _ui._display_command([
            "wp", "config", "create", "--dbpass=fixture-password",
            "API_TOKEN=fixture-token",
        ])

        self.assertEqual(
            rendered,
            "wp config create --dbpass=[REDACTED] API_TOKEN=[REDACTED]",
        )

    def test_redacts_separate_secret_option_values(self):
        from sandbox.core import _ui

        rendered = _ui._display_command([
            "wp", "core", "install", "--admin_password", "fixture-password",
            "--access-token", "fixture-token",
        ])

        self.assertEqual(
            rendered,
            "wp core install --admin_password [REDACTED] "
            "--access-token [REDACTED]",
        )

    def test_preserves_compose_project_and_boolean_password_flags(self):
        from sandbox.core import _ui

        rendered = _ui._display_command([
            "docker", "compose", "-p", "sandbox-project", "--no-password",
            "--file", "compose.yml",
        ])

        self.assertEqual(
            rendered,
            "docker compose -p sandbox-project --no-password --file compose.yml",
        )

    def test_run_redacts_only_the_progress_display(self):
        from sandbox.core import _ui

        output = io.StringIO()
        command = ["wp", "--password=fixture-password"]
        with mock.patch.object(_ui.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout="", stderr="")) as run, \
                mock.patch.object(_ui, "_WEB_STREAM", [False]), \
                redirect_stdout(output):
            _ui.run(command)

        self.assertNotIn("fixture-password", output.getvalue())
        self.assertIn("--password=[REDACTED]", output.getvalue())
        self.assertEqual(run.call_args.args[0], command)


if __name__ == "__main__":
    unittest.main()
