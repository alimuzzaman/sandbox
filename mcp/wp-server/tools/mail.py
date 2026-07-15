from __future__ import annotations
import json
import os
import shlex
import subprocess
from pathlib import Path
import httpx
from mcp.server.fastmcp import FastMCP
import re as _re



from app import _mailpit_url, _project_instance, _safe_json, mcp



@mcp.tool()
def mail_list(limit: int = 20, *, project_dir: str, label: str | None = None) -> dict:
    """List the most recent messages caught by Mailpit (test SMTP)."""
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    base = _mailpit_url(inst).rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v1/messages",
                      params={"limit": limit}, timeout=10.0)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}

@mcp.tool()
def mail_get(message_id: str, *, project_dir: str, label: str | None = None) -> dict:
    """Get a single message from Mailpit (headers, text, html)."""
    inst, err = _project_instance(project_dir, label)
    if err:
        return err
    base = _mailpit_url(inst).rstrip("/")
    try:
        r = httpx.get(f"{base}/api/v1/message/{message_id}", timeout=10.0)
        return {"ok": r.is_success, "status": r.status_code,
                "body": _safe_json(r.text)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
