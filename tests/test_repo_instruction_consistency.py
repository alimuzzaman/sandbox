"""Operative repository instructions and indexes stay true."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepoInstructionConsistencyTests(unittest.TestCase):
    def test_no_instruction_permits_pushing_to_main(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            with self.subTest(file=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertNotIn("including `main`", text)
                self.assertIn("`main` is read-only", text)

    def test_every_spec_directory_is_indexed(self):
        index = (ROOT / "specs" / "README.md").read_text(encoding="utf-8")
        indexed = set(re.findall(r"^\| *(\d{3}) *\|", index, re.MULTILINE))
        numbers = {path.name[:3] for path in (ROOT / "specs").iterdir()
                   if path.is_dir() and re.match(r"\d{3}-", path.name)}
        self.assertEqual(numbers - indexed, set())

    def test_release_guidance_does_not_require_the_app_helper_namespace(self):
        text = (ROOT / "docs" / "release-readiness.md").read_text(encoding="utf-8")
        self.assertNotIn("app._run_sandbox_json", text)


if __name__ == "__main__":
    unittest.main()
