"""Pure tests for the doctor's read-only MCP import probe."""

import subprocess
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
