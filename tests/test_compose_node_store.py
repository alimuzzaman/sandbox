"""Strict generic-Compose node-store descriptor normalization."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.config.compose import ComposeSchemaProvider


class ComposeNodeStoreTests(unittest.TestCase):
    def _write_project(self, *, node_store="__missing__", machine=None, label=None):
        root = Path(tempfile.mkdtemp(dir=Path.home()))
        (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
        compose = {
            "file": "compose.yaml",
            "service": "web",
            "internal_port": 80,
            "health_path": "/",
        }
        if node_store != "__missing__":
            compose["nodeStore"] = node_store
        (root / "sandbox.config.json").write_text(json.dumps({
            "kind": "compose", "compose": compose,
        }))
        if machine is not None:
            (root / "sandbox.config.override.json").write_text(json.dumps({
                "compose": {"nodeStore": machine},
            }))
        if label is not None:
            (root / "sandbox.config.qa.json").write_text(json.dumps({
                "compose": {"nodeStore": label},
            }))
        return root

    def test_missing_and_explicit_boolean_values_normalize(self):
        for raw, expected in (("__missing__", False), (False, False), (True, True)):
            with self.subTest(raw=raw):
                root = self._write_project(node_store=raw)
                try:
                    result = ComposeSchemaProvider().resolve(root)
                finally:
                    for path in root.iterdir():
                        path.unlink()
                    root.rmdir()
                self.assertEqual(result["node_store"], expected)

    def test_project_machine_and_label_precedence_is_preserved(self):
        root = self._write_project(node_store=True, machine=False, label=True)
        try:
            result = ComposeSchemaProvider().resolve(root, label="qa")
            self.assertTrue(result["node_store"])

            (root / "sandbox.config.qa.json").write_text(json.dumps({
                "compose": {"nodeStore": False},
            }))
            result = ComposeSchemaProvider().resolve(root, label="qa")
            self.assertFalse(result["node_store"])

            (root / "sandbox.config.override.json").write_text(json.dumps({
                "compose": {"nodeStore": True},
            }))
            (root / "sandbox.config.qa.json").unlink()
            result = ComposeSchemaProvider().resolve(root)
            self.assertTrue(result["node_store"])
        finally:
            for path in root.iterdir():
                path.unlink()
            root.rmdir()

    def test_non_boolean_values_fail_closed_before_side_effects(self):
        invalid_values = (None, "true", "false", 0, 1, 1.0, [], {}, [True])
        for raw in invalid_values:
            with self.subTest(raw=raw):
                root = self._write_project(node_store=raw)
                try:
                    with patch("subprocess.run") as run, patch.object(Path, "write_text") as write:
                        with self.assertRaisesRegex(ValueError, r"compose\.nodeStore must be a boolean"):
                            ComposeSchemaProvider().resolve(root)
                    run.assert_not_called()
                    write.assert_not_called()
                finally:
                    # The patched write_text above leaves the config files intact.
                    for path in root.iterdir():
                        path.unlink()
                    root.rmdir()

    def test_invalid_override_value_is_rejected_after_precedence_merge(self):
        root = self._write_project(node_store=False, machine="yes")
        try:
            with self.assertRaisesRegex(ValueError, r"compose\.nodeStore must be a boolean"):
                ComposeSchemaProvider().resolve(root)
        finally:
            for path in root.iterdir():
                path.unlink()
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
