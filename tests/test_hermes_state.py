import tempfile
import unittest
from pathlib import Path

from sandbox.hermes.state import HermesState, HermesStateError, HermesStateRepository


class TestHermesState(unittest.TestCase):
    def test_atomic_round_trip_and_corruption(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hermes.json"
            repo = HermesStateRepository(path)
            repo.write(HermesState(installation={"commit": "fixture"}, sessions={}))
            self.assertEqual(repo.read().installation["commit"], "fixture")
            self.assertTrue(repo.lock_path.exists())
            path.write_text("{")
            with self.assertRaises(HermesStateError):
                repo.read()


if __name__ == "__main__": unittest.main()
