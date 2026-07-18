import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


class JobCliTests(unittest.TestCase):
    def test_test_matrix_accepts_flags_after_mode_and_returns_isolated_children(self):
        result = subprocess.run([
            str(ROOT / "sb"), "test", "matrix", "--local", "--workspace", "cli-cell-a",
            "--workspace", "cli-cell-b", "--timeout", "60", "--json", "--",
            sys.executable, "-c", "print('cli-matrix')",
        ], cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["summary"]["submitted"], 2)
        self.assertEqual({child["workspace"] for child in payload["children"]}, {"cli-cell-a", "cli-cell-b"})
