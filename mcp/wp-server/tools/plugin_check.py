from __future__ import annotations
import json
import subprocess

from app import *  # noqa: F401,F403


@mcp.tool()
def run_plugin_check(project_dir: str, update: bool = False) -> dict:
    """Run WordPress.org's official Plugin Check against a project's configured
    plugin, gated by a committed baseline — only NEW ERROR-level findings beyond
    the baseline fail the run. See docs/plugin-check.md, specs/013-plugin-check/.

    Which plugin to check defaults to the project's own resolved slug (its
    sandbox.config.json's top-level `slug`, or the project directory name — the
    same resolution legacy `plugins: ["."]` self-entries already use); set
    `pluginCheck.slug` explicitly only to check a DIFFERENT plugin than the
    project's own. WARNING-level findings are included in the result and the
    rendered report for visibility but never gate the run.

    project_dir: the plugin project to check.
    update: rewrite the baseline to match current findings exactly, instead of
      gating against it (use after fixing findings, to tighten the baseline).

    Returns {ok, action, plugin_slug, errors, warnings, baseline_total, new_count,
    violations:[{key, current, baseline, delta}], report_path, error}. `ok` is
    true when the gate passes (or `update` succeeds); `violations` is only
    populated on a gate failure.
    """
    sb = SANDBOX_ROOT / "sb"
    cmd = [str(sb), "plugin-check", "--project-dir", project_dir, "--json"]
    if update:
        cmd.append("--update")
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(SANDBOX_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "run_plugin_check timed out after 300s"}
    lines = (res.stdout or "").strip().splitlines()
    result = _safe_json(lines[-1]) if lines else None
    if isinstance(result, dict) and "plugin_slug" in result:
        return result
    return {"ok": False, "code": res.returncode,
            "error": (res.stderr or res.stdout or "plugin check failed").strip()[:2000]}
