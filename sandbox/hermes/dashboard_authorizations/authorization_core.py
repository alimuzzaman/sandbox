"""Portable, dependency-free authorization state for the Hermes dashboard plugin.

This module is deliberately copied with the dashboard plugin at installation
time.  Keep its input validation and state format compatible with Sandbox's
control-plane authorization records; it must never accept arbitrary commands.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_ID = re.compile(r"^[0-9a-f]{16}$")
_SCOPE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SECRET = re.compile(r"(?i)(?:\b(?:token|password|secret|authorization|cookie|session)\b\s*[:=]\s*\S+|github_pat_[a-z0-9_]{20,}|gh[pousr]_[a-z0-9]{20,}|sk-(?:proj-)?[a-z0-9_-]{20,}|BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY)")
MAX_REQUESTS = 100
MAX_AUDIT = 200


class AuthorizationError(ValueError):
    """A bounded, display-safe authorization error."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def valid_scope(value: str) -> str:
    value = (value or "").strip().lower()
    if not _SCOPE.fullmatch(value):
        raise AuthorizationError("scope must be a lowercase slug")
    return value


def valid_origin(value: str) -> str:
    parsed = urlsplit((value or "").strip())
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise AuthorizationError("replay origin must be an HTTPS origin without credentials or path")
    return urlunsplit(("https", parsed.netloc.lower(), "", "", ""))


def valid_reason(value: str) -> str:
    value = (value or "").strip()
    if not 1 <= len(value) <= 500 or "\n" in value or _SECRET.search(value):
        raise AuthorizationError("rationale must be 1-500 non-secret characters")
    return value


def fingerprint(job_name: str, scope: str, origin: str, rationale: str) -> str:
    payload = json.dumps({"job_name": job_name, "scope": scope, "replay_origin": origin,
                          "reason": rationale}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def new_state() -> dict:
    return {"schema_version": 2, "authorizations": {"requests": {}, "audit": []}}


def normalize_state(state: object) -> dict:
    if not isinstance(state, dict) or state.get("schema_version", 2) != 2:
        raise AuthorizationError("unsupported authorization state")
    state.setdefault("schema_version", 2)
    auth = state.setdefault("authorizations", {"requests": {}, "audit": []})
    if not isinstance(auth, dict) or not isinstance(auth.setdefault("requests", {}), dict) or not isinstance(auth.setdefault("audit", []), list):
        raise AuthorizationError("invalid authorization state")
    return state


def read_state(path: Path) -> dict:
    if not path.exists():
        return new_state()
    try:
        return normalize_state(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorizationError("authorization state is invalid") from exc


def write_state(path: Path, state: dict) -> None:
    normalize_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
                json.dump(state, handle, sort_keys=True, indent=2)
                handle.write("\n")
                temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            temporary.replace(path)
            path.chmod(0o600)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def audit(state: dict, request: dict, event: str, actor: str | None = None) -> None:
    item = {"request_id": request["id"], "event": event, "at": now().isoformat(), "fingerprint": request["fingerprint"]}
    if actor:
        item["actor"] = actor[:128]
    state["authorizations"]["audit"].append(item)
    del state["authorizations"]["audit"][:-MAX_AUDIT]


def expire(state: dict) -> None:
    for request in state["authorizations"]["requests"].values():
        if request.get("status") == "pending" and datetime.fromisoformat(request["expires_at"]) <= now():
            request["status"] = "expired"
            audit(state, request, "expired")


def view(state: dict, request: dict, detail: bool = False) -> dict:
    keys = ("id", "job_name", "scope", "replay_origin", "rationale", "blocker", "source_fingerprint",
            "fingerprint", "status", "created_at", "expires_at", "approved_at")
    result = {key: request[key] for key in keys if key in request}
    if detail:
        result["audit"] = [item for item in state["authorizations"]["audit"] if item["request_id"] == request["id"]]
    return result


def create_request(state: dict, catalog: dict[str, dict], job_name: str, scope: str, origin: str,
                   rationale: str, expiry_minutes: int, actor: str) -> dict:
    if not isinstance(expiry_minutes, int) or not 1 <= expiry_minutes <= 1440:
        raise AuthorizationError("expiry must be between 1 and 1440 minutes")
    job = catalog.get((job_name or "").strip())
    if not job:
        raise AuthorizationError("job is not an enabled authorization catalog entry")
    scope, origin, rationale = valid_scope(scope), valid_origin(origin), valid_reason(rationale)
    expire(state)
    requests = state["authorizations"]["requests"]
    if len(requests) >= MAX_REQUESTS:
        raise AuthorizationError("authorization request limit reached")
    for item in requests.values():
        if item.get("job_name") == job_name and item.get("status") in {"pending", "review_required"}:
            item["status"] = "superseded"
            audit(state, item, "superseded", actor)
    created = now()
    request = {"id": secrets.token_hex(8), "job_name": job_name, "scope": scope, "replay_origin": origin,
               "rationale": rationale, "fingerprint": fingerprint(job_name, scope, origin, rationale),
               "status": "pending", "created_at": created.isoformat(),
               "expires_at": (created + timedelta(minutes=expiry_minutes)).isoformat()}
    requests[request["id"]] = request
    audit(state, request, "requested", actor)
    return request
