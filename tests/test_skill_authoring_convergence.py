"""Focused regression coverage for the remaining Spec 006 convergence work."""
from __future__ import annotations

import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = ROOT / "mcp" / "wp-server"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from sandbox.commands import skill as cli_skill
try:
    import app
    from tools import context, skills
except ModuleNotFoundError:
    app = context = skills = None


def _skill(root: Path, slug: str, description: str, *, enabled: bool = True,
           body: str = "# body\n") -> Path:
    path = root / slug / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    enable = "" if enabled else "enable: false\n"
    path.write_text(f"---\nname: {slug}\ndescription: {description}\n{enable}---\n\n{body}")
    return path


class SkillAuthoringConvergenceTests(unittest.TestCase):
    @unittest.skipIf(app is None, "MCP server dependencies are not installed")
    def test_startup_skill_catalog_omits_disabled_entries_and_points_to_live_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _skill(root, "visible", "shown at startup")
            _skill(root, "disabled", "must remain hidden", enabled=False)
            with patch.object(app, "SANDBOX_ROOT", root), \
                 patch.object(app, "SANDBOX_SKILLS_DIR", root):
                instructions = app._startup_instructions()
        self.assertIn("`visible` — shown at startup", instructions)
        self.assertNotIn("disabled", instructions)
        self.assertIn("list_skills(project_dir=...)", instructions)
        self.assertIn("load_skill(name, project_dir=...)", instructions)

    def test_explicit_project_dir_wins_and_disabled_skills_are_omitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = root / "sandbox-skills"
            personal = root / "personal-skills"
            project = root / "project"
            _skill(sandbox, "shared", "sandbox")
            _skill(personal, "shared", "personal")
            _skill(project / ".claude" / "skills", "shared", "project", body="# project\n")
            _skill(project / ".claude" / "skills", "disabled", "hidden", enabled=False)
            with patch.object(cli_skill, "_SANDBOX_SKILLS", sandbox), \
                 patch.object(cli_skill, "_PERSONAL_SKILLS", personal):
                catalog = cli_skill._resolve(project_dir=project)
                self.assertEqual(catalog["shared"]["scope"], "project")
                self.assertEqual(catalog["shared"]["description"], "project")
                self.assertNotIn("disabled", catalog)

    def test_sandbox_replace_refuses_to_overwrite_an_existing_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            sandbox = Path(temp) / "sandbox-skills"
            original = _skill(sandbox, "built-in", "original", body="# unchanged\n")
            args = SimpleNamespace(
                action="write", slug=None, title="Built In", desc="replacement",
                scope="sandbox", on_conflict="replace", file=None, enable=True,
            )
            with patch.object(cli_skill, "_SANDBOX_SKILLS", sandbox), \
                 patch.object(cli_skill, "die", side_effect=SystemExit) as die:
                with self.assertRaises(SystemExit):
                    cli_skill.cmd_skill({}, args)
            self.assertIn("cannot replace built-in sandbox skill", die.call_args.args[0])
            self.assertEqual(original.read_text().split("---\n\n", 1)[1], "# unchanged\n")

    def test_cli_uses_its_cwd_as_the_project_and_hides_disabled_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "sandbox.config.json").write_text("{}\n")
            command = [str(ROOT / "sb"), "skill", "write", "--title", "Project Skill",
                       "--desc", "available from the project", "--file", "-"]
            written = subprocess.run(command, cwd=project, input="# project\n",
                                     capture_output=True, text=True, check=False)
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertTrue((project / ".claude/skills/project-skill/SKILL.md").is_file())
            disabled = subprocess.run(
                [str(ROOT / "sb"), "skill", "write", "--title", "Hidden Skill",
                 "--desc", "must not be listed", "--disable", "--file", "-"],
                cwd=project, input="# hidden\n", capture_output=True, text=True, check=False,
            )
            self.assertEqual(disabled.returncode, 0, disabled.stderr)
            listed = subprocess.run([str(ROOT / "sb"), "skill", "list"], cwd=project,
                                    capture_output=True, text=True, check=False)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("project-skill", listed.stdout)
            self.assertNotIn("hidden-skill", listed.stdout)

    @unittest.skipIf(skills is None, "MCP server dependencies are not installed")
    def test_mcp_cli_wrapper_runs_from_the_caller_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            result = SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
            with patch.object(skills.subprocess, "run", return_value=result) as run:
                payload = skills._sb_skill("list", project_dir=str(project))
            self.assertTrue(payload["ok"])
            self.assertEqual(run.call_args.kwargs["cwd"], str(project.resolve()))

    @unittest.skipIf(context is None, "MCP server dependencies are not installed")
    def test_load_skill_returns_the_precedence_selected_record(self):
        with tempfile.TemporaryDirectory() as temp:
            skill_md = _skill(Path(temp), "shared", "project", body="# project body\n")
            record = {"scope": "project", "description": "project", "path": skill_md}
            with patch.object(context, "_catalog", return_value={"shared": record}):
                payload = context.load_skill("shared", project_dir=temp)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["source"], "project")
            self.assertEqual(payload["content"], skill_md.read_text())

    @unittest.skipIf(skills is None, "MCP server dependencies are not installed")
    def test_list_skills_includes_contract_source_and_path(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            skill_md = _skill(project / ".claude" / "skills", "visible", "one line")
            record = {"scope": "project", "description": "one line", "path": skill_md}
            with patch.object(skills, "_catalog", return_value={"visible": record}):
                payload = skills.list_skills(project_dir=str(project))
            self.assertEqual(payload["skills"], [{
                "slug": "visible", "source": "project", "scope": "project",
                "description": "one line", "path": str(skill_md),
            }])
