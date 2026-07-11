import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class TestBuilderExtractionBoundary(unittest.TestCase):
    def test_generic_surfaces_remain_and_builder_assets_are_absent(self):
        for path in (
            "sandbox/assets/abilities/00-sandbox-abilities.php",
            "sandbox/core/_provision.py",
            "sandbox/core/_licensing.py",
            "tools/pxdiff/pxdiff.mjs",
            "tools/backstop/vrdiff.mjs",
            "tools/dfdiff/dfdiff.py",
            "tools/dfdiff/specextract.py",
        ):
            self.assertTrue((ROOT / path).is_file(), path)
        for path in (
            "sandbox/assets/abilities/sandbox-editor.php",
            "sandbox/assets/abilities/00-sandbox-eb-finalizer.php",
            "sandbox/assets/abilities/00-sandbox-editor-schema-rest.php",
            "sandbox/assets/abilities/00-sandbox-schema-dump.php",
            "sandbox/commands/schema_catalog.py",
            "sandbox/core/_schema_catalog.py",
            "sandbox/assets/editor-schema/gutenberg.json.gz",
            "sandbox/assets/editor-schema/elementor.json.gz",
        ):
            self.assertFalse((ROOT / path).exists(), path)
