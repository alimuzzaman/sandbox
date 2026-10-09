"""The shell bootstrap finds the repo-local venv from any working directory."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.subprocess_support import run_test_process

REPO = Path(__file__).resolve().parent.parent

FAKE_PYTHON = """#!/bin/sh
if [ "$1" = "-c" ]; then
    case "$2" in
        *version_info\\[0\\]*) echo 3 ;;
        *) echo 12 ;;
    esac
    exit 0
fi
echo "venv-python $*"
"""


class BootstrapVenvPathTests(unittest.TestCase):
    def test_venv_is_resolved_against_script_directory(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as cwd:
            entry = Path(repo) / "sb"
            shutil.copyfile(REPO / "sb", entry)
            entry.chmod(0o755)
            python = Path(repo) / ".cli-venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text(FAKE_PYTHON)
            python.chmod(0o755)
            empty_bin = Path(cwd) / "bin"
            empty_bin.mkdir()
            # An old system python3 on PATH, as on stock macOS.
            old = empty_bin / "python3"
            old.write_text(FAKE_PYTHON.replace("echo 12", "echo 9"))
            old.chmod(0o755)
            for tool in ("sh", "dirname", "pwd", "cat"):
                found = shutil.which(tool, path="/usr/bin:/bin")
                if found:
                    os.symlink(found, empty_bin / tool)
            result = run_test_process(
                [str(entry), "--version"], cwd=cwd,
                env={"PATH": str(empty_bin)},
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("venv-python", result.stdout)
        self.assertIn("--version", result.stdout)


if __name__ == "__main__":
    unittest.main()
