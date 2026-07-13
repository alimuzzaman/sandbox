import json
import tempfile
import unittest
from pathlib import Path

from sandbox.recovery.catalog import load_catalog
from sandbox.recovery.errors import RecoveryError


class TestRecoveryCatalog(unittest.TestCase):
    def test_shipped_catalog_is_valid_and_exact(self):
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        self.assertEqual(set(catalog.by_id()), {
            "control-plane", "lenzora-prod", "lenzora-prod-storage", "alimuzzaman-site", "amarsonar-bangla-prod",
        })

    def test_unknown_fields_fail_closed(self):
        document = {"schema_version": 1, "profiles": [{
            "id": "fixture", "scope": "test", "source_type": "filesystem",
            "allowed_roots": ["root"], "sources": ["path"], "capture_mode": "partial",
            "consistency": "stable", "excludes": [], "sensitivity": "encrypted",
            "restore_target": "target", "verification": "hash", "retention_class": "test",
            "dependencies": [], "unexpected": True,
        }]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "catalog.json"; path.write_text(json.dumps(document))
            with self.assertRaises(RecoveryError):
                load_catalog(path)

    def test_command_text_and_secret_metadata_fail_closed(self):
        document = {"schema_version": 1, "profiles": [{
            "id": "fixture", "scope": "test", "source_type": "filesystem",
            "allowed_roots": ["root"], "sources": ["path"], "capture_mode": "partial",
            "consistency": "stable", "excludes": [], "sensitivity": "encrypted",
            "restore_target": "target", "verification": "hash", "retention_class": "test",
            "dependencies": [], "metadata": {"token": "not-allowed"},
        }]}
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "catalog.json"; path.write_text(json.dumps(document))
            with self.assertRaises(RecoveryError) as caught:
                load_catalog(path)
        self.assertEqual(caught.exception.code, "invalid_catalog")

    def test_duplicate_dependency_cycle_fails_closed(self):
        document = {"schema_version": 1, "profiles": []}
        base = {
            "scope": "test", "source_type": "filesystem", "allowed_roots": ["root"],
            "sources": ["path"], "capture_mode": "partial", "consistency": "stable",
            "excludes": [], "sensitivity": "encrypted", "restore_target": "target",
            "verification": "hash", "retention_class": "test", "metadata": {},
        }
        document["profiles"] = [dict(base, id="first", dependencies=["second"]), dict(base, id="second", dependencies=["first"])]
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "catalog.json"; path.write_text(json.dumps(document))
            with self.assertRaises(RecoveryError):
                load_catalog(path)

    def test_initial_profiles_keep_disposable_state_out_of_scope(self):
        catalog = load_catalog(Path(__file__).parents[1] / "config" / "recovery-profiles.json")
        profiles = catalog.by_id()
        self.assertIn("containers", profiles["lenzora-prod"].excludes)
        self.assertIn("wordpress-snapshots", profiles["control-plane"].excludes)
        self.assertEqual(profiles["alimuzzaman-site"].capture_mode, "provenance")


if __name__ == "__main__": unittest.main()
