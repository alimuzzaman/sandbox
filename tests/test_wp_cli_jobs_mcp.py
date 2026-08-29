"""MCP parity tests for Spec 004 cancellation refusal rendering."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


def _load_wp_tools():
    dependencies = types.ModuleType("dependencies")
    dependencies.ToolDependencies = object
    httpx = types.ModuleType("httpx")
    path = ROOT / "mcp" / "wp-server" / "tools" / "wp.py"
    spec = importlib.util.spec_from_file_location("spec004_wp_tools_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"dependencies": dependencies, "httpx": httpx}):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


WP_TOOLS = _load_wp_tools()


class TestWpCliJobKillMcp(unittest.TestCase):
    def _module(self, result):
        module = WP_TOOLS
        module._wp_cli_job_helpers = lambda: (
            None, lambda _instance, _job_id: dict(result), lambda _job_id: True,
        )
        module._require_project_capability = lambda *_args: None
        module._project_instance = lambda *_args: ("unit", None)
        return module

    def test_identity_mismatch_is_ok_false_with_bounded_reason(self):
        module = self._module({
            "job_id": "a" * 16,
            "status": "running",
            "killed": False,
            "error": "job process identity could not be verified",
        })

        result = module.wp_cli_job_kill("a" * 16, project_dir="/project")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["error"], "job process identity could not be verified")

    def test_only_terminal_noops_are_ok_true(self):
        for status in ("completed", "not_found"):
            with self.subTest(status=status):
                module = self._module({
                    "job_id": "b" * 16, "status": status, "killed": False,
                })
                result = module.wp_cli_job_kill("b" * 16, project_dir="/project")
                self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
