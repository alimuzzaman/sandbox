"""Final feature-022 compatibility matrix for the shipped config facade."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FIXTURES = Path(__file__).parent / "fixtures" / "modularity" / "config"


class TestProjectConfigCompatibilityMatrix(unittest.TestCase):
    def test_global_project_override_and_label_precedence_uses_public_facade(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            global_path = root / "global.json"
            global_path.write_text((FIXTURES / "global.json").read_text())
            (root / "sandbox.config.json").write_text((FIXTURES / "project.json").read_text())
            (root / "sandbox.config.override.json").write_text((FIXTURES / "override.json").read_text())
            (root / "sandbox.config.qa.json").write_text((FIXTURES / "label-qa.json").read_text())

            with patch.dict(os.environ, {"SANDBOX_USER_CONFIG": str(global_path)}):
                result = sandbox_core.load_project_config(root, label="qa")

            self.assertEqual(result["kind"], "wordpress")
            self.assertEqual(result["phpVersion"], "8.3")
            self.assertEqual(result["wpVersion"], "6.7")
            self.assertEqual(result["config"]["GLOBAL_VALUE"], "global")
            self.assertEqual(result["config"]["PROJECT_VALUE"], "project")
            self.assertEqual(result["config"]["OVERRIDDEN_VALUE"], "override")
            self.assertEqual(result["config"]["INSTANCE_LABEL"], "qa")
            self.assertIn("query-monitor", result["plugins_resolved"])
            self.assertIn("fixture-plugin", result["plugins_resolved"])

    def test_legacy_wordpress_fixture_keeps_legacy_observables(self):
        import sandbox_core

        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            document = json.loads((FIXTURES / "legacy-wordpress.json").read_text())
            (root / "sandbox.config.json").write_text(json.dumps(document))
            result = sandbox_core.load_project_config(root)

        self.assertEqual(result["kind"], "wordpress")
        self.assertEqual(result["slug"], "fixture-plugin")
        self.assertEqual(result["phpVersion"], "8.2")
        self.assertEqual(result["wpVersion"], "6.8")
        self.assertTrue(result["plugins_resolved"]["fixture-plugin"]["active"])
        self.assertTrue(result["plugins_resolved"]["query-monitor"]["active"])




import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestProjectConfig(unittest.TestCase):
    def test_legacy_config_defaults_to_wordpress_without_generic_normalization(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({"slug": "legacy-plugin"}))
            legacy = mock.Mock(return_value={"slug": "legacy-plugin", "wordpress_port": 8192})

            result = resolve_project_config(root, legacy_loader=legacy)

            self.assertEqual(result["kind"], "wordpress")
            self.assertEqual(result["wordpress_port"], 8192)
            legacy.assert_called_once_with(root, label=None)

    def test_explicit_compose_descriptor_uses_declared_service_and_health_probe(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory(suffix=".project") as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: { web: { image: nginx:alpine } }\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml",
                    "service": "web",
                    "internal_port": 80,
                    "health_path": "/healthz",
                },
            }))
            legacy = mock.Mock(side_effect=AssertionError("legacy WordPress loader called"))

            result = resolve_project_config(root, legacy_loader=legacy)

            self.assertEqual(result["kind"], "compose")
            self.assertEqual(result["compose_file"], str(root / "compose.yaml"))
            self.assertEqual(result["service"], "web")
            self.assertEqual(result["internal_port"], 80)
            self.assertEqual(result["health_path"], "/healthz")
            legacy.assert_not_called()

    def test_compose_descriptor_rejects_path_outside_project_root(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "../outside.yaml",
                    "service": "web",
                    "internal_port": 80,
                    "health_path": "/",
                },
            }))

            with self.assertRaisesRegex(ValueError, "compose file.*project root"):
                resolve_project_config(root, legacy_loader=mock.Mock())

    def test_dot_named_project_and_label_override_preserve_display_name(self):
        from sandbox.config.facade import resolve_project_config

        with tempfile.TemporaryDirectory(suffix=".site") as tmp:
            root = Path(tmp)
            (root / "compose.yaml").write_text("services: { web: { image: nginx:alpine } }\n")
            (root / "sandbox.config.json").write_text(json.dumps({
                "kind": "compose",
                "compose": {
                    "file": "compose.yaml",
                    "service": "web",
                    "internal_port": 80,
                    "health_path": "/",
                },
            }))
            (root / "sandbox.config.preview.json").write_text(json.dumps({
                "compose": {"health_path": "/preview-health"},
            }))

            result = resolve_project_config(root, label="preview", legacy_loader=mock.Mock())

            self.assertEqual(result["display_name"], root.name)
            self.assertEqual(result["label"], "preview")
            self.assertEqual(result["health_path"], "/preview-health")




if __name__ == "__main__":
    unittest.main()
