"""End-to-end CLI tests for the per-project resolution gate (spec 001).

These run the real `sb` entry as a subprocess (no Docker — the gate + registry
read happen before any container work), so they exercise the actual bootstrap,
package import, registry dispatch, and the no-`main` resolution behavior.
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SB = ROOT / "sb"


def run_sb(*args, cwd="/tmp"):
    return subprocess.run(
        [str(SB), *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "SANDBOX_INSTANCE": ""}, timeout=90)


class TestResolutionGate(unittest.TestCase):
    def test_instance_scoped_command_errors_outside_project(self):
        # `status` is instance-scoped; from a non-registered dir it must abort
        # with guidance and a non-zero exit — never silently target `main`.
        r = run_sb("status")
        self.assertNotEqual(r.returncode, 0)
        out = (r.stderr + r.stdout).lower()
        self.assertIn("no sandbox instance", out)
        self.assertNotIn("instance: main", out)

    def test_registry_wide_command_runs_anywhere(self):
        # `instances` is registry-wide → works from any directory.
        r = run_sb("instances")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unknown_instance_is_rejected(self):
        r = run_sb("status", "--instance", "definitely-not-a-real-instance")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unknown instance", (r.stderr + r.stdout).lower())

    def test_help_lists_selftest(self):
        r = run_sb("--help")
        self.assertIn("selftest", r.stdout + r.stderr)

    def test_no_main_in_help_command_list(self):
        # The phantom `main` instance is gone; it must not appear as guidance.
        r = run_sb("instances")
        self.assertNotIn(" main ", (r.stdout + r.stderr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
