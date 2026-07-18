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
