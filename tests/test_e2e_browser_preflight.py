from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sandbox.commands import e2e


class TestE2eBrowserPreflight(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent

    def test_existing_executable_passes(self):
        executable = self.root / "fixture-chromium"
        with patch.object(e2e.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout=str(executable) + "\n", stderr="")), \
             patch.object(Path, "is_file", return_value=True) as is_file:
            self.assertIsNone(e2e._playwright_browser_preflight(self.root))
        is_file.assert_called_once()

    def test_missing_executable_is_typed_and_nonmutating(self):
        with patch.object(e2e.subprocess, "run", return_value=SimpleNamespace(
                returncode=0, stdout="/cache/ms-playwright/chromium/missing\n", stderr="")), \
             patch.object(Path, "is_file", return_value=False):
            result = e2e._playwright_browser_preflight(self.root)

        self.assertEqual(result["code"], "playwright_browser_missing")
        self.assertIn("playwright install chromium", result["message"])

    def test_missing_dependency_is_typed(self):
        with patch.object(e2e.subprocess, "run", return_value=SimpleNamespace(
                returncode=2, stdout="", stderr="Cannot find module")):
            result = e2e._playwright_browser_preflight(self.root)

        self.assertEqual(result["code"], "playwright_dependency_missing")

    def test_node_missing_is_typed(self):
        with patch.object(e2e.subprocess, "run", side_effect=FileNotFoundError):
            result = e2e._playwright_browser_preflight(self.root)

        self.assertEqual(result["code"], "playwright_node_missing")


if __name__ == "__main__":
    unittest.main()
