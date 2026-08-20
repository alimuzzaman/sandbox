"""Pure tests for the doctor's read-only MCP import probe."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sandbox.commands.lifecycle as lifecycle


class TestMcpDoctorProbe(unittest.TestCase):
    def test_missing_venv_is_actionable(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            lifecycle, "MCP_VENV", Path(d)
        ):
            ok, detail = lifecycle._probe_mcp_server()
        self.assertFalse(ok)
        self.assertEqual(detail, "MCP venv is missing")

    def test_import_failure_returns_bounded_stderr(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            lifecycle, "MCP_VENV", Path(d)
        ), patch.object(lifecycle, "MCP_DIR", Path(d)), patch.object(
            lifecycle.subprocess, "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="x" * 1000
            ),
        ):
            (Path(d) / "bin").mkdir()
            (Path(d) / "bin" / "python").touch()
            ok, detail = lifecycle._probe_mcp_server()
        self.assertFalse(ok)
        self.assertEqual(len(detail), 500)

    def test_import_success_is_clean(self):
        with tempfile.TemporaryDirectory() as d, patch.object(
            lifecycle, "MCP_VENV", Path(d)
        ), patch.object(lifecycle, "MCP_DIR", Path(d)), patch.object(
            lifecycle.subprocess, "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            ),
        ):
            (Path(d) / "bin").mkdir()
            (Path(d) / "bin" / "python").touch()
            ok, detail = lifecycle._probe_mcp_server()
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_project_plugin_check_declaration_uses_resolved_config(self):
        with patch.object(lifecycle, "_core") as core:
            core.return_value.load_project_config.return_value = {
                "plugins_resolved": {"plugin-check": {"active": True}}
            }
            self.assertTrue(lifecycle._project_declares_plugin_check("/tmp/project"))

    def test_project_without_plugin_check_is_not_probed(self):
        with patch.object(lifecycle, "_core") as core:
            core.return_value.load_project_config.return_value = {"plugins_resolved": {}}
            self.assertFalse(lifecycle._project_declares_plugin_check("/tmp/project"))

    def test_storage_pressure_rows_are_preserved_without_running_subprocesses(self):
        rows = [
            {"label": "local", "ok": True, "hint": ""},
            {"label": "stale remote", "ok": False,
             "hint": "monitor record is stale; refresh with sb resources monitor --json"},
            {"label": "missing remote", "ok": False,
             "hint": "no monitor run recorded; refresh with sb resources monitor --json"},
            {"label": "malformed remote", "ok": False,
             "hint": "monitor record is invalid; refresh with sb resources monitor --json"},
        ]
        with patch("sandbox.resources.monitor.storage_doctor_checks",
                   return_value=rows), patch.object(
                       lifecycle.subprocess, "run",
                       side_effect=AssertionError("doctor storage checks must stay offline"),
                   ):
            checks = lifecycle._storage_pressure_doctor_checks()
        self.assertEqual(checks, [
            ("local", True, ""),
            ("stale remote", False,
             "monitor record is stale; refresh with sb resources monitor --json"),
            ("missing remote", False,
             "no monitor run recorded; refresh with sb resources monitor --json"),
            ("malformed remote", False,
             "monitor record is invalid; refresh with sb resources monitor --json"),
        ])

    def test_storage_pressure_missing_or_invalid_evidence_fails_closed(self):
        with patch("sandbox.resources.monitor.storage_doctor_checks",
                   side_effect=OSError("unavailable")):
            unavailable = lifecycle._storage_pressure_doctor_checks()
        self.assertEqual(unavailable[0][0], "storage monitor evidence available")
        self.assertFalse(unavailable[0][1])
        self.assertIn("could not be read", unavailable[0][2])

        with patch("sandbox.resources.monitor.storage_doctor_checks",
                   return_value=[{"label": "local", "ok": False}]):
            malformed = lifecycle._storage_pressure_doctor_checks()
        self.assertEqual(malformed[0][0], "storage monitor evidence available")
        self.assertFalse(malformed[0][1])
        self.assertIn("is invalid", malformed[0][2])

        with patch("sandbox.resources.monitor.storage_doctor_checks",
                   return_value=[]):
            empty = lifecycle._storage_pressure_doctor_checks()
        self.assertEqual(empty[0][0], "storage monitor evidence available")
        self.assertFalse(empty[0][1])
        self.assertIn("is invalid", empty[0][2])

    def test_doctor_emits_one_storage_section_check_per_evidence_row(self):
        rows = [
            {"label": "local", "ok": True, "hint": ""},
            {"label": "stale remote", "ok": False,
             "hint": "monitor record is stale; refresh with sb resources monitor --json"},
            {"label": "missing remote", "ok": False,
             "hint": "no monitor run recorded; refresh with sb resources monitor --json"},
        ]
        process = SimpleNamespace(
            returncode=0,
            stdout="\n".join(json.dumps(row) for row in (
                {"Service": "wp", "State": "running"},
                {"Service": "db", "State": "running"},
                {"Service": "mailpit", "State": "running"},
            )),
        )
        inst_cfg = {"admin": {}, "wordpress_port": 8188}
        with tempfile.TemporaryDirectory() as directory:
            venv = Path(directory) / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").touch()
            output = io.StringIO()
            with patch.object(lifecycle, "preflight_instance_capability",
                              return_value=None), \
                    patch.object(lifecycle, "resolve_instances",
                                 return_value={"fixture": inst_cfg}), \
                    patch.object(lifecycle, "_core", return_value=SimpleNamespace(
                        registry_find_instance=lambda _name: None)), \
                    patch.object(lifecycle, "php_extension_status", return_value=None), \
                    patch.object(lifecycle, "compose", return_value=process), \
                    patch.object(lifecycle, "wpcli",
                                 return_value=SimpleNamespace(returncode=0)), \
                    patch.object(lifecycle, "_probe_mcp_server", return_value=(True, "")), \
                    patch.object(lifecycle, "MCP_VENV", venv), \
                    patch.object(lifecycle, "focus_file",
                                 return_value=Path(directory) / "focus"), \
                    patch.object(lifecycle, "plugins_dir",
                                 return_value=Path(directory) / "plugins"), \
                    patch.object(lifecycle, "_local_yaml", return_value={
                        "defaults": {"github_org": "WPDevelopers"}}), \
                    patch.object(lifecycle, "SECRETS_ENV",
                                 Path(directory) / "missing-env"), \
                    patch("sandbox.core._domains.proxy_health_checks", return_value=[]), \
                    patch("sandbox.core._remote.list_remotes", return_value={}), \
                    patch("sandbox.resources.monitor.storage_doctor_checks",
                          return_value=rows), \
                    contextlib.redirect_stdout(output), \
                    self.assertRaises(SystemExit) as raised:
                lifecycle.cmd_doctor({"instances": {"fixture": {}}}, SimpleNamespace(
                    resolved_instance="fixture", json=True))
        self.assertEqual(raised.exception.code, 1)
        payload = json.loads(output.getvalue())
        storage_rows = [row for row in payload["checks"]
                        if row["section"] == "Storage pressure"]
        self.assertEqual(storage_rows, [
            {"section": "Storage pressure", "label": "local", "ok": True},
            {"section": "Storage pressure", "label": "stale remote", "ok": False,
             "hint": "monitor record is stale; refresh with sb resources monitor --json"},
            {"section": "Storage pressure", "label": "missing remote", "ok": False,
             "hint": "no monitor run recorded; refresh with sb resources monitor --json"},
        ])


if __name__ == "__main__":
    unittest.main()
