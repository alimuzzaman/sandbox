import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


class VisitWrapperTests(unittest.TestCase):
    def test_json_selector_after_url_is_consumed_by_sandbox_wrapper(self):
        from sandbox.commands import wp

        with patch.object(wp, "ensure_tools_venv", return_value=Path("/venv/bin/python")), \
                patch.object(wp, "TOOLS_DIR", Path("/sandbox/tools")), \
                patch.object(wp.os, "execv") as execv:
            wp.cmd_visit({}, SimpleNamespace(
                passthrough=["http://example.test", "--json", "--timeout", "15"],
            ))

        execv.assert_called_once_with(
            "/venv/bin/python",
            ["/venv/bin/python", "/sandbox/tools/visit/visit.py",
             "http://example.test", "--timeout", "15"],
        )


class ToolsVenvBrowserTests(unittest.TestCase):
    """The stamp proves the packages only; the browser cache can vanish."""

    def _venv(self, root):
        from sandbox.core import _config

        venv = root / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("")
        tools = root / "tools"
        (tools / "visit").mkdir(parents=True)
        (tools / "visit" / "requirements.txt").write_text("playwright==0\n")
        (venv / ".installed").write_text("playwright==0\n")
        return venv / "bin" / "python", [
            patch.object(_config, "TOOLS_VENV", venv),
            patch.object(_config, "TOOLS_DIR", tools),
            patch.object(_config, "_venv_paths_match", return_value=True),
        ]

    def test_matching_stamp_still_repairs_a_missing_browser(self):
        from sandbox.core import _config

        with tempfile.TemporaryDirectory() as raw:
            py, patches = self._venv(Path(raw))
            for active in patches:
                active.start()
                self.addCleanup(active.stop)
            with patch.object(_config.subprocess, "check_call") as check_call:
                self.assertEqual(_config.ensure_tools_venv(), py)
        check_call.assert_called_once_with(
            [str(py), "-m", "playwright", "install", "chromium"], stdout=2)

    def test_browser_install_failure_is_a_clean_error(self):
        from sandbox.core import _config

        with tempfile.TemporaryDirectory() as raw:
            _py, patches = self._venv(Path(raw))
            for active in patches:
                active.start()
                self.addCleanup(active.stop)
            with patch.object(_config.subprocess, "check_call",
                              side_effect=subprocess.CalledProcessError(1, "install")), \
                 patch.object(_config, "die", side_effect=SystemExit(1)) as die:
                with self.assertRaises(SystemExit):
                    _config.ensure_tools_venv()
        die.assert_called_once()

    def test_fresh_install_browser_failure_is_a_clean_error_and_leaves_no_stamp(self):
        from sandbox.core import _config

        with tempfile.TemporaryDirectory() as raw:
            py, patches = self._venv(Path(raw))
            stamp = py.parent.parent / ".installed"
            stamp.unlink()
            for active in patches:
                active.start()
                self.addCleanup(active.stop)

            def install(command, **_kwargs):
                if "playwright" in command:
                    raise subprocess.CalledProcessError(1, "install")
                return 0

            with patch.object(_config.subprocess, "run",
                              return_value=SimpleNamespace(returncode=0)), \
                 patch.object(_config.subprocess, "check_call", side_effect=install), \
                 patch.object(_config, "die", side_effect=SystemExit(1)) as die, \
                 patch.object(_config, "info"):
                with self.assertRaises(SystemExit):
                    _config.ensure_tools_venv()
            self.assertFalse(stamp.exists())
        die.assert_called_once()


if __name__ == "__main__":
    unittest.main()
