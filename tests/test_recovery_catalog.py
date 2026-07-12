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


if __name__ == "__main__": unittest.main()
