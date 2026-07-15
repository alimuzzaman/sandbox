"""Sandbox authorization dashboard routes, mounted by Hermes at this plugin namespace."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from authorization_core import AuthorizationError, approval_prompt, audit, expire, read_state, supersede_approved, view, write_state

router = APIRouter()
ROOT = Path(os.environ.get("SANDBOX_AUTHORIZATION_HOME", Path.home() / ".hermes" / "sandbox-authorizations"))
CONFIG = PLUGIN_ROOT / "sandbox-authorization-config.json"
try:
    _CONFIG = json.loads(CONFIG.read_text())
except (OSError, ValueError):
    _CONFIG = {}
STATE = Path(os.path.expandvars(str(_CONFIG.get("state_path", ROOT / "state.json")))).expanduser()
CATALOG = Path(os.path.expandvars(str(_CONFIG.get("catalog_path", ROOT / "catalog.json")))).expanduser()
CRON_JOBS = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "cron" / "jobs.json"
_ID = re.compile(r"^[0-9a-f]{16}$")


def _hermes_bin() -> str:
    candidates = (
        os.environ.get("HERMES_BIN"),
        _CONFIG.get("hermes_bin"),
        shutil.which("hermes"),
        str(Path.home() / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return "hermes"


HERMES = _hermes_bin()


def _actor(request: Request) -> str:
    if getattr(request.app.state, "auth_required", False):
        session = getattr(request.state, "session", None)
        value = getattr(session, "user_id", None)
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(403, "authenticated dashboard principal required")
        return value.strip()[:128]
    # Hermes's loopback auth middleware validates the injected session token
    # before any /api/plugins route reaches this handler. That mode has no
    # user principal, so preserve the distinct, intentionally generic actor.
    return "loopback-session"


def _catalog() -> dict[str, dict]:
    try:
        raw = json.loads(CATALOG.read_text())
        jobs = raw["jobs"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, "authorization catalog is not configured") from exc
    return {item["name"]: item for item in jobs if isinstance(item, dict) and item.get("enabled") and item.get("kind") == "agent" and isinstance(item.get("prompt"), str) and item["prompt"].strip()}


def _state() -> dict:
    state = read_state(STATE)
    expire(state)
    return state


def _failure(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc) if isinstance(exc, AuthorizationError) else "authorization operation failed")


def _cron_jobs() -> list[dict]:
    try:
        raw = json.loads(CRON_JOBS.read_text())
        jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    except (OSError, ValueError) as exc:
        raise HTTPException(502, "Hermes cron state is unavailable") from exc
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise HTTPException(502, "Hermes cron state is invalid")
    return jobs


@router.get("/health")
async def health():
    return {"plugin": "sandbox-authorizations", "version": "1.0.6", "catalog_configured": CATALOG.exists()}


@router.get("/requests")
async def list_requests(request: Request):
    _actor(request)
    state = _state()
    write_state(STATE, state)
    rows = [view(state, item) for item in state["authorizations"]["requests"].values()]
    rows.sort(key=lambda item: item["created_at"], reverse=True)
    return {"requests": rows}


@router.get("/requests/{request_id}")
async def show_request(request_id: str, request: Request):
    _actor(request)
    if not _ID.fullmatch(request_id):
        raise HTTPException(404, "authorization request was not found")
    state = _state()
    item = state["authorizations"]["requests"].get(request_id)
    if not item:
        raise HTTPException(404, "authorization request was not found")
    return {"request": view(state, item, True)}


@router.post("/requests/{request_id}/approve")
async def approve(request_id: str, request: Request):
    actor = _actor(request)
    try:
        body = await request.json()
        if body.get("confirm") is not True or not _ID.fullmatch(request_id):
            raise AuthorizationError("explicit confirmation is required")
        state = _state()
        item = state["authorizations"]["requests"].get(request_id)
        if not item or item.get("status") != "pending":
            raise AuthorizationError("authorization request is not pending")
        job = _catalog().get(item["job_name"])
        if not job:
            raise AuthorizationError("authorization job is not cataloged")
        jobs = _cron_jobs()
        matches = [job_row for job_row in jobs if job_row.get("name") == item["job_name"] and job_row.get("enabled")]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
            raise AuthorizationError("matching catalog cron job was not found")
        prompt = approval_prompt(job, item)
        prior = [value for value in state["authorizations"]["requests"].values()
                 if value.get("job_name") == item["job_name"] and value.get("status") == "approved"]
        rollback_prompt = job["prompt"] if not prior else approval_prompt(
            job, max(prior, key=lambda value: (str(value.get("approved_at") or ""), str(value.get("created_at") or ""), value["id"])))
        subprocess.run([HERMES, "cron", "edit", matches[0]["id"], "--prompt", prompt], text=True, capture_output=True, check=True, timeout=30)
        supersede_approved(state, item["job_name"], item["id"], actor)
        item["status"] = "approved"
        item["approved_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        audit(state, item, "approved", actor)
        try:
            write_state(STATE, state)
        except Exception:
            subprocess.run([HERMES, "cron", "edit", matches[0]["id"], "--prompt", rollback_prompt], text=True, capture_output=True, check=False, timeout=30)
            raise
        return {"request": view(state, item, True)}
    except (AuthorizationError, subprocess.SubprocessError, OSError, ValueError) as exc:
        raise _failure(exc) from exc
