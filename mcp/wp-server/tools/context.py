from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import (SANDBOX_CLAUDE_MD, SANDBOX_ROOT, SANDBOX_WORKFLOWS_DIR, _core, _focus_file, _list_sandbox_skills, _list_sandbox_workflows, _parse_skill_metadata, _project_instance, _resolve_instance, _require_project_capability, _site_url, _skill_prompt_body, _wpcli, mcp)


def _catalog(project_dir: str) -> dict:
    """Resolve the live catalog without importing another MCP tool group."""
    import sys
    if str(SANDBOX_ROOT) not in sys.path:
        sys.path.insert(0, str(SANDBOX_ROOT))
    from sandbox.commands import skill
    return skill._resolve(project_dir=project_dir)


def _record_payload(slug: str, record: dict) -> dict:
    return {
        "slug": slug,
        "source": record["scope"],
        "scope": record["scope"],
        "description": record["description"],
        "path": str(record["path"]),
    }



@mcp.tool()
def focus_get(project_dir: str, include_claude_md: bool = False,
              max_bytes: int = 16_000, label: str | None = None) -> dict:
    """Return the project's instance + focused plugin, its CLAUDE.md, and any
    skill packs it ships (so Claude can read them on demand).

    In the per-project model the project root IS the plugin's source repo, so
    this reads CLAUDE.md / .claude/skills/*/SKILL.md from `project_dir` directly.
    Requires the instance to exist — call ensure_instance first.
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.rest")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    sc = _core()
    root = Path(sc.find_project_root(project_dir))
    inst_cfg = _resolve_instance(inst)
    wp_url = _site_url(inst_cfg)
    ff = _focus_file(inst)
    focus = ff.read_text().strip() if ff.exists() else root.name

    out = {
        "ok": True, "instance": inst, "project_root": str(root),
        "focus": focus,
        "wordpress_url": wp_url,
        "admin_url": f"{wp_url}/wp-admin",
        "mailpit_url": f"http://localhost:{inst_cfg['mailpit_port']}",
        "source_path": str(root), "claude_md": None, "available_skills": [],
    }

    if include_claude_md:
        for candidate in ("CLAUDE.md", ".claude/CLAUDE.md"):
            cmd = root / candidate
            if cmd.is_file():
                out["claude_md"] = cmd.read_bytes()[:max_bytes].decode(
                    "utf-8", errors="replace")
                out["claude_md_path"] = str(cmd)
                out["claude_md_truncated"] = cmd.stat().st_size > max_bytes
                break

    # Reuse the same enabled-only, path-jailed resolver as list_skills so a
    # disabled project skill cannot leak through the focus convenience view.
    for slug, record in sorted(_catalog(project_dir).items()):
        if record["scope"] != "project":
            continue
        meta = _parse_skill_metadata(record["path"])
        out["available_skills"].append({
            "name": meta["name"] or slug,
            **_record_payload(slug, record),
        })
    return out

@mcp.tool()
def activate_plugin(slug: str, *, project_dir: str, label: str | None = None) -> dict:
    """wp plugin activate <slug>. Slug must match the plugin folder name."""
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    return _wpcli(["plugin", "activate", slug], instance=inst)

@mcp.tool()
def deactivate_plugin(slug: str, *, project_dir: str, label: str | None = None) -> dict:
    """wp plugin deactivate <slug>."""
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    return _wpcli(["plugin", "deactivate", slug], instance=inst)

@mcp.tool()
def load_context() -> dict:
    """Return the full Sandbox CLAUDE.md (operating guide).

    Call this when the user wants to "work with sandbox" or when you've
    decided you're engaged in WP work and need the full operating prompt
    beyond the 2KB instructions baseline. Also returns the list of
    available top-level sandbox skills so you know which load_skill()
    calls are available. For configured remote development, the guide directs
    callers to the co-located remote MCP server and durable status/output reads
    rather than a long-lived child-process stream.
    """
    if not SANDBOX_CLAUDE_MD.exists():
        return {"ok": False, "error": f"missing {SANDBOX_CLAUDE_MD}"}
    available_skills = []
    for skill in _list_sandbox_skills():
        skill_path = Path(skill["path"])
        slug = skill_path.parent.name
        available_skills.append({
            "slug": slug,
            "name": skill["name"] or slug,
            "description": skill["description"],
            "source": "sandbox",
            "scope": "sandbox",
            "path": str(skill_path),
        })
    return {
        "ok": True,
        "claude_md": SANDBOX_CLAUDE_MD.read_text(errors="replace"),
        "claude_md_path": str(SANDBOX_CLAUDE_MD),
        "available_skills": available_skills,
        "skill_catalog_guidance": (
            "Use list_skills(project_dir=...) for the live enabled catalog and "
            "load_skill(name, project_dir=...) to fetch only the precedence-selected body."
        ),
    }

@mcp.tool()
def load_workflow(name: str) -> dict:
    """Return the full text of a top-level sandbox workflow (WORKFLOW.md).

    Workflows are multi-phase playbooks (vs. skills, which are reflexes).
    Use this when the situation calls for a deliberate multi-stage process
    with user gates between phases — e.g. `load_workflow('build-feature')`
    before starting a net-new feature, so you run the
    establish → plan → build loop with explicit confirmation at each gate.

    Workflow names match the directories under sandbox/workflows/.
    """
    wf_md = SANDBOX_WORKFLOWS_DIR / name / "WORKFLOW.md"
    if not wf_md.is_file():
        return {
            "ok": False,
            "error": f"no workflow '{name}' (looked at {wf_md})",
            "available_workflows": [w["name"] for w in _list_sandbox_workflows()],
        }
    return {
        "ok": True,
        "name": name,
        "path": str(wf_md.relative_to(SANDBOX_ROOT)),
        "content": wf_md.read_text(errors="replace"),
    }

@mcp.tool()
def load_skill(name: str, project_dir: str = "") -> dict:
    """Return one enabled skill body, resolving project > personal > sandbox.

    Use this when a reflex tells you to engage a specific skill — e.g.
    `load_skill('fix')` before starting a bug-fix loop, `load_skill('wp-pilot')`
    before editor-driven authoring. Pass project_dir after matching a project
    catalog entry; without it this retains the sandbox-only default behavior.
    """
    if project_dir and not Path(project_dir).expanduser().is_dir():
        return {"ok": False, "error": f"invalid project_dir {project_dir!r}"}
    catalog = _catalog(project_dir or str(SANDBOX_ROOT))
    record = catalog.get(name)
    if not record:
        return {
            "ok": False,
            "error": f"no enabled skill '{name}'",
            "available_skills": [
                _record_payload(slug, entry)
                for slug, entry in sorted(catalog.items())
            ],
        }
    skill_md = record["path"]
    try:
        path = str(skill_md.relative_to(SANDBOX_ROOT))
    except ValueError:
        path = str(skill_md)
    return {
        "ok": True,
        "name": name,
        "source": record["scope"],
        "scope": record["scope"],
        "path": path,
        "content": skill_md.read_text(errors="replace"),
    }

@mcp.prompt()
def activate(task: str = "") -> str:
    """Load the full sandbox operating guide (CLAUDE.md) into this session."""
    cm = SANDBOX_CLAUDE_MD.read_text(errors="replace") if SANDBOX_CLAUDE_MD.exists() else "(missing CLAUDE.md)"
    header = "The user has activated sandbox mode. Follow this operating guide for the rest of the conversation.\n\n"
    if task:
        header += f"TASK FROM USER:\n{task}\n\n"
    return header + "--- SANDBOX CLAUDE.md ---\n\n" + cm

@mcp.prompt()
def focus(plugin: str) -> str:
    """Activate sandbox mode focused on a specific plugin.

    Runs the activation handshake: sets the focused plugin, loads the
    full sandbox operating guide, and instructs you to call focus_get
    next for the plugin's own conventions + skills.
    """
    return (
        f"The user is focusing on plugin '{plugin}' and entering sandbox mode. "
        f"Run this handshake now:\n\n"
        f"1. Call focus_set(plugin_slug='{plugin}') to persist the choice.\n"
        f"2. Call load_context() to pull the full sandbox CLAUDE.md.\n"
        f"3. Call focus_get() to fetch the plugin's own CLAUDE.md + available skills.\n\n"
        f"Then acknowledge with one line (which plugin is loaded, which skills are available) "
        f"and await the user's task. Follow the sandbox operating prompt for the rest of the conversation."
    )

@mcp.prompt()
def fix(task: str = "") -> str:
    """Engage the one-pass bug-fix loop (skills/fix/SKILL.md)."""
    return _skill_prompt_body("fix", task)

@mcp.prompt()
def build_feature(task: str = "") -> str:
    """Engage the three-phase feature-building workflow (workflows/build-feature/WORKFLOW.md).

    Generic across plugins. Phase 1 (ESTABLISH) captures spec + impact +
    edge cases; Phase 2 (PLAN) audits reuse + slices for de-risk; Phase 3
    (BUILD) executes slice-by-slice with live verification. User gates
    between each phase.
    """
    wf_md = SANDBOX_WORKFLOWS_DIR / "build-feature" / "WORKFLOW.md"
    if not wf_md.is_file():
        return f"Workflow 'build-feature' not found at {wf_md}."
    body = wf_md.read_text(errors="replace")
    header = (
        "The user has invoked the `build-feature` workflow. Follow its "
        "three-phase contract for the rest of this conversation. Do NOT "
        "skip ahead to Phase 3 — Phase 1 and Phase 2 each end with a "
        "structured block that you wait on user sign-off for.\n\n"
    )
    if task:
        header += f"FEATURE REQUEST FROM USER:\n{task}\n\n"
    return header + "--- WORKFLOW CONTRACT ---\n\n" + body

@mcp.prompt()
def bug_repro(task: str = "") -> str:
    """Reproduce a bug live on the running stack (skills/bug-repro/SKILL.md)."""
    return _skill_prompt_body("bug-repro", task)

@mcp.prompt()
def snapshot(task: str = "") -> str:
    """Snapshot / restore guidance (skills/snapshot/SKILL.md)."""
    return _skill_prompt_body("snapshot", task)

@mcp.prompt()
def wp_debug(task: str = "") -> str:
    """Debugging the WP stack (skills/wp-debug/SKILL.md)."""
    return _skill_prompt_body("wp-debug", task)

@mcp.prompt()
def wp_pilot(task: str = "") -> str:
    """Headless wp-admin authoring (skills/wp-pilot/SKILL.md)."""
    return _skill_prompt_body("wp-pilot", task)
