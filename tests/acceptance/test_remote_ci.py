"""Disposable-fixture coverage for compatible and protected remote CI paths."""

import tempfile
import unittest
from pathlib import Path

from sandbox.ci.workflow import preflight


class RemoteCIAcceptanceFixtures(unittest.TestCase):
    def _workflow(self, body):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "ci.yml"; path.write_text(body)
        return Path(temp.name), path.name

    def test_compatible_linux_matrix_fixture_is_preflightable(self):
        root, workflow = self._workflow(
            "jobs:\n  unit:\n    runs-on: ubuntu-latest\n"
            "    strategy:\n      matrix:\n        python: ['3.12', '3.13']\n"
            "    steps:\n      - run: python -V\n"
        )
        report = preflight(root, workflow)
        self.assertTrue(report["ok"])
        self.assertEqual(report["graph"]["matrix_cells"], 2)

    def test_incompatible_and_safe_mode_fixtures_do_not_execute_side_effects(self):
        root, incompatible = self._workflow(
            "jobs:\n  windows:\n    runs-on: windows-latest\n    steps:\n      - run: echo no\n"
        )
        report = preflight(root, incompatible)
        self.assertFalse(report["ok"])
        self.assertIn("act.non-linux-runner", report["blocking"])

        root, protected = self._workflow(
            "jobs:\n  release:\n    runs-on: ubuntu-latest\n    steps:\n      - run: npm run deploy\n"
        )
        safe = preflight(root, protected, safe_mode=True)
        self.assertTrue(safe["ok"])
        self.assertEqual(safe["safe_mode_actions"], [{
            "id": "safe-mode:release:0", "location": "jobs.release.steps[0]", "action": "neutralized",
        }])


if __name__ == "__main__":
    unittest.main()
