"""Contracts for bounded WP-CLI against an already deployed remote instance."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sandbox.commands import wp as command
from sandbox.core import _remote


class TestRemoteWpCliTransport(unittest.TestCase):
    @patch("sandbox.core._remote._remote_control_request")
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_uses_authenticated_control_endpoint_with_exact_preflight(self, _revision, request):
        request.return_value = {
            "ok": True,
            "wp_cli_schema": 1,
            "transport": "control",
            "status": "complete",
            "ownership": "proven",
            "runtime_revision": "a" * 24,
            "instance": "project-default",
            "stdout": "https://example.test\n",
            "stderr": "",
            "exit_code": 0,
        }
        remote = {
            "control_url": "https://control.example.test",
            "bearer_token": "secret-token",
            "mcp_service": {"ownership_marker": "b" * 24},
        }

        result = _remote.remote_wp_cli(
            remote, project_slug="project", label="default",
            argv=["option", "get", "siteurl"], timeout=17,
        )

        self.assertEqual(result["stdout"], "https://example.test\n")
        request.assert_called_once_with(
            remote, "/wp-cli", timeout=27,
            payload={
                "schema_version": 1,
                "action": "wp_cli",
                "project_slug": "project",
                "label": "default",
                "argv": ["option", "get", "siteurl"],
                "timeout_seconds": 17,
                "expected_runtime_revision": "a" * 24,
                "expected_ownership_marker": "b" * 24,
            },
        )

    @patch("sandbox.core._remote._remote_control_request")
    def test_refuses_missing_ownership_evidence_before_control_dispatch(self, request):
        with self.assertRaisesRegex(
            _remote.RemoteWpRefusalError, "ownership evidence",
        ) as raised:
            _remote.remote_wp_cli(
                {"control_url": "https://control.example.test", "bearer_token": "token"},
                project_slug="project", label="default", argv=["core", "version"], timeout=5,
            )
        self.assertEqual(raised.exception.code, "remote_service_ownership_unavailable")
        request.assert_not_called()

    @patch("sandbox.core._remote._remote_control_request")
    def test_refuses_non_argv_input_before_control_dispatch(self, request):
        with self.assertRaisesRegex(ValueError, "explicit argv"):
            _remote.remote_wp_cli(
                {"mcp_service": {"ownership_marker": "b" * 24}},
                project_slug="project", label="default", argv="core version", timeout=5,
            )
        request.assert_not_called()

    @patch("sandbox.core._remote._remote_control_request")
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_revision_or_ownership_mismatch_is_not_accepted(self, _revision, request):
        remote = {"mcp_service": {"ownership_marker": "b" * 24}}
        for response, message in (
            ({"ok": False, "wp_cli_schema": 1, "transport": "control",
              "error": {"code": "runtime_revision_mismatch"}}, "revision"),
            ({"ok": False, "wp_cli_schema": 1, "transport": "control",
              "error": {"code": "remote_service_ownership_unknown"}}, "ownership"),
            ({"ok": False, "wp_cli_schema": 1, "transport": "control",
              "error": {"code": "host_file_staging_unsupported"}}, "host-file"),
        ):
            with self.subTest(message=message):
                request.return_value = response
                with self.assertRaisesRegex(RuntimeError, message):
                    _remote.remote_wp_cli(
                        remote, project_slug="project", label="default",
                        argv=["core", "version"], timeout=5,
                    )

    @patch("sandbox.core._remote._remote_control_request", side_effect=RuntimeError("unreachable"))
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_transport_timeout_is_unknown_and_never_retried(self, _revision, request):
        remote = {"mcp_service": {"ownership_marker": "b" * 24}}
        with self.assertRaisesRegex(
            _remote.RemoteWpCompletionUnknown, "completion is unknown",
        ) as raised:
            _remote.remote_wp_cli(
                remote, project_slug="project", label="default",
                argv=["option", "update", "flag", "1"], timeout=5,
            )
        self.assertEqual(raised.exception.code, "remote_wp_transport_unknown")
        request.assert_called_once()

    @patch("sandbox.core._remote._remote_control_request")
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_oversized_output_is_unknown_and_never_retried(self, _revision, request):
        request.return_value = {
            "ok": False, "wp_cli_schema": 1, "transport": "control", "status": "unknown",
            "error": {"code": "output_too_large"},
        }
        with self.assertRaisesRegex(RuntimeError, "completion is unknown"):
            _remote.remote_wp_cli(
                {"mcp_service": {"ownership_marker": "b" * 24}},
                project_slug="project", label="default", argv=["post", "list"], timeout=5,
            )
        request.assert_called_once()

    @patch("sandbox.core._remote.ssh_run")
    @patch("sandbox.core._remote._remote_control_request")
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_never_falls_back_to_ssh(self, _revision, request, ssh_run):
        request.return_value = {
            "ok": True, "wp_cli_schema": 1, "transport": "control",
            "status": "complete", "ownership": "proven",
            "runtime_revision": "a" * 24, "instance": "project-default",
            "stdout": "", "stderr": "", "exit_code": 0,
        }
        _remote.remote_wp_cli(
            {"mcp_service": {"ownership_marker": "b" * 24}},
            project_slug="project", label="default", argv=["core", "version"], timeout=5,
        )
        request.assert_called_once()
        ssh_run.assert_not_called()

    @patch("sandbox.core._remote._remote_control_request")
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_rejects_every_malformed_status_exit_mapping(self, _revision, request):
        remote = {"mcp_service": {"ownership_marker": "b" * 24}}
        for status, exit_code in (
            ("complete", 7), ("failed", 0), ("unknown", 0), ("failed", 124),
            ("unknown", 125),
        ):
            with self.subTest(status=status, exit_code=exit_code):
                request.return_value = {
                    "ok": True, "wp_cli_schema": 1, "transport": "control",
                    "status": status, "ownership": "proven",
                    "runtime_revision": "a" * 24, "instance": "project-default",
                    "stdout": "partial out\n", "stderr": "partial err\n",
                    "exit_code": exit_code,
                }
                with self.assertRaisesRegex(
                    _remote.RemoteWpCompletionUnknown, "completion",
                ) as raised:
                    _remote.remote_wp_cli(
                        remote, project_slug="project", label="default",
                        argv=["core", "version"], timeout=5,
                    )
                self.assertEqual(raised.exception.code, "remote_wp_completion_invalid")

    @patch("sandbox.core._remote._remote_control_request")
    @patch("sandbox.core._remote._remote_mcp_runtime_revision", return_value="a" * 24)
    def test_accepts_typed_output_overflow_only_as_nonzero_unknown(self, _revision, request):
        request.return_value = {
            "ok": True, "wp_cli_schema": 1, "transport": "control",
            "status": "unknown", "ownership": "proven",
            "runtime_revision": "a" * 24, "instance": "project-default",
            "stdout": "bounded partial\n", "stderr": "completion unknown\n",
            "exit_code": 125, "error": {"code": "wp_cli_output_overflow"},
        }
        result = _remote.remote_wp_cli(
            {"mcp_service": {"ownership_marker": "b" * 24}},
            project_slug="project", label="default", argv=["post", "list"], timeout=5,
        )
        self.assertEqual((result["status"], result["exit_code"]), ("unknown", 125))


class TestRemoteWpCliCommand(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "remote": "remote-a", "local": False, "project_dir": "/project",
            "label": "default", "instance": None, "resolved_instance": None,
            "passthrough": ["--", "option", "get", "siteurl"],
            "run_async": False, "timeout": 9, "allow_missing": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    @patch("sandbox.commands.wp._remote.remote_wp_cli")
    @patch("sandbox.commands.wp._remote.get_remote")
    @patch("sandbox.commands.wp.preflight_project_capability", return_value=None)
    @patch("sandbox.commands.wp._core")
    def test_targets_existing_deploy_and_preserves_raw_streams(
        self, core, _preflight, get_remote, remote_wp_cli,
    ):
        core.return_value.load_project_config.return_value = {
            "root": "/project", "kind": "wordpress", "slug": "project",
        }
        get_remote.return_value = {"provisioned": True}
        remote_wp_cli.return_value = {
            "ok": True, "status": "complete", "stdout": "raw output\n",
            "stderr": "raw warning\n", "exit_code": 0,
        }
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            command.cmd_wp({}, self._args())
        self.assertEqual(out.getvalue(), "raw output\n")
        self.assertEqual(err.getvalue(), "raw warning\n")
        _preflight.assert_called_once_with({}, "/project", "wordpress.cli", label="default")
        remote_wp_cli.assert_called_once_with(
            get_remote.return_value, project_slug="project", label="default",
            argv=["option", "get", "siteurl"], timeout=9, allow_missing=False,
        )

    @patch("sandbox.commands.wp._remote.remote_wp_cli")
    @patch("sandbox.commands.wp._remote.get_remote")
    @patch("sandbox.commands.wp.preflight_project_capability", return_value=None)
    @patch("sandbox.commands.wp._core")
    def test_generic_compose_refuses_before_remote_dispatch(
        self, core, _preflight, get_remote, remote_wp_cli,
    ):
        core.return_value.load_project_config.return_value = {
            "root": "/project", "kind": "compose", "slug": "project",
        }
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            command.cmd_wp({}, self._args())
        get_remote.assert_not_called()
        remote_wp_cli.assert_not_called()

    @patch("sandbox.commands.wp._remote.remote_wp_cli")
    @patch("sandbox.commands.wp._remote.get_remote")
    @patch("sandbox.commands.wp.preflight_project_capability", return_value=None)
    @patch("sandbox.commands.wp._core")
    def test_remote_async_and_instance_selectors_refuse_before_dispatch(
        self, core, _preflight, get_remote, remote_wp_cli,
    ):
        core.return_value.load_project_config.return_value = {
            "root": "/project", "kind": "wordpress", "slug": "project",
        }
        for args in (self._args(run_async=True), self._args(instance="other")):
            with self.subTest(args=args), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                command.cmd_wp({}, args)
        get_remote.assert_not_called()
        remote_wp_cli.assert_not_called()

    @patch("sandbox.commands.wp._remote.remote_wp_cli")
    @patch("sandbox.commands.wp._remote.get_remote")
    @patch("sandbox.commands.wp.preflight_project_capability", return_value=None)
    @patch("sandbox.commands.wp._core")
    def test_unknown_and_unprovisioned_remote_refuse_before_control_dispatch(
        self, core, _preflight, get_remote, remote_wp_cli,
    ):
        core.return_value.load_project_config.return_value = {
            "root": "/project", "kind": "wordpress", "slug": "project",
        }
        for value in (None, {"provisioned": False}):
            with self.subTest(remote=value):
                get_remote.return_value = value
                with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                    command.cmd_wp({}, self._args())
        remote_wp_cli.assert_not_called()

    @patch("sandbox.commands.wp.wpcli")
    @patch("sandbox.commands.wp._remote.remote_wp_cli")
    @patch("sandbox.commands.wp._is_herd_instance", return_value=False)
    @patch("sandbox.commands.wp.preflight_instance_capability", return_value=None)
    def test_local_wp_behavior_is_unchanged(
        self, _preflight, _is_herd, remote_wp_cli, wpcli,
    ):
        wpcli.return_value = SimpleNamespace(returncode=0, stdout="6.9.1\n", stderr="")
        args = self._args(remote=None, local=True, resolved_instance="fixture",
                          passthrough=["core", "version"])
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            command.cmd_wp({}, args)
        wpcli.assert_called_once()
        remote_wp_cli.assert_not_called()

    def test_local_host_file_staging_behavior_remains_available(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            package = project / "plugin.zip"
            package.write_bytes(b"local-package")
            with patch.object(command, "RUNTIME_DIR", root / "runtime"), \
                    patch.object(command, "_is_herd_instance", return_value=False):
                rewritten, staged = command._stage_host_package_paths(
                    ["plugin", "install", "plugin.zip"], "local-instance", project,
                )
            try:
                self.assertTrue(rewritten[2].startswith("/sandbox-dl-cache/"))
                self.assertEqual(len(staged), 1)
                self.assertEqual(staged[0].read_bytes(), b"local-package")
            finally:
                for path in staged:
                    path.unlink(missing_ok=True)

    @patch("sandbox.commands.wp._remote.remote_wp_cli")
    @patch("sandbox.commands.wp._remote.get_remote")
    @patch("sandbox.commands.wp.preflight_project_capability", return_value=None)
    @patch("sandbox.commands.wp._core")
    def test_unknown_remote_result_can_never_exit_zero(
        self, core, _preflight, get_remote, remote_wp_cli,
    ):
        core.return_value.load_project_config.return_value = {
            "root": "/project", "kind": "wordpress", "slug": "project",
        }
        get_remote.return_value = {"provisioned": True}
        remote_wp_cli.return_value = {
            "ok": True, "status": "unknown", "stdout": "partial\n",
            "stderr": "completion unknown\n", "exit_code": 0,
        }
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()), \
                self.assertRaises(SystemExit) as raised:
            command.cmd_wp({}, self._args())
        self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
