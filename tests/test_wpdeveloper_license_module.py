"""Static contract for the scoped WPDeveloper keyless activation catalog."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "sandbox/assets/licensing/platforms/wpdeveloper.php"
CATALOG = ROOT / "docs/plugin-catalog.md"


class WPDeveloperLicenseModuleContract(unittest.TestCase):
    def test_xspeed_pro_is_allowlisted_without_matching_free_xspeed(self):
        source = MODULE.read_text(encoding="utf-8")
        self.assertIn("'xspeed-pro'", source)
        self.assertNotIn("'xspeed',", source)

    def test_catalog_documents_the_exact_pro_slug_boundary(self):
        catalog = " ".join(CATALOG.read_text(encoding="utf-8").split())
        self.assertIn("exact `xspeed-pro` Pro slug", catalog)
        self.assertIn("free `xspeed` slug is not matched", catalog)


if __name__ == "__main__":
    unittest.main()
