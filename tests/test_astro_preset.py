from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sandbox.runtimes.presets.astro import propose_astro


class TestAstroPreset(unittest.TestCase):
    def _project(self, *, scripts=None, lock=None, config=""):
        root = Path(tempfile.mkdtemp())
        (root / "package.json").write_text(json.dumps({
            "name": "fixture", "scripts": scripts or {"dev": "astro dev"},
        }))
        if lock:
            (root / lock).write_text("")
        if config:
            (root / "astro.config.mjs").write_text(config)
        return root

    def test_defaults_to_npm_and_astro_port(self):
        root = self._project()
        result = propose_astro(root)
        self.assertEqual(result["preset"]["package_manager"], "npm")
        self.assertEqual(result["compose"]["internal_port"], 4321)
        self.assertIn("npm run dev -- --host 0.0.0.0", (root / "sandbox.compose.yml").read_text())

    def test_lockfile_and_config_port_are_explicit(self):
        root = self._project(lock="pnpm-lock.yaml", config="export default { server: { port: 4350 } };\n")
        result = propose_astro(root)
        self.assertEqual(result["preset"]["package_manager"], "pnpm")
        self.assertEqual(result["preset"]["install_command"], "pnpm install --frozen-lockfile")
        self.assertEqual(result["compose"]["internal_port"], 4350)

    def test_missing_explicit_dev_script_fails_without_writing_preset(self):
        root = self._project(scripts={"build": "astro build"})
        with self.assertRaisesRegex(ValueError, "scripts.dev"):
            propose_astro(root)
        self.assertFalse((root / "sandbox.config.json").exists())

    def test_detection_does_not_execute_project_script(self):
        root = self._project(scripts={"dev": "touch SHOULD_NOT_EXIST"})
        propose_astro(root)
        self.assertFalse((root / "SHOULD_NOT_EXIST").exists())


if __name__ == "__main__":
    unittest.main()
