"""Focused regression coverage for the remaining Spec 006 convergence work."""
from __future__ import annotations

import json
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

    def test_sandbox_conflict_rename_preserves_builtin_and_writes_free_slug(self):
        with tempfile.TemporaryDirectory() as temp:
            sandbox = Path(temp) / "sandbox-skills"
            original = _skill(sandbox, "built-in", "original", body="# unchanged\n")
            args = SimpleNamespace(
                action="write", slug=None, title="Built In", desc="renamed",
                scope="sandbox", on_conflict="rename", file=None, enable=True,
            )
            with patch.object(cli_skill, "_SANDBOX_SKILLS", sandbox):
                cli_skill.cmd_skill({}, args)
            renamed = sandbox / "built-in-2" / "SKILL.md"
            self.assertTrue(renamed.is_file())
            self.assertIn("description: renamed", renamed.read_text())
            self.assertEqual(original.read_text().split("---\n\n", 1)[1], "# unchanged\n")

    def test_project_rename_does_not_shadow_enabled_sandbox_builtin(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "sandbox.config.json").write_text("{}\n")
            written = subprocess.run(
                [str(ROOT / "sb"), "skill", "write", "--title", "Fix",
                 "--desc", "project-specific fix", "--on-conflict", "rename", "--file", "-"],
                cwd=project, input="# project fix\n", capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertIn("wrote skill 'fix-2' (project)", written.stdout)
            self.assertFalse((project / ".claude/skills/fix/SKILL.md").exists())
            self.assertTrue((project / ".claude/skills/fix-2/SKILL.md").is_file())

            listed = subprocess.run(
                [str(ROOT / "sb"), "skill", "list"], cwd=project,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("fix-2", listed.stdout)
            self.assertIn("fix", listed.stdout)

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
            shown = subprocess.run([str(ROOT / "sb"), "skill", "show", "hidden-skill"],
                                   cwd=project, capture_output=True, text=True, check=False)
            self.assertNotEqual(shown.returncode, 0)
            self.assertIn("no skill 'hidden-skill'", shown.stderr)

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

    @unittest.skipIf(context is None, "MCP server dependencies are not installed")
    def test_load_context_returns_normalized_enabled_sandbox_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claude_md = root / "CLAUDE.md"
            claude_md.write_text("guide\n")
            visible = _skill(root / "skills", "visible", "one line")
            _skill(root / "skills", "disabled", "hidden", enabled=False)
            with patch.object(app, "SANDBOX_ROOT", root), \
                 patch.object(app, "SANDBOX_SKILLS_DIR", root / "skills"), \
                 patch.object(context, "SANDBOX_ROOT", root), \
                 patch.object(context, "SANDBOX_CLAUDE_MD", claude_md):
                payload = context.load_context()
            self.assertEqual(payload["available_skills"], [{
                "slug": "visible", "name": "visible", "description": "one line",
                "source": "sandbox", "scope": "sandbox",
                "path": str(visible.relative_to(root)),
            }])
            json.dumps(payload["available_skills"])

    @unittest.skipIf(context is None, "MCP server dependencies are not installed")
    def test_focus_get_returns_enabled_project_catalog_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            visible = _skill(project / ".claude" / "skills", "visible", "one line")
            record = {"scope": "project", "description": "one line", "path": visible}
            core = SimpleNamespace(find_project_root=lambda _: str(project))
            instance = {"mailpit_port": 8025}
            with patch.object(context, "_require_project_capability", return_value=None), \
                 patch.object(context, "_project_instance", return_value=("test", None)), \
                 patch.object(context, "_core", return_value=core), \
                 patch.object(context, "_resolve_instance", return_value=instance), \
                 patch.object(context, "_site_url", return_value="https://test.tst"), \
                 patch.object(context, "_focus_file", return_value=project / ".focus"), \
                 patch.object(context, "_catalog", return_value={"visible": record}):
                payload = context.focus_get(str(project))
            self.assertEqual(payload["available_skills"], [{
                "name": "visible", "slug": "visible", "source": "project",
                "scope": "project", "description": "one line", "path": str(visible),
            }])

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

    @unittest.skipIf(skills is None, "MCP server dependencies are not installed")
    def test_project_skill_operations_return_structured_outcomes(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            root = project / ".claude" / "skills"
            skill_md = root / "project-skill" / "SKILL.md"
            record = {"scope": "project", "description": "one line", "path": skill_md}
            with patch.object(skills, "_sb_skill", return_value={
                    "ok": True,
                    "output": f"wrote skill 'project-skill' (project) → {skill_md}",
                 }), patch.object(skills, "_catalog", return_value={"project-skill": record}):
                written = skills.skill_write(
                    "Project Skill", "one line", "# body\n", project_dir=str(project)
                )
            self.assertEqual(written, {
                "ok": True, "action": "created", "slug": "project-skill",
                "source": "project", "scope": "project", "description": "one line",
                "path": str(skill_md),
            })
            json.dumps(written)

            with patch.object(skills, "_sb_skill", return_value={"ok": True, "output": "edited"}), \
                 patch.object(skills, "_selected_record", return_value=record):
                edited = skills.skill_edit(
                    "project-skill", description="changed", project_dir=str(project)
                )
            self.assertEqual(edited["action"], "updated")
            self.assertEqual(edited["slug"], "project-skill")
            self.assertEqual(edited["path"], str(skill_md))

            with patch.object(skills, "_sb_skill", return_value={"ok": True, "output": "deleted"}), \
                 patch.object(skills, "_selected_record", return_value=record):
                deleted = skills.skill_delete("project-skill", project_dir=str(project))
            self.assertEqual(deleted["action"], "deleted")
            self.assertEqual(deleted["slug"], "project-skill")
            self.assertEqual(deleted["path"], str(skill_md))
            json.dumps([edited, deleted])

    @unittest.skipIf(skills is None, "MCP server dependencies are not installed")
    def test_skill_write_conflict_returns_machine_actionable_details(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            with patch.object(skills, "_sb_skill", return_value={
                "ok": False,
                "output": "skill 'project-skill' exists in project (free slug: project-skill-2)",
            }):
                payload = skills.skill_write(
                    "Project Skill", "one line", project_dir=str(project)
                )
            self.assertEqual(payload["ok"], False)
            self.assertEqual(payload["code"], "skill_conflict")
            self.assertEqual(payload["slug"], "project-skill")
            self.assertEqual(payload["suggested_slug"], "project-skill-2")
            json.dumps(payload)
