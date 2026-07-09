"""Unit tests for the e2e multi-worker runner (docs/ci-e2e-runner-spec.md §2).

Stdlib `unittest` only, no docker — pure config-discovery / convention-
detection logic. Run from the repo root:

    .cli-venv/bin/python -m unittest discover -s tests -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sandbox.commands.e2e as e2e  # noqa: E402


class TestFindPlaywrightConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_config_at_root(self):
        (self.root / "playwright.config.js").write_text("module.exports = {}")
        found = e2e._find_playwright_config(self.root)
        self.assertEqual(found, self.root / "playwright.config.js")

    def test_finds_config_under_tests_subdir(self):
        # Templately's real layout: tests/playwright.config.js, not at root.
        (self.root / "tests").mkdir()
        (self.root / "tests" / "playwright.config.js").write_text("module.exports = {}")
        found = e2e._find_playwright_config(self.root)
        self.assertEqual(found, self.root / "tests" / "playwright.config.js")

    def test_explicit_override_wins(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "playwright.config.js").write_text("module.exports = {}")
        (self.root / "custom.config.js").write_text("module.exports = {}")
        found = e2e._find_playwright_config(self.root, explicit="custom.config.js")
        self.assertEqual(found, self.root / "custom.config.js")

    def test_none_when_missing(self):
        self.assertIsNone(e2e._find_playwright_config(self.root))


class TestDetectWpEnvPortConvention(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_detects_wp_env_url_require(self):
        # Mirrors Templately's real tests/playwright.config.js.
        cfg = self.root / "playwright.config.js"
        cfg.write_text(
            "const { resolveWpEnvConfig } = require('./e2e/utils/wp-env-url');\n"
            "module.exports = {};\n"
        )
        self.assertTrue(e2e._detect_wp_env_port_convention(cfg))

    def test_no_false_positive_on_ordinary_config(self):
        cfg = self.root / "playwright.config.js"
        cfg.write_text("module.exports = { use: { baseURL: process.env.BASE_URL } };\n")
        self.assertFalse(e2e._detect_wp_env_port_convention(cfg))


class TestWriteWpEnvPort(unittest.TestCase):
    def test_writes_expected_shape(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            entry = {"url": "https://x.tst", "login_url": "https://x.tst/?a=1",
                     "instance": "proj-e2e-w0"}
            e2e._write_wp_env_port(root, entry)
            data = json.loads((root / ".wp-env-port").read_text())
            self.assertEqual(data["baseUrl"], "https://x.tst")
            self.assertEqual(data["loginUrl"], "https://x.tst/?a=1")
            self.assertEqual(data["runtime"], "sandbox")
            self.assertEqual(data["instance"], "proj-e2e-w0")


if __name__ == "__main__":
    unittest.main()
