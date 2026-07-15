"""Sandbox authorization dashboard routes, mounted by Hermes at this plugin namespace."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from authorization_core import AuthorizationError, create_request, expire, read_state, view, write_state

router = APIRouter()
ROOT = Path(os.environ.get("SANDBOX_AUTHORIZATION_HOME", Path.home() / ".hermes" / "sandbox-authorizations"))
CONFIG = Path(__file__).resolve().parents[1] / "sandbox-authorization-config.json"
try:
    _CONFIG = json.loads(CONFIG.read_text())
except (OSError, ValueError):
    _CONFIG = {}
STATE = Path(os.path.expandvars(str(_CONFIG.get("state_path", ROOT / "state.json")))).expanduser()
CATALOG = Path(os.path.expandvars(str(_CONFIG.get("catalog_path", ROOT / "catalog.json")))).expanduser()
HERMES = os.environ.get("HERMES_BIN", str(Path.home() / ".local" / "bin" / "hermes"))
_ID = re.compile(r"^[0-9a-f]{16}$")
_REVIEW_REQUIRED = re.compile(r"^REVIEW_REQUIRED\s*(?:[—:-]\s*)?(.+)$", re.MULTILINE)


def _actor(request: Request) -> str:
    principal = getattr(request.state, "principal", None) or getattr(request.state, "user", None)
    value = getattr(principal, "user_id", None) or getattr(principal, "id", None) or (principal if isinstance(principal, str) else None)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(403, "authenticated dashboard principal required")
    return value.strip()[:128]


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


def _cron_json(*args: str) -> dict:
    completed = subprocess.run([HERMES, "cron", *args, "--json"], text=True, capture_output=True, check=False, timeout=30)
    if completed.returncode:
        raise HTTPException(502, "Hermes cron command failed")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HTTPException(502, "Hermes cron returned invalid data") from exc


@router.get("/health")
async def health():
    return {"plugin": "sandbox-authorizations", "version": "1.0.0", "catalog_configured": CATALOG.exists()}


@router.get("/requests")
async def list_requests():
    state = _state()
    write_state(STATE, state)
    rows = [view(state, item) for item in state["authorizations"]["requests"].values()]
    rows.sort(key=lambda item: item["created_at"], reverse=True)
    return {"requests": rows}


@router.get("/requests/{request_id}")
async def show_request(request_id: str):
    if not _ID.fullmatch(request_id):
        raise HTTPException(404, "authorization request was not found")
    state = _state()
    item = state["authorizations"]["requests"].get(request_id)
    if not item:
        raise HTTPException(404, "authorization request was not found")
    return {"request": view(state, item, True)}


@router.post("/requests")
async def request_authorization(request: Request):
    actor = _actor(request)
    try:
        body = await request.json()
        state = _state()
        item = create_request(state, _catalog(), body.get("job_name"), body.get("scope"), body.get("replay_origin"), body.get("rationale"), body.get("expires_in_minutes", 1440), actor)
        write_state(STATE, state)
        return {"request": view(state, item, True)}
    except (AuthorizationError, TypeError, ValueError) as exc:
        raise _failure(exc) from exc


@router.post("/sync")
async def sync(request: Request):
    actor = _actor(request)
    state, catalog, created = _state(), _catalog(), []
    from authorization_core import audit, now
    for job in _cron_json("list").get("jobs", []):
        if job.get("name") not in catalog or not job.get("enabled") or not isinstance(job.get("id"), str):
            continue
        output = _cron_json("output", job["id"])
        text = str(output.get("output") or output.get("data", {}).get("output") or "")
        match = _REVIEW_REQUIRED.search(text)
        if not match:
            continue
        blocker = match.group(1).strip()[:500]
        if not blocker or re.search(r"(?i)\b(?:token|password|secret)\b\s*[:=]", blocker):
            continue
        source = __import__("hashlib").sha256((job["id"] + "\n" + blocker).encode()).hexdigest()
        if any(item.get("source_fingerprint") == source for item in state["authorizations"]["requests"].values()):
            continue
        for item in state["authorizations"]["requests"].values():
            if item.get("job_name") == job["name"] and item.get("status") == "review_required":
                item["status"] = "superseded"
                audit(state, item, "superseded", actor)
        item = {"id": __import__("secrets").token_hex(8), "job_name": job["name"], "blocker": blocker,
                "source_fingerprint": source, "fingerprint": source, "status": "review_required",
                "created_at": now().isoformat(), "expires_at": (now() + __import__("datetime").timedelta(days=7)).isoformat()}
        state["authorizations"]["requests"][item["id"]] = item
        audit(state, item, "review_required", actor)
        created.append(view(state, item, True))
    write_state(STATE, state)
    return {"created_count": len(created), "created": created}


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
        jobs = _cron_json("list").get("jobs", [])
        matches = [job_row for job_row in jobs if job_row.get("name") == item["job_name"] and job_row.get("enabled")]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
            raise AuthorizationError("matching catalog cron job was not found")
        prompt = job["prompt"].rstrip() + "\n\nSANDBOX AUTHORIZATION: This is the sole approved exception for this run. Request %s authorizes only scope %s against replay origin %s. Rationale: %s. Do not broaden this authorization or perform any other protected action.\n" % (item["id"], item["scope"], item["replay_origin"], item["rationale"])
        subprocess.run([HERMES, "cron", "edit", matches[0]["id"], "--prompt", prompt], text=True, capture_output=True, check=True, timeout=30)
        item["status"] = "approved"
        item["approved_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        from authorization_core import audit
        audit(state, item, "approved", actor)
        try:
            write_state(STATE, state)
        except Exception:
            subprocess.run([HERMES, "cron", "edit", matches[0]["id"], "--prompt", job["prompt"]], text=True, capture_output=True, check=False, timeout=30)
            raise
        return {"request": view(state, item, True)}
    except (AuthorizationError, subprocess.SubprocessError, OSError, ValueError) as exc:
        raise _failure(exc) from exc
