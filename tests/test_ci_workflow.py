import tempfile
import unittest
from pathlib import Path

from sandbox.ci.workflow import WorkflowError, preflight


class WorkflowTests(unittest.TestCase):
    def test_preflight_is_contained_and_blocks_unaccepted_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); flow = root / "ci.yml"
            flow.write_text("jobs:\n  test:\n    runs-on: ubuntu-latest\n    timeout-minutes: 2\n    strategy:\n      matrix:\n        node: [20, 22]\n")
            result = preflight(root, "ci.yml")
            self.assertFalse(result["ok"]); self.assertEqual(result["graph"]["matrix_cells"], 2)
            self.assertTrue(preflight(root, "ci.yml", accepted_differences=["act.job-timeout-ignored"])["ok"])
            with self.assertRaises(WorkflowError): preflight(root, "../outside.yml")

    def test_safe_mode_neutralizes_deployment_and_records_difference_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); flow = root / "ci.yml"
            flow.write_text("jobs:\n  release:\n    steps:\n      - run: ./deploy.sh\n")
            result = preflight(root, "ci.yml", safe_mode=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["safe_mode_actions"][0]["action"], "neutralized")
            self.assertEqual(result["differences"][-1]["id"], "safe-mode:release:0")
            self.assertNotIn("safe-mode:release:0", result["blocking"])
