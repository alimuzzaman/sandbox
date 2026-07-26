import json
import tempfile
import unittest
from pathlib import Path


class TestConfigFacade(unittest.TestCase):
    def test_shipped_loader_defaults_kind_to_wordpress(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "fixture-plugin"}))
            result = sandbox_core.load_project_config(root)
            self.assertEqual(result["kind"], "wordpress")
            self.assertEqual(result["slug"], "fixture-plugin")

    def test_explicit_wordpress_matches_omitted_kind(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as left, \
                tempfile.TemporaryDirectory(dir=Path.home()) as right:
            a, b = Path(left), Path(right)
            content = {"slug": "fixture-plugin", "phpVersion": "8.2"}
            (a / "sandbox.config.json").write_text(json.dumps(content))
            (b / "sandbox.config.json").write_text(json.dumps({"kind": "wordpress", **content}))
            omitted = sandbox_core.load_project_config(a)
            explicit = sandbox_core.load_project_config(b)
            omitted.pop("root", None)
            explicit.pop("root", None)
            self.assertEqual(omitted, explicit)

    def test_compose_settings_can_recreate_and_extend_startup(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: {web: {image: nginx}}\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml", "service": "web", "internal_port": 80,
                    "health_path": "/", "startupTimeoutSeconds": 300,
                    "recreateOnEnsure": True,
                },
            }))
            result = sandbox_core.load_project_config(root)
            self.assertEqual(result["startup_timeout_seconds"], 300)
            self.assertTrue(result["recreate_on_ensure"])


if __name__ == "__main__":
    unittest.main()
