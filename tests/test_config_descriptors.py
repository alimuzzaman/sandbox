import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestDescriptorDiscovery(unittest.TestCase):
    def test_omitted_kind_defaults_to_wordpress_before_legacy_loader(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "fixture"}))
            seen = []

            def legacy(project_dir, label=None):
                seen.append((str(project_dir), label))
                return {"slug": "fixture", "kind": "wordpress"}

            result = resolve_project_config(root, legacy_loader=legacy)
            self.assertEqual(result["kind"], "wordpress")
            self.assertEqual(len(seen), 1)

    def test_non_wordpress_kind_avoids_legacy_slug_validation(self):
        from sandbox.config.facade import resolve_project_config
        from sandbox.config.registry import SchemaRegistry

        class TestProvider:
            def resolve(self, root, *, label=None):
                return {"kind": "test", "root": str(root), "label": label or "default"}

        with tempfile.TemporaryDirectory(suffix=".project") as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"kind": "test"}))
            schemas = SchemaRegistry()
            schemas.register("test", TestProvider(), owner="tests")
            legacy = mock.Mock(side_effect=AssertionError("legacy WordPress loader called"))
            result = resolve_project_config(root, legacy_loader=legacy, schemas=schemas)
            self.assertEqual(result["kind"], "test")
            legacy.assert_not_called()

    def test_duplicate_schema_registration_fails(self):
        from sandbox.config.registry import SchemaRegistry

        schemas = SchemaRegistry()
        schemas.register("wordpress", object(), owner="first")
        with self.assertRaisesRegex(ValueError, "duplicate schema kind"):
            schemas.register("wordpress", object(), owner="second")

    def test_descriptor_discovery_does_not_execute_project_code(self):
        from sandbox.config.descriptors import discover_project_kind

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"kind": "wordpress"}))
            with mock.patch.object(subprocess, "run", side_effect=AssertionError("executed")):
                self.assertEqual(discover_project_kind(root), "wordpress")

    def test_unknown_kind_fails_before_legacy_loader(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"kind": "unknown"}))
            legacy = mock.Mock(side_effect=AssertionError("legacy loader called"))
            with self.assertRaisesRegex(ValueError, "unsupported project kind"):
                resolve_project_config(root, legacy_loader=legacy)
            legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
