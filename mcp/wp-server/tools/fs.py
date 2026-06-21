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
def tail_log(lines: int = 100, *, project_dir: str) -> dict:
    """Tail wp-content/debug.log for the project's instance."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    log_path = _log_path(inst)
    if not log_path.exists():
        return {"ok": True, "lines": [], "note": "debug.log not yet created",
                "path": str(log_path)}
    try:
        data = log_path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        return {"ok": True, "lines": text.splitlines()[-lines:],
                "path": str(log_path)}
    except OSError as e:
        return {"ok": False, "error": str(e)}

@mcp.tool()
def fs_read(path: str, max_bytes: int = 200_000, *, project_dir: str) -> dict:
    """Read a file under the project instance's WordPress install.

    path is relative to the WP root — e.g. 'wp-content/themes/my-theme/style.css'.
    Refuses paths that escape the WP root.
    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    wp_root = _wp_root(inst)
    target = _safe_resolve(path, wp_root)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if not target.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    data = target.read_bytes()[:max_bytes]
    try:
        return {"ok": True, "path": str(target.relative_to(wp_root)),
                "size": target.stat().st_size,
                "truncated": target.stat().st_size > max_bytes,
                "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"ok": True, "path": str(target.relative_to(wp_root)),
                "size": target.stat().st_size, "binary": True,
                "note": "binary file; use wp_exec to inspect"}

@mcp.tool()
def fs_write(path: str, content: str, create_dirs: bool = True,
             *, project_dir: str) -> dict:
    """Write a file under the project instance's WordPress install. Creates
    parent dirs by default. Refuses paths that escape WP root. Returns bytes.

    project_dir: the plugin project to target (call ensure_instance first).
    """
    inst, err = _project_instance(project_dir)
    if err:
        return err
    wp_root = _wp_root(inst)
    target = _safe_resolve(path, wp_root)
    if target is None:
        return {"ok": False, "error": f"path escapes WP root: {path!r}"}
    if create_dirs:
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"ok": True, "path": str(target.relative_to(wp_root)),
            "bytes": len(content.encode("utf-8"))}

@mcp.tool()
def fs_list(path: str = "", depth: int = 1, *, project_dir: str) -> dict:
    """List files under the project instance's WP install. depth=1 is shallow."""
    inst, err = _project_instance(project_dir)
    if err:
        return err
    wp_root = _wp_root(inst)
    target = _safe_resolve(path or ".", wp_root)
    if target is None or not target.exists():
        return {"ok": False, "error": f"not found or escapes root: {path!r}"}
    out = []
    base_depth = len(target.parts)
    for p in target.rglob("*"):
        if len(p.parts) - base_depth > depth:
            continue
        out.append({
            "path": str(p.relative_to(wp_root)),
            "type": "dir" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else None,
        })
        if len(out) >= 500:
            out.append({"note": "truncated at 500 entries"})
            break
    return {"ok": True, "root": str(target.relative_to(wp_root)), "entries": out}
