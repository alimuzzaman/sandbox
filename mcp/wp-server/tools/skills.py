from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

from app import SANDBOX_ROOT, mcp


def _project_cwd(project_dir: str) -> str | None:
    """Validate the caller's project directory before launching the CLI."""
    try:
        root = Path(project_dir).expanduser().resolve()
    except OSError:
        return None
    return str(root) if root.is_dir() else None


def _sb_skill(*args: str, project_dir: str, body: str | None = None) -> dict:
    """Run `./sb skill <args>` and return {ok, output}. Single source of truth =
    the CLI implementation in sandbox/commands/skill.py."""
    cwd = _project_cwd(project_dir)
    if not cwd:
        return {"ok": False, "error": f"invalid project_dir {project_dir!r}"}
    cmd = [str(SANDBOX_ROOT / "sb"), "skill", *args]
    try:
        res = subprocess.run(cmd, input=body, capture_output=True, text=True, cwd=cwd)
    except OSError as exc:
        return {"ok": False, "error": f"could not run skill command: {exc}"}
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    return {"ok": res.returncode == 0, "output": out}


def _skill_mod():
    sys.path.insert(0, str(SANDBOX_ROOT))
    from sandbox.commands import skill as _s
    return _s


def _catalog(project_dir: str, *, include_disabled: bool = False) -> dict:
    """Read the live, precedence-resolved catalog for one project."""
    return _skill_mod()._resolve(include_disabled=include_disabled, project_dir=project_dir)


def _selected_record(slug: str, project_dir: str, *, scope: str = "",
                     include_disabled: bool = False) -> dict | None:
    if not scope:
        return _catalog(project_dir, include_disabled=include_disabled).get(slug)
    return next((record for record in _skill_mod()._iter_source(scope, project_dir)
                 if record["slug"] == slug and (include_disabled or record["enable"])), None)


def _record_payload(slug: str, record: dict) -> dict:
    return {
        "slug": slug,
        "source": record["scope"],
        "scope": record["scope"],
        "description": record["description"],
        "path": str(record["path"]),
    }


def _error_payload(result: dict, slug: str) -> dict:
    """Make expected conflict paths machine-actionable without hiding stderr."""
    output = result.get("output") or result.get("error", "skill command failed")
    payload = {"ok": False, "slug": slug, "error": output}
    suggested = re.search(r"free slug: ([a-z0-9-]+)", output)
    if suggested:
        payload.update({"code": "skill_conflict", "suggested_slug": suggested.group(1)})
    elif "built-in sandbox skill" in output:
        payload["code"] = "builtin_skill_conflict"
    return payload


def _written_record(result: dict, project_dir: str, *, existing: bool,
                    on_conflict: str, slug: str) -> dict:
    """Return a contract-complete write response from the CLI result."""
    match = re.search(r"wrote skill '([^']+)' \(([^)]+)\) → (.+)$", result["output"])
    actual_slug = match.group(1) if match else slug
    record = _catalog(project_dir).get(actual_slug)
    payload = {"ok": True, "action": "updated" if existing else "created", "slug": actual_slug}
    if existing and on_conflict == "rename":
        payload["action"] = "renamed"
    if record:
        payload.update(_record_payload(actual_slug, record))
    elif match:
        payload.update({"source": match.group(2), "scope": match.group(2), "path": match.group(3)})
    return payload


@mcp.tool()
def list_skills(*, project_dir: str) -> dict:
    """List all skills across sources (project > personal > sandbox) with their
    scope + one-line description — the lazy catalog (bodies load on demand via
    load_skill). Spec 006."""
    if not _project_cwd(project_dir):
        return {"ok": False, "error": f"invalid project_dir {project_dir!r}", "skills": []}
    recs = _catalog(project_dir)
    return {"ok": True, "skills": [
        _record_payload(k, r)
        for k, r in sorted(recs.items())
    ]}


@mcp.tool()
def skill_write(title: str, description: str, body: str = "",
                scope: str = "", enable: bool = True, on_conflict: str = "fail",
                *, project_dir: str) -> dict:
    """Create a foldered skill (slug from title) in scope project|personal|sandbox
    (default: project if in a project, else sandbox). on_conflict: fail|replace|rename.
    Spec 006."""
    s = _skill_mod()
    slug = s._slugify(title)
    if not slug:
        return {"ok": False, "error": "could not derive a slug from the title"}
    if not _project_cwd(project_dir):
        return {"ok": False, "slug": slug, "error": f"invalid project_dir {project_dir!r}"}
    chosen_scope = scope or ("project" if s._project_skills_dir(project_dir) else "sandbox")
    root = s._scope_root(chosen_scope, project_dir)
    existing = bool(root and (root / slug).exists())
    args = ["write", "--title", title, "--desc", description, "--file", "-"]
    if scope:
        args += ["--scope", scope]
    if not enable:
        args += ["--disable"]
    if on_conflict:
        args += ["--on-conflict", on_conflict]
    result = _sb_skill(*args, project_dir=project_dir, body=body)
    if not result["ok"]:
        return _error_payload(result, slug)
    return _written_record(result, project_dir, existing=existing,
                           on_conflict=on_conflict, slug=slug)


@mcp.tool()
def skill_edit(slug: str, description: str | None = None,
               body: str | None = None, scope: str = "", *, project_dir: str) -> dict:
    """Edit an existing skill's description and/or body. Spec 006."""
    if not _project_cwd(project_dir):
        return {"ok": False, "slug": slug, "error": f"invalid project_dir {project_dir!r}"}
    args = ["edit", slug]
    if description is not None:
        args += ["--desc", description]
    if scope:
        args += ["--scope", scope]
    if body is not None:
        args += ["--file", "-"]
    result = _sb_skill(*args, project_dir=project_dir, body=body)
    if not result["ok"]:
        return _error_payload(result, slug)
    record = _selected_record(slug, project_dir, scope=scope, include_disabled=True)
    payload = {"ok": True, "action": "updated", "slug": slug}
    if record:
        payload.update(_record_payload(slug, record))
    return payload


@mcp.tool()
def skill_delete(slug: str, scope: str = "", *, project_dir: str) -> dict:
    """Delete a skill (optionally restricted to a scope). Spec 006."""
    if not _project_cwd(project_dir):
        return {"ok": False, "slug": slug, "error": f"invalid project_dir {project_dir!r}"}
    before = _selected_record(slug, project_dir, scope=scope, include_disabled=True)
    args = ["delete", slug]
    if scope:
        args += ["--scope", scope]
    result = _sb_skill(*args, project_dir=project_dir)
    if not result["ok"]:
        return _error_payload(result, slug)
    payload = {"ok": True, "action": "deleted", "slug": slug}
    if before:
        payload.update(_record_payload(slug, before))
    return payload
