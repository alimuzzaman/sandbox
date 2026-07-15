import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

            path.write_text("[]")
            with self.assertRaises(HermesStateError):
                repo.read()

            for invalid in (
                '{"schema_version": 1, "installation": []}',
                '{"schema_version": 1, "sessions": "running"}',
            ):
                path.write_text(invalid)
                with self.assertRaises(HermesStateError):
                    repo.read()

    def test_schema_defaults_are_written_under_a_private_lock(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "runtime" / "hermes.json"
            repo = HermesStateRepository(path)

            repo.write(HermesState())

            self.assertEqual(repo.read().as_dict(), {
                "schema_version": 1, "installation": {}, "sessions": {},
            })
            self.assertEqual(repo.lock_path, path.with_name("hermes.json.lock"))
            self.assertEqual(repo.lock_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_read_and_write_reject_boolean_schema_and_non_mapping_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hermes.json"
            repo = HermesStateRepository(path)
            path.write_text('{"schema_version": true}')
            with self.assertRaises(HermesStateError):
                repo.read()
            with self.assertRaises(HermesStateError):
                repo.write(HermesState(installation=[]))
            with self.assertRaises(HermesStateError):
                repo.write(HermesState(schema_version=True))

    def test_interrupted_atomic_replace_keeps_the_previous_document(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hermes.json"
            repo = HermesStateRepository(path)
            repo.write(HermesState(installation={"commit": "before"}))
            before = path.read_bytes()

            with patch("sandbox.hermes.state.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    repo.write(HermesState(installation={"commit": "after"}))

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("hermes.json.*")), [repo.lock_path])

    def test_atomic_replace_syncs_the_parent_directory(self):
        with tempfile.TemporaryDirectory() as root:
            repo = HermesStateRepository(Path(root) / "hermes.json")
            with patch("sandbox.hermes.state.os.fsync") as fsync:
                repo.write(HermesState())
            self.assertEqual(fsync.call_count, 2)

    def test_legacy_collections_survive_compatible_read_and_rewrite(self):
        """US7 seam: state extraction must not discard legacy control-plane data."""
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "hermes.json"
            path.write_text(
                '{"schema_version": 1, "repositories": {"sandbox": {"ref": "main"}}, '
                '"sessions": {"job": {"status": "running"}}, "gates": {"v2": true}}\n'
            )
            repo = HermesStateRepository(path)

            state = repo.read()
            repo.write(state)

            persisted = path.read_text()
            self.assertIn('"repositories"', persisted)
            self.assertIn('"gates"', persisted)


if __name__ == "__main__": unittest.main()
