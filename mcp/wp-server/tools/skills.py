from __future__ import annotations
import subprocess
import sys

from app import *  # noqa: F401,F403


def _sb_skill(*args: str) -> dict:
    """Run `./sb skill <args>` and return {ok, output}. Single source of truth =
    the CLI implementation in sandbox/commands/skill.py."""
    cmd = [str(SANDBOX_ROOT / "sb"), "skill", *args]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    return {"ok": res.returncode == 0, "output": out}


def _skill_mod():
    sys.path.insert(0, str(SANDBOX_ROOT))
    from sandbox.commands import skill as _s
    return _s


@mcp.tool()
def list_skills(*, project_dir: str) -> dict:
    """List all skills across sources (project > personal > sandbox) with their
    scope + one-line description — the lazy catalog (bodies load on demand via
    load_skill). Spec 006."""
    s = _skill_mod()
    recs = s._resolve(include_disabled=False)
    return {"ok": True, "skills": [
        {"slug": k, "scope": r["scope"], "description": r["description"]}
        for k, r in sorted(recs.items())
    ]}


@mcp.tool()
def skill_write(title: str, description: str, body: str = "",
                scope: str = "", on_conflict: str = "fail", *, project_dir: str) -> dict:
    """Create a foldered skill (slug from title) in scope project|personal|sandbox
    (default: project if in a project, else sandbox). on_conflict: fail|replace|rename.
    Spec 006."""
    args = ["write", "--title", title, "--desc", description, "--file", "-"]
    if scope:
        args += ["--scope", scope]
    if on_conflict:
        args += ["--on-conflict", on_conflict]
    cmd = [str(SANDBOX_ROOT / "sb"), "skill", *args]
    res = subprocess.run(cmd, input=body, capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
    return {"ok": res.returncode == 0, "output": ((res.stdout or "") + (res.stderr or "")).strip()}


@mcp.tool()
def skill_edit(slug: str, description: str = "", body: str = "", *, project_dir: str) -> dict:
    """Edit an existing skill's description and/or body. Spec 006."""
    args = ["edit", slug]
    if description:
        args += ["--desc", description]
    if body:
        cmd = [str(SANDBOX_ROOT / "sb"), "skill", *args, "--file", "-"]
        res = subprocess.run(cmd, input=body, capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
        return {"ok": res.returncode == 0, "output": ((res.stdout or "") + (res.stderr or "")).strip()}
    return _sb_skill(*args)


@mcp.tool()
def skill_delete(slug: str, scope: str = "", *, project_dir: str) -> dict:
    """Delete a skill (optionally restricted to a scope). Spec 006."""
    args = ["delete", slug]
    if scope:
        args += ["--scope", scope]
    return _sb_skill(*args)
