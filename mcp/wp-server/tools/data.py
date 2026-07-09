from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import *  # noqa: F401,F403



@mcp.tool()
def db_query(sql: str, mutate: bool = False, *, project_dir: str, label: str | None = None) -> dict:
    """Run a SQL query against the WP database.

    Reads (SELECT/SHOW/DESCRIBE/EXPLAIN) run freely.
    Writes (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/TRUNCATE/REPLACE) require
    mutate=true — an explicit acknowledgement that this changes data.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    head = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
    reads = {"SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "WITH"}
    writes = {"INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER",
              "CREATE", "DROP", "TRUNCATE", "GRANT", "REVOKE", "SET"}
    if head in writes and not mutate:
        return {
            "ok": False,
            "error": f"refused: {head} requires mutate=true (writes the DB)",
        }
    if head not in reads and head not in writes:
        return {"ok": False, "error": f"unrecognized statement type: {head!r}"}
    return _wpcli(["db", "query", sql], instance=inst)

@mcp.tool()
def import_content(seed_file: str, authors: str = "create",
                   *, project_dir: str, label: str | None = None) -> dict:
    """Import a WXR XML from runtime/seeds/. Pass just the filename."""
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    # Containers mount runtime/seeds at /seeds; herd reads the host path.
    seed = (str(SANDBOX_ROOT / "runtime" / "seeds" / seed_file)
            if _is_herd(inst) else f"/seeds/{seed_file}")
    return _wpcli(["import", seed, f"--authors={authors}"],
                  instance=inst, timeout=180)


@mcp.tool()
def wp_reset(confirm: bool = False, rebaseline: bool = False, *, project_dir: str, label: str | None = None) -> dict:
    """Reset the instance DB to the post-install @install baseline (spec 008) — a fast
    in-place rollback (keeps uploads/containers/ports). Requires confirm=true (it
    drops the current DB). rebaseline=true re-captures the baseline from the current
    DB instead of restoring.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    if rebaseline:
        res = subprocess.run([str(SANDBOX_ROOT / "sb"), "--instance", inst, "reset", "--rebaseline"],
                             capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
        return {"ok": res.returncode == 0, "output": ((res.stdout or "") + (res.stderr or "")).strip()}
    if not confirm:
        return {"ok": False, "error": "wp_reset drops the DB — pass confirm=true"}
    res = subprocess.run([str(SANDBOX_ROOT / "sb"), "--instance", inst, "reset", "--yes"],
                         capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
    return {"ok": res.returncode == 0, "output": ((res.stdout or "") + (res.stderr or "")).strip()}
