from __future__ import annotations

from app import SANDBOX_ROOT, _run_sandbox_json, mcp


def _capability_error(project_dir: str, capability: str):
    """Keep legacy isolated app fakes import-compatible during migration."""
    try:
        from app import _require_project_capability
    except ImportError:
        return None
    return _require_project_capability(project_dir, None, capability)


def _plugin_check_error(message: str, *, action: str = "check",
                        code: int | None = None) -> dict:
    result = {
        "ok": False,
        "action": action,
        "plugin_slug": None,
        "errors": 0,
        "warnings": 0,
        "baseline_total": 0,
        "new_count": 0,
        "violations": [],
        "report_path": None,
        "error": message,
    }
    if code is not None:
        result["code"] = code
    return result


@mcp.tool()
def run_plugin_check(project_dir: str, update: bool = False) -> dict:
    """Run WordPress.org's official Plugin Check against a project's configured
    plugin, gated by a committed baseline — only NEW ERROR-level findings beyond
    the baseline fail the run. See docs/plugin-check.md, specs/013-plugin-check/.

    Which plugin to check is always the project's own resolved slug (its
    sandbox.config.json's top-level `slug`, or the project directory name -- the
    same resolution legacy `plugins: ["."]` self-entries already use). There is
    no `pluginCheck.slug` override; Plugin Check is intentionally a self-check
    tool. WARNING-level findings are included in the result and the rendered
    report for visibility but never gate the run.

    project_dir: the plugin project to check.
    update: rewrite the baseline to match current findings exactly, instead of
      gating against it (use after fixing findings, to tighten the baseline).

    Returns {ok, action, plugin_slug, errors, warnings, baseline_total, new_count,
    violations:[{key, current, baseline, delta}], report_path, error}. `ok` is
    true when the gate passes (or `update` succeeds); `violations` is only
    populated on a gate failure.
    """
    capability_error = _capability_error(project_dir, "wordpress.cli")
    if capability_error:
        return capability_error
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "plugin-check", "--project-dir", project_dir, "--json"]
    if update:
        cmd.append("--update")
    res = _run_sandbox_json(cmd, 300)
    if res["timed_out"]:
        return _plugin_check_error(
            "run_plugin_check timed out after 300s",
            action="update" if update else "check",
        )
    result = res["payload"]
    if isinstance(result, dict) and "plugin_slug" in result:
        return result
    return _plugin_check_error(
        (res["stderr"] or res["stdout"] or "plugin check failed").strip()[:2000],
        action="update" if update else "check",
        code=res["returncode"],
    )
