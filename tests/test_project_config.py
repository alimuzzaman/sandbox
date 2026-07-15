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


if __name__ == "__main__":
    unittest.main()