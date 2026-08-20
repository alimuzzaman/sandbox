from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import (SANDBOX_ROOT, _is_herd, _project_instance,
                 _require_project_capability, _run_sandbox_json, _wpcli, mcp)


def _snapshot_identifier(name: str) -> str:
    """Return the CLI's safe snapshot slug without exposing caller text."""
    return _re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")


def _safe_cli_error(result: dict, fallback: str) -> str:
    """Map CLI diagnostics to a bounded, path/content-free error code."""
    text = ((result.get("stderr") or "") + "\n" +
            (result.get("stdout") or "")).lower()
    markers = (
        ("requires --yes", "confirmation_required"),
        ("requires confirm=true", "confirmation_required"),
        ("reserved for the install baseline", "reserved_snapshot"),
        ("protected install baseline", "reserved_snapshot"),
        ("exists", "snapshot_exists"),
        ("no snapshot", "snapshot_not_found"),
        ("no @install baseline", "baseline_missing"),
        ("unsupported", "unsupported"),
    )
    for marker, code in markers:
        if marker in text:
            return code
    return fallback



@mcp.tool()
def db_query(sql: str, mutate: bool = False, *, project_dir: str, label: str | None = None) -> dict:
    """Run a SQL query against the WP database.

    Reads (SELECT/SHOW/DESCRIBE/EXPLAIN) run freely.
    Writes (INSERT/UPDATE/DELETE/ALTER/CREATE/DROP/TRUNCATE/REPLACE) require
    mutate=true — an explicit acknowledgement that this changes data.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.database")
    if capability_error:
        return capability_error
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
    capability_error = _require_project_capability(project_dir, label, "wordpress.cli")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    # Containers mount runtime/seeds at /seeds; herd reads the host path.
    seed = (str(SANDBOX_ROOT / "runtime" / "seeds" / seed_file)
            if _is_herd(inst) else f"/seeds/{seed_file}")
    return _wpcli(["import", seed, f"--authors={authors}"],
                  instance=inst, timeout=180)


@mcp.tool()
def snapshot(name: str, db_only: bool = False, force: bool = False, *,
             project_dir: str, label: str | None = None) -> dict:
    """Capture a named WordPress snapshot.

    Set db_only=true to export only the database and leave uploads out of the
    snapshot. force=true intentionally replaces an existing snapshot of the
    same name. project_dir: the plugin project to target (call
    ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.snapshot")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    safe_name = _snapshot_identifier(name)
    if not safe_name:
        return {"ok": False, "error": "snapshot name is required"}
    command = [str(SANDBOX_ROOT / "sb"), "--instance", inst, "snapshot", name]
    if db_only:
        command.append("--db-only")
    if force:
        command.append("--force")
    result = _run_sandbox_json(command, 300)
    if result["timed_out"]:
        return {"ok": False, "error": "snapshot timed out after 300s"}
    if result["returncode"] == 0:
        # Keep the MCP boundary to safe identifiers and bounded outcome data;
        # CLI progress may contain host paths or command lines and is not API
        # payload.  ``safe_name`` mirrors the CLI slugification for callers.
        return {"ok": True, "instance": inst, "snapshot": safe_name,
                "mode": "db-only" if db_only else "full", "forced": bool(force)}
    return {"ok": False, "error": _safe_cli_error(result, "snapshot_failed")}


@mcp.tool()
def wp_reset(confirm: bool = False, rebaseline: bool = False, *, project_dir: str, label: str | None = None) -> dict:
    """Reset the instance DB to the post-install @install baseline (spec 008) — a fast
    in-place rollback (keeps uploads/containers/ports). Requires confirm=true (it
    drops the current DB). rebaseline=true re-captures the baseline from the current
    DB instead of restoring.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    capability_error = _require_project_capability(project_dir, label, "wordpress.reset")
    if capability_error:
        return capability_error
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    if rebaseline:
        res = subprocess.run([str(SANDBOX_ROOT / "sb"), "--instance", inst, "reset", "--rebaseline"],
                             capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
        if res.returncode == 0:
            return {"ok": True, "instance": inst, "operation": "reset",
                    "rebaseline": True, "confirmed": False}
        return {"ok": False,
                "error": _safe_cli_error({"stdout": res.stdout, "stderr": res.stderr},
                                          "reset_failed")}
    if not confirm:
        return {"ok": False, "error": "wp_reset drops the DB — pass confirm=true"}
    res = subprocess.run([str(SANDBOX_ROOT / "sb"), "--instance", inst, "reset", "--yes"],
                         capture_output=True, text=True, cwd=str(SANDBOX_ROOT))
    if res.returncode == 0:
        return {"ok": True, "instance": inst, "operation": "reset",
                "rebaseline": False, "confirmed": True}
    return {"ok": False,
            "error": _safe_cli_error({"stdout": res.stdout, "stderr": res.stderr},
                                      "reset_failed")}
