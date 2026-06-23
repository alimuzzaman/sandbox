from __future__ import annotations
import json
import subprocess

from app import *  # noqa: F401,F403


def _sb(inst: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(SANDBOX_ROOT / "sb"), "--instance", inst, *args],
                          capture_output=True, text=True, cwd=str(SANDBOX_ROOT))


@mcp.tool()
def qm_capture(url: str = "/", collectors: list[str] | None = None, *, project_dir: str) -> dict:
    """Capture Query Monitor data for a real request to `url` as JSON (spec 007).
    Auto-activates QM, fires the request, returns the parsed collectors (db_queries,
    php_errors, timing, http, …; `hooks` excluded). Optional `collectors` filter.
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    res = _sb(inst, "qm", url)
    line = ""
    for ln in reversed((res.stdout or "").splitlines()):
        if ln.strip().startswith("{"):
            line = ln.strip()
            break
    if not line:
        return {"ok": False, "error": "no qm.jsonl line produced",
                "output": ((res.stdout or "") + (res.stderr or ""))[-1500:]}
    try:
        rec = json.loads(line)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"bad JSON ({e})"}
    data = rec.get("data", {})
    if collectors:
        data = {k: v for k, v in data.items() if k in collectors}
    return {"ok": True, "url": rec.get("url"), "data": data}


@mcp.tool()
def xdebug(action: str = "status", *, project_dir: str) -> dict:
    """Toggle/inspect Xdebug for the instance (spec 007). action: on|off|status.
    Docker toggles the container ini; herd reports status + guidance (shared host PHP).
    """
    if action not in ("on", "off", "status"):
        return {"ok": False, "error": "action must be on|off|status"}
    inst, err = _project_instance(project_dir)
    if err:
        return err
    res = _sb(inst, "xdebug", action)
    return {"ok": res.returncode == 0,
            "output": ((res.stdout or "") + (res.stderr or "")).strip()}
