from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BuiltinSkillMirrorTests(unittest.TestCase):
    def test_builtin_agent_skills_match_canonical_sources(self):
        for name in ("bug-repro", "fix", "sandbox-cli", "snapshot", "wp-debug", "wp-pilot"):
            with self.subTest(name=name):
                canonical = (ROOT / "skills" / name / "SKILL.md").read_text()
                mirrored = (ROOT / ".agents/skills" / name / "SKILL.md").read_text()
                self.assertEqual(canonical, mirrored)
