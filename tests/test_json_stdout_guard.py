"""24e67562 follow-up: ``ensure --json`` stdout carries only the JSON document.

Progress from Python and from child processes (compose, wp-cli) must go to
stderr, so a caller that parses the whole of stdout gets one JSON line.
"""
import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class JsonStdoutGuardTests(unittest.TestCase):
    def _run(self, json_mode: bool):
        program = textwrap.dedent(f"""
            import json, subprocess, sys, types
            sys.path.insert(0, {str(ROOT)!r})
            from sandbox.commands.instances_cmd import _json_progress_guard
            args = types.SimpleNamespace(json={json_mode!r})
            with _json_progress_guard(args):
                print("python progress")
                subprocess.run([sys.executable, "-c", "print('child progress')"], check=True)
            print(json.dumps({{"instance": "x", "status": "ready"}}))
        """)
        return subprocess.run([sys.executable, "-c", program], capture_output=True,
                              text=True, timeout=60, check=True)

    def test_json_mode_stdout_is_only_the_document(self):
        result = self._run(True)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertEqual(json.loads(lines[0])["status"], "ready")
        self.assertIn("python progress", result.stderr)
        self.assertIn("child progress", result.stderr)

    def test_human_mode_keeps_progress_on_stdout(self):
        result = self._run(False)
        self.assertIn("python progress", result.stdout)
        self.assertIn("child progress", result.stdout)


if __name__ == "__main__":
    unittest.main()
