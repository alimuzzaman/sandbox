"""Hermes control-plane helpers for a configured Sandbox remote.

The module deliberately keeps Hermes host-native: every remote command runs as
the existing Sandbox account, uses that account's ``SANDBOX_HOME``, and invokes
the remote ``sb`` executable directly.  It never creates a second WordPress
registry or exposes a new network listener.
"""
from __future__ import annotations

import base64
import copy
import io
import hashlib
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import pathlib
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

import sandbox.core._remote as remote
import sandbox.core._cloudflare as cloudflare_zone
import sandbox.core._cloudflare_access as cloudflare_access
import sandbox.core._cloudflare_tunnel as cloudflare_tunnel
from sandbox.core._config import _local_yaml
from sandbox.core._secrets import resolve_secret
from sandbox.hermes.scheduler import (
    SCHEDULE_GUARD,
    audit_jobs,
    catalog_fingerprint,
    classify_job,
    effective_job_status,
    load_catalog,
    reconciliation_plan,
    render_entry,
    scheduled_route,
    scripts_path,
)


SUPPORTED_TAG = "v2026.7.7.2"
SUPPORTED_COMMIT = "9de9c25f620ff7f1ce0fd5457d596052d5159596"
HERMES_REPOSITORY_URL = "https://github.com/NousResearch/hermes-agent.git"
HERMES_DEFAULT_PROVIDER = "openai-codex"
HERMES_DEFAULT_MODEL = "gpt-5.3-codex-spark"
HERMES_ROUTING_POLICY_START = "<!-- SANDBOX_ROUTING_BEGIN -->"
HERMES_ROUTING_POLICY_END = "<!-- SANDBOX_ROUTING_END -->"
HERMES_STATE_REPO_KEY = "hermes_state_repo"
HERMES_DRIVE_DESTINATION_KEY = "hermes_drive_destination"
HERMES_RELEASE_SIGNER = "teknium1"
# Pinned after verifying the upstream release tag signer fingerprint
# SHA256:x9xNOpeJhoEAY2gWhmWHZROC3QF3VjOEbmNo9vQ8y2A.
HERMES_RELEASE_SIGNER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPpWPAE2WMbZ0fAZ8xsqiTIJqA28qDBfGru8kPrpNyUb"
GATEWAY_UNIT = "hermes-gateway-sandbox.service"
GATEWAY_STABILITY_SECONDS = 120
GATEWAY_STABILITY_INTERVAL = 10
GATEWAY_STABILITY_TIMEOUT_MARGIN = 30
GATEWAY_STABILITY_MAX_SAMPLES = 48
_MANAGED_CATALOG_WORKTREES = {
    "sandbox-spec-backlog": ("sandbox", "hermes/autonomous-backlog"),
    "lenzora-todo-task": ("lenzora", "hermes/lenzora-todo-task"),
}
_LENZORA_SPECKIT_SKILLS = (
    "speckit-specify", "speckit-clarify", "speckit-plan", "speckit-tasks", "speckit-analyze",
    "speckit-implement",
)
DASHBOARD_UNIT = "hermes-dashboard-sandbox.service"
DASHBOARD_LOOPBACK_HOST = "127.0.0.1"
DASHBOARD_PORT = 9119
DASHBOARD_AUTHORIZATION_PLUGIN = "sandbox-authorizations"
DASHBOARD_AUTHORIZATION_VERSION = "1.0.6"
PUBLIC_DASHBOARD_FQDN = "hermes.asb.bd"
PUBLIC_PROXY_PORT = 9120
PUBLIC_TUNNEL_UNIT = "hermes-cloudflared.service"
PUBLIC_CADDY_FRAGMENT = "/etc/caddy/conf.d/hermes-dashboard.caddy"
PUBLIC_BASIC_FRAGMENT = "/etc/caddy/conf.d/hermes-dashboard-basic.caddy"
PUBLIC_TUNNEL_TOKEN_FILE = "$HOME/.hermes/cloudflared-token"
PUBLIC_TUNNEL_TOKEN_UNIT_FILE = "%h/.hermes/cloudflared-token"
STATE_SCHEMA = 2
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_JOB_RE = re.compile(r"^[0-9a-f]{16}$")
_CRON_JOB_RE = re.compile(r"^[0-9a-f]{8,32}$")
_AUTH_REQUEST_RE = re.compile(r"^[0-9a-f]{16}$")
_AUTH_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_AUTH_MAX_REQUESTS = 100
_AUTH_MAX_AUDIT = 200
_REVIEW_REQUIRED_RE = re.compile(r"^REVIEW_REQUIRED\s*(?:[—:-]\s*)?(.+)$", re.MULTILINE)
_SECRET_RE = re.compile(r"(?i)\b(token|password|secret|authorization|cookie|session)\b\s*[=:]\s*(?:bearer\s+)?[^\s,;]+")
_BARE_SECRET_RE = re.compile(
    r"(?ix)(?:"
    r"github_pat_[a-z0-9_]{20,}|"
    r"gh[pousr]_[a-z0-9]{20,}|"
    r"sk-(?:proj-)?[a-z0-9_-]{20,}|"
    r"xox[baprs]-[a-z0-9-]{20,}|"
    r"ya29\.[a-z0-9._-]{20,}"
    r")"
)
_CREDENTIAL_PATTERN = (
    r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{24,}|"
    r"BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY|"
    r"(?:api[_-]?key|token|password|passphrase|secret|authorization)\s*[:=]\s*['\"]?\S{8,}|"
    r"authorization\s*:\s*bearer\s+\S+|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_CREDENTIAL_RE = re.compile(_CREDENTIAL_PATTERN, re.IGNORECASE)
_DEFAULT_POLICY = {"max_jobs": 2, "max_worktrees": 8, "min_free_disk_mb": 1024, "min_free_memory_mb": 512}
_BACKUP_RETENTION_COUNT = 10
_BACKUP_MIN_FREE_MB = 512
_COMPLETED_JOB_RETENTION_DAYS = 7
_V2_ACCEPTANCE_CHECKS = (
    "update_rollback",
    "backup_restore",
    "resource_rejection",
    "stale_reconciliation",
    "reboot_recovery",
)


class HermesError(RuntimeError):
    """A safe error returned by the CLI/MCP wrappers."""

    def __init__(self, message: str, code: str = "hermes_error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class _RemoteStateSnapshot(dict):
    """Normalized state plus the semantic digest observed before mutation."""
    def __init__(self, state: dict, digest: str) -> None:
        super().__init__(state)
        self.digest = digest


def _state_digest(state: object) -> str:
    payload = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _redact(value: str, entry: dict | None = None) -> str:
    text = remote.redact_ssh_connection(str(value or ""), entry)
    text = _SECRET_RE.sub(lambda m: m.group(1) + "=[redacted]", text)
    return _BARE_SECRET_RE.sub("[redacted]", text)


def _contains_credential(value: object) -> bool:
    """Recognize credential-like content before it can enter review output."""
    return bool(_CREDENTIAL_RE.search(str(value or "")))


def _redact_public(value):
    """Recursively protect result-envelope strings from remote output leaks."""
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_redact_public(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public(item) for item in value)
    if isinstance(value, dict):
        return {key: _redact_public(item) for key, item in value.items()}
    return value


def _backup_forbidden_source_path(path: str) -> bool:
    """Whether a tracked Hermes source path may contain runtime credentials.

    The source pack is opaque to archive inspection, so this policy evaluates
    every tracked path before packing. It deliberately permits source modules
    such as ``cookies.py`` while rejecting credential-bearing data filenames at
    any directory depth.
    """
    name = PurePosixPath(path).name.lower()
    if name == ".env.example":
        return False
    return (
        name == ".env"
        or name.startswith(".env.")
        or name == "auth.json"
        or name == "credentials"
        or re.fullmatch(r"credentials\.(json|ya?ml|toml|ini|txt)", name) is not None
        or name == "cookies"
        or re.fullmatch(r"cookies\.(json|ya?ml|toml|txt)", name) is not None
        or name.endswith((".pem", ".key"))
    )


def result(ok: bool, action: str, remote_name: str, *, version: str | None = None,
           commit: str | None = None, status: str | None = None,
           repo: str | None = None, path: str | None = None,
           job_id: str | None = None, data: dict | None = None,
           error: HermesError | None = None) -> dict:
    """Stable, JSON-safe envelope shared by CLI and MCP callers."""
    return {
        "ok": ok,
        "action": action,
        "remote": remote_name,
        "version": _redact_public(version),
        "commit": _redact_public(commit),
        "status": _redact_public(status),
        "repo": _redact_public(repo),
        "path": _redact_public(path),
        "job_id": _redact_public(job_id),
        "data": _redact_public(data or {}),
        "error": None if error is None else {
            "code": error.code,
            "message": _redact(str(error)),
            "retryable": error.retryable,
            "details": {},
        },
    }


def validate_repo_name(name: str) -> str:
    name = (name or "").strip()
    if not _REPO_NAME_RE.fullmatch(name) or name.startswith("."):
        raise HermesError("repository name must be a simple managed name", "invalid_repo_name")
    return name


def _valid_cron_job_id(job_id: str) -> str:
    value = (job_id or "").strip().lower()
    if not _CRON_JOB_RE.fullmatch(value):
        raise HermesError("invalid Hermes cron job id", "invalid_cron_job_id")
    return value


def validate_repo_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("git@") and ":" in value and "@" in value:
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
        raise HermesError("repository URL must use https or ssh", "invalid_repo_url")
    if parsed.username or parsed.password:
        raise HermesError("repository URL must not embed credentials", "credential_in_repo_url")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _validate_release_tag(tag: str) -> str:
    tag = (tag or "").strip()
    if not tag.startswith("v") or tag in {"main", "master", "latest"}:
        raise HermesError("Hermes release must be an immutable version tag", "invalid_release")
    return tag


def validate_release(tag: str, commit: str) -> tuple[str, str]:
    tag = _validate_release_tag(tag)
    commit = (commit or "").strip().lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise HermesError("Hermes release requires a full 40-character commit", "invalid_commit")
    return tag, commit


def validate_gateway_allowlist(values: list[str] | None) -> list[str]:
    cleaned = [str(value).strip() for value in (values or []) if str(value).strip()]
    if not cleaned or any(value.lower() in {"*", "all", "allow-all"} for value in cleaned):
        raise HermesError("gateway requires a non-empty explicit allowlist", "unsafe_gateway_allowlist")
    return cleaned


def _require_remote(name: str) -> dict:
    try:
        remote.validate_remote_name(name)
    except ValueError as exc:
        raise HermesError(str(exc), "invalid_remote") from exc
    entry = remote.get_remote(name)
    if not entry:
        raise HermesError(f"no remote named '{name}'", "unknown_remote")
    if not entry.get("provisioned"):
        raise HermesError(f"remote '{name}' is not provisioned", "remote_not_provisioned")
    return entry


def validate_state_repo(value: str) -> str:
    """Accept only credential-free GitHub repository URLs."""
    value = (value or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.username or parsed.password:
        raise HermesError("state repository must be an HTTPS GitHub URL without credentials", "invalid_state_repo")
    path = parsed.path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", path):
        raise HermesError("state repository must be github.com/OWNER/REPOSITORY", "invalid_state_repo")
    return urlunsplit(("https", "github.com", "/" + path.removesuffix(".git") + ".git", "", ""))


def _ssh(entry: dict, command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return remote.ssh_run(entry, command, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HermesError(f"remote command timed out after {timeout} seconds", "remote_unavailable", True) from exc
    except (OSError, ValueError) as exc:
        raise HermesError(_redact(str(exc), entry)[:500], "remote_unavailable", True) from exc


def _ssh_stdin(entry: dict, command: str, data: bytes, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a remote command with sensitive input over SSH stdin, never argv."""
    try:
        return subprocess.run(remote.ssh_command_args(entry, command), input=data,
                              capture_output=True, text=False, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise HermesError(f"remote input command timed out after {timeout} seconds", "remote_unavailable", True) from exc
    except (OSError, ValueError) as exc:
        raise HermesError(_redact(str(exc), entry)[:500], "remote_unavailable", True) from exc


def _ssh_stdin_with_progress(entry: dict, command: str, data: bytes, timeout: int = 60) -> subprocess.CompletedProcess:
    """Relay sanitized remote stderr progress while preserving stdout for JSON results."""
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(remote.ssh_command_args(entry, command), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
        assert proc.stdin and proc.stdout and proc.stderr
        proc.stdin.write(data)
        proc.stdin.close()
        progress = []
        for raw in iter(proc.stderr.readline, b""):
            line = _redact(raw.decode(errors="replace"), entry)
            progress.append(line)
            sys.stderr.write(line)
            sys.stderr.flush()
        stdout = proc.stdout.read()
        returncode = proc.wait(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, returncode, stdout, "".join(progress).encode())
    except subprocess.TimeoutExpired as exc:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()
        raise HermesError(f"remote streaming command timed out after {timeout} seconds", "remote_unavailable", True) from exc
    except (OSError, ValueError) as exc:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()
        raise HermesError(_redact(str(exc), entry)[:500], "remote_unavailable", True) from exc


def _checked(entry: dict, command: str, timeout: int = 60, *, what: str) -> subprocess.CompletedProcess:
    res = _ssh(entry, command, timeout)
    if res.returncode != 0:
        detail = _redact(res.stderr or res.stdout or what, entry)[:1000]
        raise HermesError(f"{what}: {detail}", "remote_command_failed", True)
    return res


def _sandbox_home(entry: dict) -> str:
    try:
        return remote.resolve_sandbox_home(entry)
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise HermesError(_redact(str(exc), entry), "sandbox_home_unavailable", True) from exc


def _paths(entry: dict) -> dict:
    sandbox_home = _sandbox_home(entry)
    return {
        "sandbox_home": sandbox_home,
        "sb": f"{sandbox_home}/sb-src/sb",
        "repo_root": f"{sandbox_home}/hermes-repos",
        "state": f"{sandbox_home}/runtime/hermes.json",
        "jobs": f"{sandbox_home}/runtime/hermes-jobs",
        "locks": f"{sandbox_home}/runtime/hermes-locks",
        "worktrees": f"{sandbox_home}/runtime/hermes-worktrees",
        "hermes_home": "$HOME/.hermes",
        "launcher": "$HOME/.local/bin/hermes",
        "policy": "$HOME/.hermes/sandbox-resource-policy.json",
    }


def read_state(path: Path) -> dict:
    if not path.exists():
        return _new_state()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HermesError(f"invalid Hermes state: {exc}", "invalid_state") from exc
    return _normalize_state(data)


def _new_state() -> dict:
    return {"schema_version": STATE_SCHEMA, "repositories": {}, "sessions": {}, "gates": {},
            "authorizations": {"requests": {}, "audit": []}}


def _normalize_state(data: dict) -> dict:
    """Migrate the original unversioned state shape before using it."""
    if not isinstance(data, dict):
        raise HermesError("unsupported Hermes state schema", "invalid_state")
    schema = data.get("schema_version", 0)
    if schema in (0, 1):
        data = dict(data)
        data["schema_version"] = STATE_SCHEMA
    elif schema != STATE_SCHEMA:
        raise HermesError("unsupported Hermes state schema", "invalid_state")
    for key in ("repositories", "sessions", "gates"):
        data.setdefault(key, {})
        if not isinstance(data[key], dict):
            raise HermesError("invalid Hermes state collection", "invalid_state")
    authorizations = data.setdefault("authorizations", {"requests": {}, "audit": []})
    if not isinstance(authorizations, dict):
        raise HermesError("invalid Hermes authorization collection", "invalid_state")
    authorizations.setdefault("requests", {})
    authorizations.setdefault("audit", [])
    if (not isinstance(authorizations["requests"], dict)
            or not isinstance(authorizations["audit"], list)
            or not all(isinstance(request, dict) for request in authorizations["requests"].values())
            or not all(isinstance(event, dict) for event in authorizations["audit"])):
        raise HermesError("invalid Hermes authorization record", "invalid_state")
    if any(not isinstance(request_id, str) for request_id in authorizations["requests"]):
        raise HermesError("invalid Hermes authorization collection", "invalid_state")
    request_fields = {"id", "status", "created_at", "expires_at", "fingerprint"}
    audit_fields = {"request_id", "event", "at", "fingerprint"}
    if (any(not request_fields <= set(request) for request in authorizations["requests"].values())
            or any(not audit_fields <= set(event) for event in authorizations["audit"])):
        raise HermesError("invalid Hermes authorization record", "invalid_state")
    valid_statuses = {"pending", "approved", "expired", "superseded", "review_required"}
    for request_id, request in authorizations["requests"].items():
        if (request.get("id") != request_id or not _AUTH_REQUEST_RE.fullmatch(request_id) or
                not isinstance(request.get("status"), str) or request["status"] not in valid_statuses or
                any(not isinstance(request.get(field), str) for field in request_fields) or
                not re.fullmatch(r"[0-9a-f]{64}", request["fingerprint"])):
            raise HermesError("invalid Hermes authorization record", "invalid_state")
    for event in authorizations["audit"]:
        if (not isinstance(event["request_id"], str) or not _AUTH_REQUEST_RE.fullmatch(event["request_id"]) or
                not isinstance(event["event"], str) or not event["event"] or
                any(not isinstance(event.get(field), str) for field in audit_fields) or
                not re.fullmatch(r"[0-9a-f]{64}", event["fingerprint"])):
            raise HermesError("invalid Hermes authorization record", "invalid_state")
    return data


def _authorization_now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_authorization_id(value: str) -> str:
    value = value.strip().lower() if isinstance(value, str) else ""
    if not _AUTH_REQUEST_RE.fullmatch(value):
        raise HermesError("authorization request id is invalid", "invalid_authorization_id")
    return value


def _valid_authorization_scope(value: str) -> str:
    value = value.strip().lower() if isinstance(value, str) else ""
    if not _AUTH_SCOPE_RE.fullmatch(value):
        raise HermesError("authorization scope must be a lowercase slug", "invalid_authorization_scope")
    return value


def _valid_replay_origin(value: str) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise HermesError("replay origin contains unsafe control text", "invalid_replay_origin")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise HermesError("replay origin must be an HTTPS origin without credentials or path", "invalid_replay_origin")
    return urlunsplit(("https", parsed.netloc.lower(), "", "", ""))


def _valid_authorization_reason(value: str) -> str:
    value = value.strip() if isinstance(value, str) else ""
    if (not 1 <= len(value) <= 500 or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or _contains_credential(value)):
        raise HermesError("authorization reason must be 1-500 non-secret characters", "invalid_authorization_reason")
    return value


def _authorization_expiry(request: dict) -> datetime:
    try:
        expiry = datetime.fromisoformat(request["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HermesError("authorization request has an invalid expiry", "invalid_state") from exc
    if expiry.tzinfo is None:
        raise HermesError("authorization request has an invalid expiry", "invalid_state")
    return expiry


def _authorization_fingerprint(job_name: str, scope: str, replay_origin: str, reason: str) -> str:
    payload = json.dumps({"job_name": job_name, "scope": scope, "replay_origin": replay_origin,
                          "reason": reason}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _authorization_audit(state: dict, request: dict, event: str) -> None:
    audit = state["authorizations"]["audit"]
    audit.append({"request_id": request["id"], "event": event, "at": _authorization_now().isoformat(),
                  "fingerprint": request["fingerprint"]})
    del audit[:-_AUTH_MAX_AUDIT]


def _expire_authorizations(state: dict) -> None:
    now = _authorization_now()
    for request in state["authorizations"]["requests"].values():
        if request.get("status") == "pending" and _authorization_expiry(request) <= now:
            request["status"] = "expired"
            _authorization_audit(state, request, "expired")


def _supersede_approved_authorizations(state: dict, job_name: str, keep_id: str) -> None:
    for request in state["authorizations"]["requests"].values():
        if request.get("job_name") == job_name and request.get("status") == "approved" and request.get("id") != keep_id:
            request["status"] = "superseded"
            _authorization_audit(state, request, "superseded")


def _catalog_authorization_job(name: str, paths: dict) -> dict:
    for item in load_catalog()["jobs"]:
        if item.name == name and item.enabled and item.kind == "agent":
            return render_entry(item, paths)
    raise HermesError("authorization job must be an enabled catalog-managed agent", "invalid_authorization_job")


def _authorization_view(state: dict, request: dict, detail: bool) -> dict:
    keys = ("id", "job_name", "scope", "replay_origin", "rationale", "blocker", "source_fingerprint",
            "fingerprint", "status", "created_at", "expires_at", "approved_at")
    view = {key: request.get(key) for key in keys if request.get(key) is not None}
    if request.get("status") == "pending" and _authorization_expiry(request) <= _authorization_now():
        view["status"] = "expired"
    if detail:
        view["audit"] = [event for event in state["authorizations"]["audit"] if event["request_id"] == request["id"]]
    return view


def authorization_sync(remote_name: str) -> dict:
    """Capture terminal REVIEW_REQUIRED results as review-only authorization drafts."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    state = _remote_state_read(entry, paths)
    expected_digest = getattr(state, "digest", None) or _state_digest(state)
    _expire_authorizations(state)
    catalog_names = {item.name for item in load_catalog()["jobs"] if item.enabled and item.kind == "agent"}
    jobs = [job for job in _cron_snapshot(entry)["jobs"]
            if job.get("name") in catalog_names and job.get("enabled")]
    created = []
    requests = state["authorizations"]["requests"]
    for job in jobs:
        job_id = str(job.get("id") or "")
        if not _CRON_JOB_RE.fullmatch(job_id):
            continue
        output = cron_output(remote_name, job_id, 200)
        if output.get("status") != "available":
            continue
        match = _REVIEW_REQUIRED_RE.search(str(output.get("data", {}).get("output") or ""))
        if not match:
            continue
        blocker = _redact(match.group(1).strip())[:500]
        if not blocker or _contains_credential(blocker):
            continue
        source_fingerprint = hashlib.sha256(f"{job_id}\n{blocker}".encode()).hexdigest()
        if any(request.get("source_fingerprint") == source_fingerprint for request in requests.values()):
            continue
        for request in requests.values():
            if request.get("job_name") == job["name"] and request.get("status") == "review_required":
                request["status"] = "superseded"
                _authorization_audit(state, request, "superseded")
        now = _authorization_now()
        request = {"id": secrets.token_hex(8), "job_name": job["name"], "blocker": blocker,
                   "source_fingerprint": source_fingerprint, "fingerprint": source_fingerprint,
                   "status": "review_required", "created_at": now.isoformat(),
                   "expires_at": (now + timedelta(days=7)).isoformat()}
        requests[request["id"]] = request
        _authorization_audit(state, request, "review_required")
        created.append(_authorization_view(state, request, True))
    if created:
        _remote_state_write(entry, paths, state, expected_digest=expected_digest)
    return result(True, "authorization_sync", remote_name, status="synced",
                  data={"created": created, "created_count": len(created)})


def authorization_list(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    state = _remote_state_read(entry, _paths(entry))
    requests = state["authorizations"]["requests"]
    rows = [_authorization_view(state, request, False) for request in requests.values()]
    rows.sort(key=lambda row: (row["status"] != "pending", row["created_at"]), reverse=False)
    return result(True, "authorization_list", remote_name, status="ok", data={"requests": rows})


def authorization_show(remote_name: str, request_id: str) -> dict:
    entry = _require_remote(remote_name)
    state = _remote_state_read(entry, _paths(entry))
    request_id = _valid_authorization_id(request_id)
    request = state["authorizations"]["requests"].get(request_id)
    if not request:
        raise HermesError("authorization request was not found", "authorization_not_found")
    view = _authorization_view(state, request, True)
    return result(True, "authorization_show", remote_name, status=view["status"], data={"request": view})


def authorization_request(remote_name: str, job_name: str, scope: str, replay_origin: str, reason: str,
                          expires_in_minutes: int = 1440) -> dict:
    if (isinstance(expires_in_minutes, bool) or not isinstance(expires_in_minutes, int) or
            not 1 <= expires_in_minutes <= 1440):
        raise HermesError("authorization expiry must be between 1 and 1440 minutes", "invalid_authorization_expiry")
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    job = _catalog_authorization_job((job_name or "").strip(), paths)
    scope, replay_origin, reason = (_valid_authorization_scope(scope), _valid_replay_origin(replay_origin),
                                    _valid_authorization_reason(reason))
    state = _remote_state_read(entry, paths)
    expected_digest = getattr(state, "digest", None) or _state_digest(state)
    _expire_authorizations(state)
    requests = state["authorizations"]["requests"]
    if len(requests) >= _AUTH_MAX_REQUESTS:
        raise HermesError("authorization request limit reached", "authorization_limit_reached")
    for request in requests.values():
        if request.get("job_name") == job["name"] and request.get("status") in {"pending", "review_required"}:
            request["status"] = "superseded"
            _authorization_audit(state, request, "superseded")
    now = _authorization_now()
    request_id = secrets.token_hex(8)
    request = {"id": request_id, "job_name": job["name"], "scope": scope, "replay_origin": replay_origin,
               "rationale": reason, "fingerprint": _authorization_fingerprint(job["name"], scope, replay_origin, reason),
               "status": "pending", "created_at": now.isoformat(),
               "expires_at": (now + timedelta(minutes=expires_in_minutes)).isoformat()}
    requests[request_id] = request
    _authorization_audit(state, request, "requested")
    _remote_state_write(entry, paths, state, expected_digest=expected_digest)
    return result(True, "authorization_request", remote_name, status="pending",
                  data={"request": _authorization_view(state, request, True)})


def _set_cron_prompt(entry: dict, job_id: str, prompt: str) -> None:
    paths = _paths(entry)
    command = f"{paths['launcher']} cron edit {shlex.quote(job_id)} --prompt {shlex.quote(prompt)}"
    res = _ssh(entry, command, timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "Hermes cron prompt update failed", entry)[:1000],
                          "authorization_prompt_update_failed", True)


def _authorization_prompt(job: dict, request: dict) -> str:
    return job["prompt"].rstrip() + "\n\n" + (
        "SANDBOX AUTHORIZATION: This is the sole approved exception for this run. "
        f"Request {request['id']} authorizes only scope {request['scope']} against replay origin "
        f"{request['replay_origin']}. Rationale: {request['rationale']}. "
        f"Expires at {request['expires_at']}. Before any protected action, compare the current UTC time; "
        "at or after expiry, do not perform that action and report REVIEW_REQUIRED. "
        "Do not broaden this authorization or perform any other protected action.\n"
    )


def authorization_approve(remote_name: str, request_id: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("authorization approval requires --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    state = _remote_state_read(entry, paths)
    expected_digest = getattr(state, "digest", None) or _state_digest(state)
    _expire_authorizations(state)
    request_id = _valid_authorization_id(request_id)
    request = state["authorizations"]["requests"].get(request_id)
    if not request:
        raise HermesError("authorization request was not found", "authorization_not_found")
    if request.get("status") != "pending":
        raise HermesError("authorization request is not pending", "authorization_not_pending")
    job = _catalog_authorization_job(request["job_name"], paths)
    matches = [item for item in _cron_snapshot(entry)["jobs"] if item.get("name") == job["name"] and item.get("enabled")]
    if len(matches) != 1 or not _CRON_JOB_RE.fullmatch(str(matches[0].get("id") or "")):
        raise HermesError("matching catalog cron job was not found", "authorization_cron_job_not_found")
    prompt = _authorization_prompt(job, request)
    original_state = copy.deepcopy(state)
    _supersede_approved_authorizations(state, request["job_name"], request_id)
    request["status"] = "approved"
    request["approved_at"] = _authorization_now().isoformat()
    _authorization_audit(state, request, "approved")
    _remote_state_write(entry, paths, state, expected_digest=expected_digest)
    try:
        _set_cron_prompt(entry, matches[0]["id"], prompt)
    except Exception as prompt_error:
        try:
            _remote_state_write(entry, paths, original_state, expected_digest=_state_digest(state))
        except Exception as rollback_error:
            raise HermesError("authorization state rollback failed after prompt update failure",
                              "authorization_state_rollback_failed", True) from rollback_error
        raise prompt_error
    return result(True, "authorization_approve", remote_name, status="approved", job_id=matches[0]["id"],
                  data={"request": _authorization_view(state, request, True)})


def write_state(path: Path, state: dict) -> None:
    if state.get("schema_version") != STATE_SCHEMA:
        raise HermesError("cannot write an unsupported Hermes state schema", "invalid_state")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                temp_name = handle.name
            os.chmod(temp_name, 0o600)
            Path(temp_name).replace(path)
            path.chmod(0o600)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _remote_state_write(entry: dict, paths: dict, state: dict, *, expected_digest: str | None = None) -> None:
    payload = base64.b64encode((json.dumps(state, sort_keys=True) + "\n").encode()).decode()
    target = shlex.quote(paths["state"])
    lock = shlex.quote(paths["state"] + ".lock")
    guard = ""
    if expected_digest:
        digest_script = (
            "import hashlib,json,sys; "
            "value=json.load(open(sys.argv[1])); "
            "print(hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest())"
        )
        guard = (
            f"if test -f {target}; then actual=$(python3 -c {shlex.quote(digest_script)} {target}); "
            f"if test \"$actual\" != {shlex.quote(expected_digest)}; then "
            "echo state_conflict >&2; exit 75; fi; fi; "
        )
    command = (
        f"mkdir -p {shlex.quote(paths['sandbox_home'] + '/runtime')}; "
        f"exec 9>{lock}; flock -w 30 9; "
        f"{guard}"
        f"tmp={target}.tmp.$$; trap 'rm -f \"$tmp\"' EXIT; "
        f"echo {shlex.quote(payload)} | base64 -d > \"$tmp\"; "
        f"chmod 600 \"$tmp\"; mv \"$tmp\" {target}; "
        f"python3 -c {shlex.quote('import os,sys; fd=os.open(os.path.dirname(sys.argv[1]), os.O_RDONLY | getattr(os, \"O_DIRECTORY\", 0)); os.fsync(fd); os.close(fd)')} {target}; "
        "trap - EXIT"
    )
    try:
        _checked(entry, command, what="could not write Hermes state")
    except HermesError as exc:
        if "state_conflict" in str(exc):
            raise HermesError("Hermes state changed during the authorization operation", "state_conflict", True) from exc
        raise


def _remote_state_read(entry: dict, paths: dict) -> dict:
    res = _ssh(entry, f"if test -f {shlex.quote(paths['state'])}; then cat {shlex.quote(paths['state'])}; fi")
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not read Hermes state", entry), "state_read_failed", True)
    raw_output = res.stdout or ""
    raw = raw_output.strip()
    if not raw:
        return _RemoteStateSnapshot(_new_state(), _state_digest(None))
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HermesError("remote Hermes state is invalid", "invalid_state") from exc
    try:
        digest = _state_digest(state)
        return _RemoteStateSnapshot(_normalize_state(state), digest)
    except HermesError as exc:
        raise HermesError("remote Hermes state schema is unsupported", "invalid_state") from exc


def _record_v2_evidence(entry: dict, paths: dict, check: str, details: dict | None = None) -> None:
    """Record evidence only from a completed control-plane operation."""
    if check not in _V2_ACCEPTANCE_CHECKS:
        return
    try:
        state = _remote_state_read(entry, paths)
    except (HermesError, KeyError, OSError, StopIteration):
        # Evidence bookkeeping must never turn an already-completed operation
        # into a failure (and remains compatible with older state fixtures).
        return
    installation = state.get("installation") or {}
    commit = installation.get("commit")
    if not _COMMIT_RE.fullmatch(str(commit or "")):
        return
    gate = state.setdefault("gates", {}).setdefault("v2_operations", {})
    evidence = gate.setdefault("evidence", {})
    evidence[check] = "passed"
    if details:
        gate.setdefault("check_details", {})[check] = {str(k): str(v)[:200] for k, v in details.items()}
    gate["commit"] = commit
    gate["integration_schema"] = STATE_SCHEMA
    gate["sandbox_commit"] = os.environ.get("SANDBOX_COMMIT", "unknown")[:80]
    gate["recorded_at"] = datetime.now(timezone.utc).isoformat()
    if all(evidence.get(name) == "passed" for name in _V2_ACCEPTANCE_CHECKS):
        gate["status"] = "passed"
    _remote_state_write(entry, paths, state)


def render_profile(sandbox_home: str, sb_path: str) -> dict:
    """Return the integration-owned Hermes config values without secrets."""
    return {
        "model": {"default": HERMES_DEFAULT_MODEL, "provider": HERMES_DEFAULT_PROVIDER},
        "terminal": {"backend": "local", "home_mode": "real", "cwd": f"{sandbox_home}/hermes-repos"},
        "approvals": {
            "mode": "manual", "cron_mode": "deny", "mcp_reload_confirm": True,
            "destructive_slash_confirm": True,
        },
        "checkpoints": {"enabled": True},
        "mcp_servers": {
            "sandbox": {
                "command": sb_path,
                "args": ["mcp"],
                "env": {"SANDBOX_HOME": sandbox_home},
                "enabled": True,
                "connect_timeout": 60,
                "timeout": 1200,
                "supports_parallel_tool_calls": False,
                "tools": {"resources": True, "prompts": True},
            }
        },
    }


def render_routing_profile() -> dict:
    """Return the non-secret routed-worker policy owned by Sandbox setup."""
    luna = scheduled_route("luna")
    terra = scheduled_route("terra")
    sol = scheduled_route("sol")
    coordinator_soul = f"""{HERMES_ROUTING_POLICY_START}
## Sandbox worker routing

You run on Codex Spark and coordinate non-trivial work rather than performing it.
Route read-only evidence and file review to Luna, bounded implementation and tests to
Terra, and architecture, security, authorization, data/API, or production-risk work to
Sol. Give workers a bounded goal, context, acceptance criteria, and tool scope. Gather
their evidence, report residual risk, and require human approval before high-impact
changes. Do not silently downgrade Sol-class work. Trivial questions may be answered
directly without delegation. For scheduled jobs, keep provider, model, and reasoning
effort as separate fields. Never append an effort such as `/high` to a model identifier.
Use the Sandbox `hermes cron` controls so routing is validated before activation.
{HERMES_ROUTING_POLICY_END}"""
    return {
        "delegation": {
            "provider": terra.provider,
            "model": terra.model,
            "max_concurrent_children": 1,
            "max_spawn_depth": 1,
            "orchestrator_enabled": False,
        },
        "kanban": {
            "dispatch_in_gateway": True,
            "auto_decompose": True,
            "auto_decompose_per_tick": 1,
            "orchestrator_profile": "default",
            "default_assignee": "terra",
            "max_in_progress": 1,
            "max_in_progress_per_profile": 1,
        },
        "auxiliary": {
            "kanban_decomposer": {"provider": HERMES_DEFAULT_PROVIDER, "model": HERMES_DEFAULT_MODEL},
            "triage_specifier": {"provider": sol.provider, "model": sol.model},
        },
        "coordinator_soul": coordinator_soul,
        "workers": (
            {
                "name": "luna",
                "model": luna.model,
                "reasoning_effort": luna.effort,
                "description": "Read-only evidence worker for file review, logs, specifications, and research.",
                "toolsets": ["safe", "file"],
                "soul": (
                    "You are Luna, the evidence worker. Read and search files, task context, logs, and public "
                    "sources; gather and summarize evidence; state uncertainty; and finish with concise findings. "
                    "Never call write, patch, or rename operations; never run commands, execute code, create tasks, "
                    "or make external changes. If mutation is necessary, recommend routing the work to Terra or Sol."
                ),
            },
            {
                "name": "terra",
                "model": terra.model,
                "reasoning_effort": terra.effort,
                "description": "Implementation worker for bounded approved changes, tests, and routine debugging.",
                "toolsets": [],
                "soul": (
                    "You are Terra, the implementation worker. Execute only a bounded assigned task with explicit "
                    "acceptance criteria. Make minimal changes, run focused tests, and return evidence and residual "
                    "risk. Escalate unresolved architecture, security, authorization, data/API, migration, or production "
                    "decisions to Sol and a human reviewer."
                ),
            },
            {
                "name": "sol",
                "model": sol.model,
                "reasoning_effort": sol.effort,
                "description": "High-judgment worker for architecture, specifications, and sensitive boundaries.",
                "toolsets": [],
                "soul": (
                    "You are Sol, the high-judgment architecture and risk worker. Handle architecture, specifications, "
                    "security, authorization, data/API/production boundaries, and critical debugging. Before any "
                    "high-impact mutation, require an explicit human checkpoint and do not claim completion without verification."
                ),
            },
        ),
    }


def _routing_setup_command(paths: dict) -> str:
    """Render idempotent remote setup for the Sandbox-owned worker routing."""
    routing = render_routing_profile()
    integration = render_profile(paths["sandbox_home"], paths["sb"])
    # ``_paths`` intentionally supplies a remote-shell expression using
    # ``$HOME``. Quoting it would make the dollar sign literal and prevent the
    # remote shell from locating Hermes. Quote only literal arguments below.
    launcher = paths["launcher"]
    worker_commands = []
    for worker in routing["workers"]:
        name = shlex.quote(worker["name"])
        worker_commands.append(
            f"if ! {launcher} profile show {name} >/dev/null 2>&1; then "
            f"{launcher} profile create {name} --no-alias --description {shlex.quote(worker['description'])} >/dev/null; fi"
        )
    worker_setup = "\n".join(worker_commands)
    payload = base64.b64encode(json.dumps(routing).encode()).decode()
    integration_payload = base64.b64encode(json.dumps(integration).encode()).decode()
    return f"""
{worker_setup}
{launcher} kanban init >/dev/null
routing_payload={shlex.quote(payload)}
integration_payload={shlex.quote(integration_payload)}
export routing_payload integration_payload
PYTHONPATH="$HOME/.hermes/hermes-agent" "$HOME/.hermes/hermes-agent/venv/bin/python" - <<'PY'
import base64
import json
import os
import re
from pathlib import Path

import yaml
from hermes_cli.config import get_config_path, read_raw_config
from utils import atomic_yaml_write

routing = json.loads(base64.b64decode(os.environ["routing_payload"]).decode())
integration = json.loads(base64.b64decode(os.environ["integration_payload"]).decode())

def merge_owned(target, source):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge_owned(target[key], value)
        else:
            target[key] = value

root_config = read_raw_config()
merge_owned(root_config, integration)
root_config["delegation"] = routing["delegation"]
root_config["kanban"] = routing["kanban"]
root_config["auxiliary"] = routing["auxiliary"]
toolsets = root_config.setdefault("platform_toolsets", {{}})
cli = toolsets.setdefault("cli", [])
if not isinstance(cli, list):
    raise SystemExit("platform_toolsets.cli must be a list")
if "hermes-cli" not in cli:
    cli.insert(0, "hermes-cli")
if "kanban" not in cli:
    cli.append("kanban")
atomic_yaml_write(get_config_path(), root_config, sort_keys=False)

root = Path.home() / ".hermes"
start = "<!-- SANDBOX_ROUTING_BEGIN -->"
end = "<!-- SANDBOX_ROUTING_END -->"
root_soul = root / "SOUL.md"
existing = root_soul.read_text() if root_soul.exists() else ""
block = routing["coordinator_soul"].strip()
pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
updated = pattern.sub(block, existing, count=1) if pattern.search(existing) else (existing.rstrip() + "\\n\\n" + block + "\\n")
root_soul.write_text(updated)

for worker in routing["workers"]:
    profile = root / "profiles" / worker["name"]
    config_path = profile / "config.yaml"
    config = yaml.safe_load(config_path.read_text()) or {{}}
    merge_owned(config, integration)
    config["model"] = {{"provider": {HERMES_DEFAULT_PROVIDER!r}, "default": worker["model"]}}
    config.setdefault("agent", {{}})["reasoning_effort"] = worker["reasoning_effort"]
    if worker["toolsets"]:
        config["platform_toolsets"] = {{"cli": worker["toolsets"]}}
    atomic_yaml_write(config_path, config, sort_keys=False)
    (profile / "SOUL.md").write_text(worker["soul"] + "\\n")
PY
"""


def state_setup(remote_name: str, repository: str) -> dict:
    entry = _require_remote(remote_name)
    repo_url = validate_state_repo(repository)
    remote.put_remote(remote_name, **{HERMES_STATE_REPO_KEY: repo_url})
    return result(True, "state_setup", remote_name, status="configured",
                  data={"repository": repo_url, "credentials": "operator-owned"})


def _state_repo(entry: dict) -> str:
    value = entry.get(HERMES_STATE_REPO_KEY)
    if not value:
        raise HermesError("no Hermes state repository configured", "state_repo_unconfigured")
    return validate_state_repo(value)


def _state_restore_command(paths: dict, repository: str) -> str:
    repo = shlex.quote(repository)
    return (
        "set -eu; stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; "
        f"git clone --quiet --depth=1 {repo} \"$stage/repo\"; "
        "test -f \"$stage/repo/manifest.json\"; "
        "grep -q 'schema_version' \"$stage/repo/manifest.json\"; "
        "for forbidden in auth.json credentials cookies sessions checkpoints state.db; do "
        "if find \"$stage/repo\" -type f -iname \"$forbidden\" | grep -q .; then exit 42; fi; done; "
        "mkdir -p \"$stage/new-hermes\" \"$stage/new-runtime\"; "
        "for safe in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json SOUL.md; do "
        "test ! -e \"$stage/repo/hermes/$safe\" || cp -p \"$stage/repo/hermes/$safe\" \"$stage/new-hermes/$safe\"; done; "
        "test ! -d \"$stage/repo/hermes/memories\" || cp -R \"$stage/repo/hermes/memories\" \"$stage/new-hermes/memories\"; "
        f"test ! -e \"$stage/repo/sandbox/hermes.json\" || cp -p \"$stage/repo/sandbox/hermes.json\" \"$stage/new-runtime/hermes.json\"; "
        f"mkdir -p \"$HOME/.hermes\" {shlex.quote(paths['sandbox_home'])}/runtime; "
        "for safe in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json SOUL.md; do "
        "test ! -e \"$stage/new-hermes/$safe\" || install -m 600 \"$stage/new-hermes/$safe\" \"$HOME/.hermes/$safe\"; done; "
        "test ! -d \"$stage/new-hermes/memories\" || { rm -rf \"$HOME/.hermes/memories.new\"; cp -R \"$stage/new-hermes/memories\" \"$HOME/.hermes/memories.new\"; mv \"$HOME/.hermes/memories.new\" \"$HOME/.hermes/memories\"; }; "
        f"test ! -e \"$stage/new-runtime/hermes.json\" || install -m 600 \"$stage/new-runtime/hermes.json\" {shlex.quote(paths['state'])}; "
        "printf '%s\\n' restored"
    )


def state_restore(remote_name: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("state restore requires --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    repository = _state_repo(entry)
    paths = _paths(entry)
    res = _checked(entry, _state_restore_command(paths, repository), timeout=300,
                   what="Hermes state restore failed")
    return result(True, "state_restore", remote_name, status="restored",
                  data={"repository": repository, "result": (res.stdout or "").strip()})


def _state_sync_command(paths: dict, repository: str) -> str:
    repo = shlex.quote(repository)
    return (
        "set -eu; stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; "
        f"git clone --quiet {repo} \"$stage/repo\"; mkdir -p \"$stage/repo/hermes\" \"$stage/repo/sandbox\"; "
        "for src in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json; do "
        "test ! -e \"$HOME/.hermes/$src\" || cp -p \"$HOME/.hermes/$src\" \"$stage/repo/hermes/$src\"; done; "
        "test ! -e \"$HOME/.hermes/SOUL.md\" || cp -p \"$HOME/.hermes/SOUL.md\" \"$stage/repo/hermes/SOUL.md\"; "
        "test ! -d \"$HOME/.hermes/memories\" || cp -R \"$HOME/.hermes/memories\" \"$stage/repo/hermes/memories\"; "
        f"test ! -e {shlex.quote(paths['state'])} || cp -p {shlex.quote(paths['state'])} \"$stage/repo/sandbox/hermes.json\"; "
        "if find \"$stage/repo\" -type f \\( -iname 'auth.json' -o -iname 'credentials*' -o -iname 'cookies*' -o -iname 'sessions*' -o -iname 'checkpoints*' -o -iname '*.pem' -o -iname '*.key' -o -iname 'state.db*' \\) | grep -q .; then exit 42; fi; "
        "if grep -RIEq 'github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{30,}|BEGIN (RSA|OPENSSH|EC|PRIVATE) KEY' \"$stage/repo/hermes\" \"$stage/repo/sandbox\" 2>/dev/null; then exit 43; fi; "
        "cd \"$stage/repo\"; git add -A; if git diff --cached --quiet; then echo unchanged; else git -c user.name='Hermes State Backup' -c user.email='hermes-state@users.noreply.github.com' commit -qm 'chore: sync sanitized Hermes state'; git push -q origin HEAD; echo pushed; fi"
    )


def state_sync(remote_name: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("state sync requires --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    repository = _state_repo(entry)
    res = _checked(entry, _state_sync_command(_paths(entry), repository), timeout=300,
                   what="Hermes state sync failed")
    status = (res.stdout or "").strip().splitlines()[-1:] or ["unknown"]
    return result(True, "state_sync", remote_name, status=status[0],
                  data={"repository": repository})


def validate_drive_destination(value: str) -> str:
    """Validate an rclone destination without allowing shell syntax or URLs."""
    value = (value or "").strip()
    path = value.split(":", 1)[1] if ":" in value else ""
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]*", value)
            or (path and any(part in {"", ".", ".."} for part in path.split("/")))):
        raise HermesError("Drive destination must be an rclone remote path, e.g. gdrive:hermes-backups", "invalid_drive_destination")
    return value.rstrip("/")


def drive_setup(remote_name: str, destination: str) -> dict:
    _require_remote(remote_name)
    destination = validate_drive_destination(destination)
    remote.put_remote(remote_name, **{HERMES_DRIVE_DESTINATION_KEY: destination})
    return result(True, "drive_setup", remote_name, status="configured",
                  data={"destination": destination, "scope": "full"})


def _drive_destination(entry: dict) -> str:
    destination = entry.get(HERMES_DRIVE_DESTINATION_KEY)
    if not destination:
        raise HermesError("no Google Drive destination configured", "drive_unconfigured")
    return validate_drive_destination(destination)


def _drive_backup_command(paths: dict, destination: str, backup_id: str, scope: str = "full") -> str:
    """Create a full or incremental encrypted recovery point; passphrase arrives only on stdin."""
    if scope not in {"full", "incremental"}:
        raise HermesError("drive scope must be full or incremental", "invalid_drive_scope")
    destination = validate_drive_destination(destination)

    script = r'''import atexit
import json
import os
import pathlib
import shutil
import subprocess
import hashlib
import tempfile

HOME = pathlib.Path(os.path.expanduser("~"))
SANDBOX = pathlib.Path(__SANDBOX__)
SB = __SB__
DESTINATION = __DESTINATION__
BACKUP_ID = __BACKUP_ID__
SCOPE = __SCOPE__


def _run(command, capture=False):
    kwargs = {
        "stdout": subprocess.PIPE if capture else subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
        "cwd": HOME,
        "text": True,
    }
    result = subprocess.run(command, **kwargs)
    if result.returncode != 0:
        raise SystemExit(result.stdout or "command failed")
    return result


def _read_instances():
    registry = SANDBOX / "runtime" / "registry.json"
    if not registry.exists():
        return []
    data = json.loads(registry.read_text())
    entries = data.get("instances") if isinstance(data, dict) else {}
    if not entries:
        entries = data
    for value in entries.values() if isinstance(entries, dict) else []:
        if isinstance(value, dict) and value.get("instance"):
            yield str(value.get("instance"))


def _sha256(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


stage = pathlib.Path(tempfile.mkdtemp(prefix="hermes-drive-backup-", dir="/tmp"))
atexit.register(shutil.rmtree, str(stage), ignore_errors=True)

passfile = stage / "passphrase"
passfile.write_bytes(__import__('sys').stdin.buffer.read())

archive = stage / f"{BACKUP_ID}.tar.gz"
cipher = stage / f"{BACKUP_ID}.tar.gz.gpg"
manifest = stage / f"{BACKUP_ID}.manifest.json"
state = stage / f"{BACKUP_ID}.state.snar"
fallback = SANDBOX / "runtime" / f".drive-volume-fallbacks-{BACKUP_ID}"
fallback.mkdir(parents=True, exist_ok=True)
atexit.register(shutil.rmtree, str(fallback), ignore_errors=True)

instances = list(_read_instances())
for instance in instances:
    env = dict(os.environ)
    env["SANDBOX_INSTANCE"] = instance
    result = subprocess.run([SB, "snapshot", f"drive-{BACKUP_ID}", "--force"], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, check=False, cwd=HOME)
    if result.returncode == 0:
        print(f"Hermes Drive: snapshot {instance} complete", file=__import__('sys').stderr)
        continue
    msg = (result.stdout or "").lower()
    if "address already in use" in msg or "cannot assign requested address" in msg or "permission denied" in msg:
        print(f"Hermes Drive: snapshot for {instance} hit host-port conflict; using fallback", file=__import__('sys').stderr)
    else:
        print(f"Hermes Drive: snapshot for {instance} failed; using fallback", file=__import__('sys').stderr)
    backup_tar = fallback / f"{instance}-mysql.tar"
    container = f"sandbox-{instance}-db-1"
    inspect_result = subprocess.run(["docker", "inspect", container],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    text=False, cwd=HOME, check=False)
    if inspect_result.returncode != 0:
        print(f"Hermes Drive: database container unavailable for {instance}; continuing", file=__import__('sys').stderr)
        continue
    with backup_tar.open("wb") as out:
        docker_result = subprocess.run(["docker", "cp", f"{container}:/var/lib/mysql", "-"],
                                       stdout=out, stderr=subprocess.PIPE, text=False, cwd=HOME, check=False)
    if docker_result.returncode != 0:
        print(f"Hermes Drive: database snapshot unavailable for {instance}; continuing", file=__import__('sys').stderr)
        try:
            backup_tar.unlink()
        except FileNotFoundError:
            pass

base_id = ""
chain_id = BACKUP_ID
if SCOPE == "incremental":
    manifests = _run(["rclone", "lsf", "--files-only", DESTINATION], capture=True).stdout.splitlines()
    manifest_names = [name.strip() for name in manifests if name.strip().endswith(".manifest.json")]
    if not manifest_names:
        raise SystemExit(1)
    base_id = manifest_names[-1][:-14]
    previous = stage / f"{base_id}.manifest.json"
    _run(["rclone", "copyto", f"{DESTINATION}/{base_id}.manifest.json", str(previous)], capture=True)
    if previous.exists():
        base_manifest = json.loads(previous.read_text())
        base_scope = base_manifest.get("scope", "full")
        if base_scope not in {"full", "incremental"}:
            raise SystemExit(1)
        base_state = base_manifest.get("state_file", f"{base_id}.state.snar")
        chain_id = base_manifest.get("chain_id") or base_id
        previous_state = stage / f"{base_id}.state.snar"
        _run(["rclone", "copyto", f"{DESTINATION}/{base_state}", str(previous_state)], capture=True)
        if previous_state.exists():
            previous_state.replace(state)

_run(["tar", "--ignore-failed-read", "--absolute-names", "--listed-incremental", str(state),
      "--exclude", f"{HOME}/.hermes/hermes-agent",
      "--exclude", f"{HOME}/.hermes/hermes-agent.restore.*",
      "--exclude", f"{HOME}/.hermes/node",
      "--exclude", f"{SANDBOX}/runtime/dl-cache",
      "--exclude", f"{SANDBOX}/runtime/hermes-jobs",
      "--exclude", f"{SANDBOX}/runtime/.drive-volume-fallbacks-*",
      "-czf", str(archive),
      f"{HOME}/.hermes", f"{HOME}/.config/gh", f"{HOME}/.config/rclone",
      f"{SANDBOX}/runtime"],
     capture=True)
_run(["rm", "-rf", str(fallback)], capture=True)

_run(["gpg", "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase-file", str(passfile),
      "--symmetric", "--cipher-algo", "AES256", "--output", str(cipher), str(archive)], capture=True)

manifest.write_text(json.dumps({
    "schema_version": 2,
    "id": BACKUP_ID,
    "scope": SCOPE,
    "chain_id": chain_id,
    "base_id": base_id,
    "archive": f"{BACKUP_ID}.tar.gz.gpg",
    "state_file": f"{BACKUP_ID}.state.snar",
    "plain_sha256": _sha256(archive),
    "cipher_sha256": _sha256(cipher),
    "excluded": ["container-images", "package-caches", "runtime-sockets"],
}))

_run(["rclone", "copyto", "--stats-one-line", "--stats=10s", str(cipher),
      f"{DESTINATION}/{BACKUP_ID}.tar.gz.gpg"], capture=True)
_run(["rclone", "copyto", str(manifest), f"{DESTINATION}/{BACKUP_ID}.manifest.json"], capture=True)
_run(["rclone", "copyto", str(state), f"{DESTINATION}/{BACKUP_ID}.state.snar"], capture=True)

print(f"scope={SCOPE}")
print(f"chain_id={chain_id}")
print(f"base_id={base_id}")
print(f"archive_bytes={cipher.stat().st_size}")
'''

    script = script.replace("__SANDBOX__", json.dumps(pathlib.Path(paths["sandbox_home"]).as_posix()))
    script = script.replace("__SB__", json.dumps(paths["sb"]))
    script = script.replace("__DESTINATION__", json.dumps(destination))
    script = script.replace("__BACKUP_ID__", json.dumps(backup_id))
    script = script.replace("__SCOPE__", json.dumps(scope))

    return (
        "set -eu; umask 077; "
        "python3 - <<'PY'\n"
        f"{script}\n"
        "PY\n"
    )


def drive_backup(remote_name: str, passphrase: bytes, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("Drive backup requires --confirm", "confirmation_required")
    if not passphrase or len(passphrase.rstrip(b"\r\n")) < 12:
        raise HermesError("recovery passphrase must be at least 12 bytes", "invalid_recovery_passphrase")
    entry = _require_remote(remote_name)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    completed = _ssh_stdin_with_progress(entry, _drive_backup_command(_paths(entry), _drive_destination(entry), backup_id),
                                         passphrase.rstrip(b"\r\n") + b"\n", timeout=3600)
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode(errors="replace")
        stdout = (completed.stdout or b"").decode(errors="replace")
        raise HermesError(_redact(stderr or stdout or "Drive backup failed", entry)[:1000], "drive_backup_failed", True)
    values = dict(line.split("=", 1) for line in (completed.stdout or b"").decode(errors="replace").splitlines() if "=" in line)
    return result(True, "drive_backup", remote_name, status="backed_up",
                  data={"backup_id": backup_id,
                        "scope": values.get("scope", "full"),
                        "chain_id": values.get("chain_id", backup_id),
                        "base_id": values.get("base_id", ""),
                        "archive_bytes": int(values.get("archive_bytes", "0"))})


def drive_list(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    destination = _drive_destination(entry)
    res = _checked(entry, f"command -v rclone >/dev/null 2>&1 && rclone lsf --files-only {shlex.quote(destination)}", timeout=120,
                   what="could not list Google Drive backups")
    manifests = sorted(line for line in (res.stdout or "").splitlines() if line.endswith(".manifest.json"))
    return result(True, "drive_list", remote_name, status="ready", data={"backups": manifests})


def _drive_restore_command(paths: dict, destination: str, backup_id: str) -> str:
    dest = shlex.quote(destination)
    home = shlex.quote(paths["sandbox_home"])
    return (
        "set -eu; umask 077; stage=$(mktemp -d \"$HOME/.hermes-restore.XXXXXX\"); passfile=\"$stage/passphrase\"; "
        "trap 'rm -rf \"$stage\"' EXIT; cat > \"$passfile\"; test -s \"$passfile\"; "
        "command -v rclone >/dev/null 2>&1; command -v gpg >/dev/null 2>&1; "
        f"python3 - \"{backup_id}\" {dest} \"$stage\" <<'PY' > \"$stage/plan.txt\"\n"
        "import json\n"
        "import pathlib\n"
        "import re\n"
        "import subprocess\n\n"
        "import sys\n"
        "current = sys.argv[1]\n"
        "destination = sys.argv[2]\n"
        "stage = pathlib.Path(sys.argv[3])\n"
        "id_pattern = re.compile(r'^\\d{8}T\\d{6}Z-[0-9a-f]{8}$')\n"
        "if not id_pattern.fullmatch(current):\n"
        "  raise SystemExit(1)\n"
        "seen = []\n"
        "history = set()\n"
        "while True:\n"
        "  manifest = stage / f'{current}.manifest.json'\n"
        "  cp = subprocess.run(['rclone', 'copyto', f'{destination}/{current}.manifest.json', str(manifest)], capture_output=True, text=True)\n"
        "  if cp.returncode != 0:\n"
        "    raise SystemExit(1)\n"
        "  data = json.loads(manifest.read_text())\n"
        "  if data.get('schema_version') != 2 or data.get('id') != current:\n"
        "    raise SystemExit(1)\n"
        "  scope = data.get('scope', 'full')\n"
        "  if scope not in {'full', 'incremental'}:\n"
        "    raise SystemExit(1)\n"
        "  if data.get('archive') != f'{current}.tar.gz.gpg' or data.get('state_file') != f'{current}.state.snar':\n"
        "    raise SystemExit(1)\n"
        "  if not id_pattern.fullmatch(str(data.get('chain_id') or '')):\n"
        "    raise SystemExit(1)\n"
        "  for digest_name in ('cipher_sha256', 'plain_sha256'):\n"
        "    if not re.fullmatch(r'[0-9a-f]{64}', str(data.get(digest_name) or '')):\n"
        "      raise SystemExit(1)\n"
        "  if current in history:\n"
        "    raise SystemExit(1)\n"
        "  history.add(current)\n"
        "  seen.append((current, data))\n"
        "  if scope == 'full':\n"
        "    if data.get('base_id') not in ('', None):\n"
        "      raise SystemExit(1)\n"
        "    break\n"
        "  current = data.get('base_id')\n"
        "  if not isinstance(current, str) or not id_pattern.fullmatch(current):\n"
        "    raise SystemExit(1)\n"
        "print('CHAIN_ID=' + (seen[-1][1].get('chain_id') or seen[-1][0]))\n"
        "for rid, _ in reversed(seen):\n"
        "  print(rid)\n"
        "PY\n"
        "while IFS= read -r restore_id; do\n"
        "  if test -z \"$restore_id\"; then continue; fi\n"
        "  if test \"$restore_id\" = CHAIN_ID=*; then continue; fi\n"
        "  manifest=\"$stage/$restore_id.manifest.json\"\n"
        "  archive=\"$stage/$restore_id.tar.gz.gpg\"\n"
        "  rclone copyto {dest}/\"$restore_id\".tar.gz.gpg \"$archive\"\n"
        "  rclone copyto {dest}/\"$restore_id\".manifest.json \"$manifest\"\n"
        "  expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(\"cipher_sha256\",\"\"))' \"$manifest\"); "
        "actual=$(sha256sum \"$archive\" | awk '{print $1}'); test \"$expected\" = \"$actual\"; \n"
        "gpg --batch --yes --pinentry-mode loopback --passphrase-file \"$passfile\" --decrypt --output \"$stage/$restore_id.tar.gz\" \"$archive\"; "
        "expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(\"plain_sha256\",\"\"))' \"$manifest\"); "
        "actual=$(sha256sum \"$stage/$restore_id.tar.gz\" | awk '{print $1}'); test \"$expected\" = \"$actual\"; "
        "python3 - \"$stage/$restore_id.tar.gz\" \"$HOME\" " + home + " <<'PY'\n"
        "import pathlib\n"
        "import posixpath\n"
        "import stat\n"
        "import sys\n"
        "import tarfile\n\n"
        "archive_path, home_root, sandbox_root = sys.argv[1:]\n"
        "roots = tuple(root.rstrip('/') or '/' for root in (home_root, sandbox_root))\n"
        "def clean(path):\n"
        "  if not isinstance(path, str) or '\\x00' in path:\n"
        "    raise SystemExit(1)\n"
        "  raw = path if path.startswith('/') else '/' + path\n"
        "  trimmed = raw.rstrip('/') or '/'\n"
        "  normalized = posixpath.normpath(trimmed)\n"
        "  if normalized != trimmed or not any(normalized == root or normalized.startswith(root + '/') for root in roots):\n"
        "    raise SystemExit(1)\n"
        "  return normalized\n"
        "with tarfile.open(archive_path, 'r:gz') as archive:\n"
        "  members = {}\n"
        "  for member in archive.getmembers():\n"
        "    name = clean(member.name)\n"
        "    if name in members or member.ischr() or member.isblk() or member.isfifo():\n"
        "      raise SystemExit(1)\n"
        "    members[name] = member\n"
        "  def resolve_link(name, seen):\n"
        "    member = members.get(name)\n"
        "    if member is None or not (member.issym() or member.islnk()):\n"
        "      return name\n"
        "    if name in seen:\n"
        "      raise SystemExit(1)\n"
        "    target = clean(member.linkname if member.linkname.startswith('/') else posixpath.join(posixpath.dirname(name), member.linkname))\n"
        "    return resolve_link(target, seen | {name})\n"
        "  for name, member in members.items():\n"
        "    if member.issym() or member.islnk():\n"
        "      resolve_link(name, set())\n"
        "PY\n"
        "mkdir -p \"$stage/extract\"; tar -C \"$stage/extract\" --no-same-owner --no-same-permissions --transform='s,^/,,' -xzf \"$stage/$restore_id.tar.gz\"; \n"
        "done < <(grep -v '^CHAIN_ID=' \"$stage/plan.txt\")\n"
        "chain_id=$(sed -n 's/^CHAIN_ID=//p' \"$stage/plan.txt\" | head -n1)\n"
        "new_home=\"$stage/extract$HOME\"; new_sandbox=\"$stage/extract" + paths['sandbox_home'] + "\"; test -d \"$new_home/.hermes\"; test -d \"$new_sandbox\"; "
        "systemctl --user stop hermes-gateway-sandbox.service hermes-dashboard-sandbox.service 2>/dev/null || true; "
        f"previous_home=\"$HOME/.hermes.pre-restore\"; previous_sandbox={home}.pre-restore; previous_gh=\"$HOME/.config/gh.pre-restore\"; previous_rclone=\"$HOME/.config/rclone.pre-restore\"; rm -rf \"$previous_home\" \"$previous_sandbox\" \"$previous_gh\" \"$previous_rclone\"; "
        "test ! -e \"$HOME/.hermes\" || mv \"$HOME/.hermes\" \"$previous_home\"; "
        "test ! -e \"$HOME/.config/gh\" || mv \"$HOME/.config/gh\" \"$previous_gh\"; test ! -e \"$HOME/.config/rclone\" || mv \"$HOME/.config/rclone\" \"$previous_rclone\"; "
        f"test ! -e {home} || mv {home} \"$previous_sandbox\"; mv \"$new_home/.hermes\" \"$HOME/.hermes\"; mkdir -p \"$HOME/.config\"; test ! -d \"$new_home/.config/gh\" || mv \"$new_home/.config/gh\" \"$HOME/.config/gh\"; test ! -d \"$new_home/.config/rclone\" || mv \"$new_home/.config/rclone\" \"$HOME/.config/rclone\"; mv \"$new_sandbox\" {home}; "
        f"fallback={home}/runtime/.drive-volume-fallbacks-{backup_id}; if test -d \"$fallback\"; then for file in \"$fallback\"/*-mysql.tar; do test -f \"$file\" || continue; instance=$(basename \"$file\" -mysql.tar); SANDBOX_INSTANCE=\"$instance\" {shlex.quote(paths['sb'])} up >/dev/null; container=\"sandbox-$instance-db-1\"; docker stop \"$container\" >/dev/null; image=$(docker inspect \"$container\" --format '{{{{.Config.Image}}}}'); volume=$(docker inspect \"$container\" --format '{{{{range .Mounts}}}}{{{{if eq .Destination \"/var/lib/mysql\"}}}}{{{{.Name}}}}{{{{end}}}}{{{{end}}}}'); test -n \"$volume\"; docker run --rm --user 0 --entrypoint /bin/sh -v \"$volume:/dest\" -v \"$file:/backup.tar:ro\" \"$image\" -c 'find /dest -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +; tar -C /dest --strip-components=1 -xf /backup.tar'; SANDBOX_INSTANCE=\"$instance\" {shlex.quote(paths['sb'])} up >/dev/null; done; fi; "
        "systemctl --user daemon-reload 2>/dev/null || true; systemctl --user start hermes-gateway-sandbox.service 2>/dev/null || true; systemctl --user start hermes-dashboard-sandbox.service 2>/dev/null || true; "
        "printf '%s\\n' restored"
    )


def drive_restore(remote_name: str, backup_id: str, passphrase: bytes, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("Drive restore requires --confirm", "confirmation_required")
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", backup_id or ""):
        raise HermesError("invalid Drive backup id", "invalid_backup_id")
    if not passphrase or len(passphrase.rstrip(b"\r\n")) < 12:
        raise HermesError("recovery passphrase must be at least 12 bytes", "invalid_recovery_passphrase")
    entry = _require_remote(remote_name)
    completed = _ssh_stdin(entry, _drive_restore_command(_paths(entry), _drive_destination(entry), backup_id),
                           passphrase.rstrip(b"\r\n") + b"\n", timeout=3600)
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode(errors="replace")
        stdout = (completed.stdout or b"").decode(errors="replace")
        raise HermesError(_redact(stderr or stdout or "Drive restore failed", entry)[:1000], "drive_restore_failed", True)
    return result(True, "drive_restore", remote_name, status="restored", data={"backup_id": backup_id, "scope": "full"})


def _resolve_commit(entry: dict, tag: str, expected: str | None) -> str:
    res = _checked(entry,
                   f"git ls-remote {shlex.quote(HERMES_REPOSITORY_URL)} refs/tags/{shlex.quote(tag)}^{{}}",
                   what="could not resolve Hermes release")
    commit = (res.stdout or "").split()[0].lower() if (res.stdout or "").split() else ""
    if not _COMMIT_RE.fullmatch(commit):
        raise HermesError("Hermes tag did not resolve to an immutable commit", "release_not_found")
    if expected and commit != expected.lower():
        raise HermesError("requested Hermes commit does not match the signed tag", "release_mismatch")
    return commit


def _expected_commit(tag: str, commit: str | None) -> str | None:
    """Use the audited full revision for the built-in supported release."""
    return commit or (SUPPORTED_COMMIT if tag == SUPPORTED_TAG else None)


def _installed_checkout_snapshot(entry: dict) -> dict:
    res = _checked(
        entry,
        "if test -d \"$HOME/.hermes/hermes-agent/.git\"; then "
        "printf 'HEAD=%s\\n' \"$(git -C \"$HOME/.hermes/hermes-agent\" rev-parse HEAD)\"; "
        "printf 'ORIGIN=%s\\n' \"$(git -C \"$HOME/.hermes/hermes-agent\" remote get-url origin 2>/dev/null || true)\"; fi",
        what="could not read installed Hermes checkout",
    )
    values = dict(line.split("=", 1) for line in (res.stdout or "").splitlines() if "=" in line)
    head = values.get("HEAD", "")
    origin = values.get("ORIGIN", "")
    if not _COMMIT_RE.fullmatch(head):
        raise HermesError("installed Hermes revision is invalid", "invalid_installed_revision")
    if origin != HERMES_REPOSITORY_URL:
        raise HermesError("installed Hermes checkout does not retain the canonical upstream",
                          "invalid_installed_origin")
    return {"commit": head, "origin": origin}


def release_provenance_plan(remote_name: str, version: str = SUPPORTED_TAG,
                            commit: str | None = None) -> dict:
    """Verify a signed release in a disposable remote checkout without mutation."""
    _validate_release_tag(version)
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    resolved = _resolve_commit(entry, version, _expected_commit(version, commit))
    tag, resolved = validate_release(version, resolved)
    before = _installed_checkout_snapshot(entry)
    allowed_signer = f"{HERMES_RELEASE_SIGNER} {HERMES_RELEASE_SIGNER_KEY}"
    command = (
        "set -eu; stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; repo=\"$stage/repo\"; "
        "git init -q \"$repo\"; "
        f"git -C \"$repo\" remote add origin {shlex.quote(HERMES_REPOSITORY_URL)}; "
        f"git -C \"$repo\" fetch -q --depth=1 origin refs/tags/{shlex.quote(tag)}:refs/tags/{shlex.quote(tag)}; "
        f"printf '%s\\n' {shlex.quote(allowed_signer)} > \"$stage/allowed_signers\"; chmod 600 \"$stage/allowed_signers\"; "
        f"if ! git -C \"$repo\" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=\"$stage/allowed_signers\" verify-tag {shlex.quote(tag)}; then "
        "echo HERMES_RELEASE_PROVENANCE_FAILED >&2; exit 42; fi; "
        f"actual=$(git -C \"$repo\" rev-parse refs/tags/{shlex.quote(tag)}^{{}}); "
        f"test \"$actual\" = {shlex.quote(resolved)}; printf 'PROVENANCE_VERIFIED:%s:%s\\n' {shlex.quote(tag)} \"$actual\""
    )
    verified = _ssh(entry, command, timeout=180)
    if verified.returncode != 0:
        detail = _redact(verified.stderr or verified.stdout or "Hermes release provenance verification failed", entry)[:1000]
        if "HERMES_RELEASE_PROVENANCE_FAILED" in (verified.stderr or "") + (verified.stdout or ""):
            detail = "Hermes release signature or revision verification failed"
        raise HermesError(detail, "release_provenance_failed", True)
    after = _installed_checkout_snapshot(entry)
    if before != after:
        raise HermesError("installed Hermes checkout changed during provenance verification",
                          "installed_checkout_changed", True)
    return result(True, "release_provenance_plan", remote_name, version=tag, commit=resolved,
                  status="verified", data={"current_commit": after["commit"],
                                           "origin": after["origin"],
                                           "target_commit": resolved,
                                           "signature": "verified",
                                           "installed_checkout_unchanged": True})


def install(remote_name: str, version: str = SUPPORTED_TAG, commit: str | None = None) -> dict:
    _validate_release_tag(version)
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    resolved = _resolve_commit(entry, version, _expected_commit(version, commit))
    tag, resolved = validate_release(version, resolved)
    allowed_signer = f"{HERMES_RELEASE_SIGNER} {HERMES_RELEASE_SIGNER_KEY}"
    command = (
        "set -eu; stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; repo=\"$stage/repo\"; "
        "git init -q \"$repo\"; "
        f"git -C \"$repo\" remote add origin {shlex.quote(HERMES_REPOSITORY_URL)}; "
        f"git -C \"$repo\" fetch -q --depth=1 origin refs/tags/{shlex.quote(tag)}:refs/tags/{shlex.quote(tag)}; "
        f"printf '%s\\n' {shlex.quote(allowed_signer)} > \"$stage/allowed_signers\"; chmod 600 \"$stage/allowed_signers\"; "
        f"if ! git -C \"$repo\" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=\"$stage/allowed_signers\" verify-tag {shlex.quote(tag)}; then "
        "echo HERMES_RELEASE_PROVENANCE_FAILED >&2; exit 42; fi; "
        f"actual=$(git -C \"$repo\" rev-parse refs/tags/{shlex.quote(tag)}^{{}}); "
        f"if test \"$actual\" != {shlex.quote(resolved)}; then echo HERMES_RELEASE_PROVENANCE_FAILED >&2; exit 42; fi; "
        f"git -C \"$repo\" show {shlex.quote(resolved)}:scripts/install.sh > \"$stage/install.sh\"; chmod 700 \"$stage/install.sh\"; "
        f"mkdir -p {shlex.quote(paths['repo_root'])}; bash \"$stage/install.sh\" "
        f"--branch {shlex.quote(tag)} --commit {shlex.quote(resolved)} --skip-setup --non-interactive "
        f"--dir \"$HOME/.hermes/hermes-agent\" --hermes-home \"$HOME/.hermes\"; "
        f"test \"$(git -C \"$HOME/.hermes/hermes-agent\" rev-parse HEAD)\" = {shlex.quote(resolved)}; "
        "if git -C \"$HOME/.hermes/hermes-agent\" remote get-url origin >/dev/null 2>&1; then "
        f"test \"$(git -C \"$HOME/.hermes/hermes-agent\" remote get-url origin)\" = {shlex.quote(HERMES_REPOSITORY_URL)}; "
        f"else git -C \"$HOME/.hermes/hermes-agent\" remote add origin {shlex.quote(HERMES_REPOSITORY_URL)}; fi; "
        "test -x \"$HOME/.hermes/hermes-agent/venv/bin/hermes\"; mkdir -p \"$HOME/.local/bin\"; "
        "launcher_tmp=\"$HOME/.local/bin/hermes.install.$$\"; "
        "printf '%s\\n' '#!/usr/bin/env bash' 'unset PYTHONPATH' 'unset PYTHONHOME' "
        "'exec \"$HOME/.hermes/hermes-agent/venv/bin/hermes\" \"$@\"' > \"$launcher_tmp\"; "
        "chmod 700 \"$launcher_tmp\"; mv \"$launcher_tmp\" \"$HOME/.local/bin/hermes\"; "
        "\"$HOME/.local/bin/hermes\" --version >/dev/null"
    )
    installed = _ssh(entry, command, timeout=1800)
    if installed.returncode != 0:
        detail = _redact(installed.stderr or installed.stdout or "Hermes installation failed", entry)[:1000]
        if "HERMES_RELEASE_PROVENANCE_FAILED" in (installed.stderr or "") + (installed.stdout or ""):
            raise HermesError("Hermes release signature or revision verification failed", "release_provenance_failed")
        raise HermesError(f"Hermes installation failed: {detail}", "remote_command_failed", True)
    version_res = _checked(entry, f"{paths['launcher']} --version", what="Hermes version check failed")
    state = _remote_state_read(entry, paths)
    state["installation"] = {"release_tag": tag, "commit": resolved, "status": "installed"}
    _remote_state_write(entry, paths, state)
    return result(True, "install", remote_name, version=tag, commit=resolved, status="installed",
                  data={"launcher": paths["launcher"], "reported_version": (version_res.stdout or "").strip()[:200]})


def setup(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    if entry.get(HERMES_STATE_REPO_KEY):
        _checked(entry, _state_restore_command(paths, _state_repo(entry)), timeout=300,
                 what="Hermes state restore during setup failed")
    profile = render_profile(paths["sandbox_home"], paths["sb"])
    payload = base64.b64encode(json.dumps(profile).encode()).decode()
    # Hermes' managed virtualenv supplies PyYAML; use it remotely so unrelated
    # upstream config keys survive the merge rather than concatenating YAML.
    command = f"""set -eu
mkdir -p \"$HOME/.hermes\" {shlex.quote(paths['repo_root'])}
test -x {paths['launcher']}
payload={shlex.quote(payload)}
hermes_bin={paths['launcher']}
run_hermes() {{
  if timeout --foreground 45 "$hermes_bin" "$@"; then
    return 0
  else
    rc=$?
  fi
  printf 'HERMES_SETUP_STEP_FAILED:%s:%s:%s\n' "${{1:-unknown}}" "${{2:-unknown}}" "$rc" >&2
  return "$rc"
}}
run_hermes --version >/dev/null
if test -f "$HOME/.hermes/sandbox-integration.json"; then
  cp "$HOME/.hermes/sandbox-integration.json" "$HOME/.hermes/sandbox-integration.json.backup"
  chmod 600 "$HOME/.hermes/sandbox-integration.json.backup"
fi
{_routing_setup_command({'launcher': 'run_hermes', 'sandbox_home': paths['sandbox_home'], 'sb': paths['sb']})}
python3 - <<'PY'
import base64, json, pathlib
p = pathlib.Path.home() / '.hermes' / 'sandbox-integration.json'
p.write_text(base64.b64decode({payload!r}).decode())
p.chmod(0o600)
PY
"""
    _checked(entry, command, timeout=180, what="Hermes setup failed")
    _install_cron_scripts(entry)
    state_sync_status = None
    if entry.get(HERMES_STATE_REPO_KEY):
        synced = _checked(entry, _state_sync_command(paths, _state_repo(entry)), timeout=300,
                          what="Hermes state sync during setup failed")
        state_sync_status = (synced.stdout or "").strip().splitlines()[-1:] or ["unknown"]
    state = _remote_state_read(entry, paths)
    state.setdefault("installation", {})["status"] = "configured"
    state["profile"] = {"sandbox_home": paths["sandbox_home"], "sandbox_sb": paths["sb"]}
    _remote_state_write(entry, paths, state)
    return result(True, "setup", remote_name, status="configured",
                  data={"mcp_server": "sandbox", "parallel_calls": False, "full_catalog": True,
                        "state_sync": state_sync_status[0] if state_sync_status else None})


def status(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    command = (
        f"if test -x {paths['launcher']}; then v=$({paths['launcher']} --version 2>/dev/null | head -n1); "
        f"else v=''; fi; test -x {shlex.quote(paths['sb'])}; "
        "printf '%s\\n' \"$v\""
    )
    res = _ssh(entry, command, timeout=20)
    healthy = res.returncode == 0 and bool((res.stdout or "").strip())
    data = {"direct_sb": res.returncode == 0,
            "reported_version": _redact((res.stdout or "").strip(), entry)[:200]}
    if not healthy:
        return result(False, "status", remote_name, status="absent", data=data,
                      error=HermesError("Hermes is not installed or Sandbox sb is unavailable", "not_ready"))
    state = _remote_state_read(entry, paths)
    running = sum(session.get("state") == "running" for session in state["sessions"].values())
    lifecycle = "running" if running else "configured" if state.get("profile") else "installed"
    data["lifecycle"] = lifecycle
    data["running_sessions"] = running
    return result(True, "status", remote_name, status=lifecycle, data=data)


def _cron_snapshot(entry: dict) -> dict:
    """Read only non-secret scheduler metadata from the default Hermes home."""
    program = r'''
import base64, hashlib, json
import re
import sys
from pathlib import Path

path = Path.home() / ".hermes" / "cron" / "jobs.json"
data = json.loads(path.read_text()) if path.exists() else {"jobs": []}
guard = base64.b64decode(sys.argv[1]).decode()
config_path = Path.home() / ".hermes" / "config.yaml"

def config_effort():
    try:
        lines = config_path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    agent_indent = None
    for line in lines:
        direct = re.match(r"^\s*agent\.reasoning_effort\s*:\s*([^#]+)", line)
        if direct:
            return direct.group(1).strip().strip("\"'")
        match = re.match(r"^(\s*)agent\s*:\s*(?:#.*)?$", line)
        if match:
            agent_indent = len(match.group(1))
            continue
        if agent_indent is None:
            continue
        if line.strip() and len(line) - len(line.lstrip()) <= agent_indent:
            agent_indent = None
            continue
        nested = re.match(r"^\s*reasoning_effort\s*:\s*([^#]+)", line)
        if nested:
            return nested.group(1).strip().strip("\"'")
    return None

def guarded_hash(value):
    if not isinstance(value, str):
        return None
    if "<!-- SANDBOX_CRON_GUARD_BEGIN -->" not in value:
        value = value.rstrip() + "\n\n" + guard + "\n"
    return hashlib.sha256(value.encode()).hexdigest()

def safe_effort(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"(?:none|minimal|low|medium|high|xhigh|max)", value) else None

configured_effort = safe_effort(config_effort())
safe = []
for job in data.get("jobs", []):
    if not isinstance(job, dict):
        continue
    reason = ""
    job_id = str(job.get("id") or "")
    dumps = sorted((Path.home() / ".hermes" / "sessions").glob(f"request_dump_cron_{job_id}_*.json"),
                   key=lambda item: item.stat().st_mtime, reverse=True)[:1]
    if dumps:
        try:
            with dumps[0].open("rb") as stream:
                stream.seek(max(0, dumps[0].stat().st_size - 65536))
                tail = stream.read().decode(errors="replace")
            if re.search(r"(?i)not supported|unsupported model", tail): reason = "unsupported_model"
            elif re.search(r"(?i)http\s*400|bad request", tail): reason = "provider_bad_request"
            elif re.search(r"(?i)authentication|unauthorized|forbidden", tail): reason = "provider_authentication"
            elif re.search(r"(?i)rate.?limit|quota exceeded|usage limit|\b429\b", tail): reason = "provider_quota"
        except OSError:
            reason = "evidence_unreadable"
    raw_script = job.get("script")
    script = raw_script if isinstance(raw_script, str) and Path(raw_script).name == raw_script else None
    script_path = Path.home() / ".hermes" / "scripts" / script if script else None
    try:
        script_sha256 = hashlib.sha256(script_path.read_bytes()).hexdigest() if script_path and script_path.is_file() else None
    except OSError:
        script_sha256 = None
    safe.append({
        "id": job.get("id"),
        "name": job.get("name"),
        "enabled": job.get("enabled"),
        "state": job.get("state"),
        "schedule": job.get("schedule_display") or job.get("schedule"),
        "workdir": job.get("workdir"),
        "deliver": job.get("deliver") if isinstance(job.get("deliver"), str) else None,
        "provider_snapshot": job.get("provider_snapshot") or job.get("provider"),
        "model_snapshot": job.get("model_snapshot") or job.get("model"),
        "reasoning_effort_snapshot": (
            safe_effort(job.get("reasoning_effort_snapshot"))
            or safe_effort(job.get("reasoning_effort"))
            or safe_effort(job.get("model_reasoning_effort"))
            or configured_effort
        ),
        "no_agent": bool(job.get("no_agent")),
        "prompt_sha256": guarded_hash(job.get("prompt")) if not job.get("no_agent") else None,
        "script": script,
        "script_sha256": script_sha256,
        "last_status": job.get("last_status"),
        "last_error": job.get("last_error"),
        "last_run_at": job.get("last_run_at"),
        "next_run_at": job.get("next_run_at"),
        "evidence_reason": reason,
    })
print(json.dumps({"jobs": safe}, sort_keys=True))
'''
    guard = base64.b64encode(SCHEDULE_GUARD.encode()).decode()
    res = _checked(entry, f"python3 -c {shlex.quote(program)} {shlex.quote(guard)}", timeout=20,
                   what="Hermes cron metadata read failed")
    try:
        payload = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HermesError("Hermes cron metadata was invalid", "invalid_cron_state") from exc
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise HermesError("Hermes cron jobs collection was invalid", "invalid_cron_state")
    return {"jobs": jobs}


def cron_list(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    snapshot = _cron_snapshot(entry)
    return result(True, "cron_list", remote_name, status="ok", data=snapshot)


def cron_output(remote_name: str, job_id: str, lines: int = 200) -> dict:
    """Return the bounded latest saved output for one validated cron job."""
    valid_id = _valid_cron_job_id(job_id)
    if not isinstance(lines, int) or not 1 <= lines <= 2000:
        raise HermesError("cron output lines must be between 1 and 2000", "invalid_lines")
    entry = _require_remote(remote_name)
    program = r'''
import json, re, sys
from pathlib import Path
job_id, line_limit = sys.argv[1], int(sys.argv[2])
cron_root = Path.home() / ".hermes" / "cron"
root = cron_root / "output" / job_id
files = sorted(root.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True) if root.is_dir() else []
if not files:
    path = cron_root / f"sandbox-trigger-{job_id}.log"
    if not path.is_file():
        print(json.dumps({"found": False, "file": None, "output": "", "truncated": False}))
        raise SystemExit(0)
    trigger_log = True
else:
    path = files[0]
    trigger_log = False
with path.open("rb") as stream:
    stream.seek(max(0, path.stat().st_size - 131072))
    raw = stream.read().decode(errors="replace")
selected = raw.splitlines()[-line_limit:]
tail = "\n".join(selected)
text = tail.strip() if trigger_log else ""
format_supported = trigger_log
if not trigger_log:
    for marker in ("\n## Response\n", "\n## Error\n", "\n---\n"):
        if marker in tail:
            text = tail.rsplit(marker, 1)[1].strip()
            format_supported = True
            break
    if not format_supported:
        status = re.search(r"(?m)^\*\*Status:\*\*\s*([^\n]+)$", tail)
        if status:
            text = "Status: " + status.group(1).strip()
            format_supported = True
secret_like = bool(re.search(r"(?i)(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{24,}|BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY|(?:api[_-]?key|token|password|passphrase|secret|authorization)\s*[:=]\s*['\"]?[^\s'\"]{8,})", text))
print(json.dumps({"found": True, "file": path.name,
                  "output": "" if secret_like else text,
                  "format_supported": format_supported, "secret_like": secret_like,
                  "source": "trigger-log" if trigger_log else "saved-output",
                  "truncated": path.stat().st_size > 131072 or len(raw.splitlines()) > line_limit}))
'''
    res = _checked(
        entry,
        f"python3 -c {shlex.quote(program)} {shlex.quote(valid_id)} {lines}",
        timeout=20,
        what="Hermes cron output read failed",
    )
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HermesError("Hermes cron output was invalid", "invalid_cron_output") from exc
    data["output"] = _redact(str(data.get("output") or ""), entry)
    output_status = "never_run"
    if data.get("found"):
        output_status = "withheld" if data.get("secret_like") or not data.get("format_supported", True) else "available"
    return result(True, "cron_output", remote_name,
                  status=output_status,
                  job_id=valid_id, data=data)


def cron_validate(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    snapshot = _cron_snapshot(entry)
    invalid = audit_jobs(snapshot["jobs"])
    return result(
        not invalid,
        "cron_validate",
        remote_name,
        status="valid" if not invalid else "invalid",
        data={"job_count": len(snapshot["jobs"]), "invalid": invalid},
        error=None if not invalid else HermesError(
            "one or more Hermes cron jobs have invalid model routing",
            "invalid_cron_routing",
        ),
    )


def cron_catalog(remote_name: str) -> dict:
    """Render the committed desired cron catalog without remote mutation."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    try:
        catalog = load_catalog()
        jobs = [render_entry(item, paths) for item in catalog["jobs"]]
    except (ValueError, KeyError, OSError) as exc:
        raise HermesError(str(exc), "invalid_cron_catalog") from exc
    safe_jobs = [
        {key: value for key, value in job.items() if key != "prompt"}
        for job in jobs
    ]
    return result(True, "cron_catalog", remote_name, status="valid", data={
        "schema_version": catalog["schema_version"],
        "fingerprint": catalog_fingerprint(catalog),
        "jobs": safe_jobs,
    })


def _install_cron_scripts(entry: dict) -> None:
    payload = {
        path.name: base64.b64encode(path.read_bytes()).decode()
        for path in sorted(scripts_path().glob("*.py"))
    }
    program = r'''
import base64, json, os, sys, tempfile
from pathlib import Path
payload = json.loads(base64.b64decode(sys.argv[1]))
root = Path.home() / ".hermes" / "scripts"
root.mkdir(mode=0o700, parents=True, exist_ok=True)
for name, encoded in payload.items():
    if Path(name).name != name or not name.endswith(".py"):
        raise SystemExit(3)
    fd, temp = tempfile.mkstemp(prefix=name + ".", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(base64.b64decode(encoded))
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temp, 0o700)
        os.replace(temp, root / name)
    finally:
        if os.path.exists(temp): os.unlink(temp)
'''
    encoded = base64.b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    _checked(entry, f"python3 -c {shlex.quote(program)} {shlex.quote(encoded)}", timeout=30,
             what="Hermes cron script installation failed")


def _create_catalog_job(entry: dict, paths: dict, desired: dict) -> str:
    before = {job.get("id") for job in _cron_snapshot(entry)["jobs"]}
    args = ["cron", "create", desired["schedule"]]
    if desired["kind"] == "agent":
        args.append(desired["prompt"])
    args += ["--name", desired["name"], "--deliver", desired["deliver"]]
    if desired.get("workdir"):
        args += ["--workdir", desired["workdir"]]
    if desired["kind"] == "script":
        args += ["--script", desired["script"], "--no-agent"]
    # ``launcher`` is a trusted remote-shell expression containing ``$HOME``;
    # quoting it would turn the dollar sign literal and make the executable
    # disappear. Every catalog-controlled argument remains shell-quoted.
    command = paths["launcher"] + " " + " ".join(shlex.quote(part) for part in args)
    res = _ssh(entry, command, timeout=45)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "cron create failed", entry)[:1000],
                          "cron_create_failed", True)
    after = {job.get("id") for job in _cron_snapshot(entry)["jobs"]}
    created = [value for value in after - before if isinstance(value, str) and _CRON_JOB_RE.fullmatch(value)]
    if len(created) != 1:
        raise HermesError("cron creation did not produce exactly one job", "invalid_cron_response")
    if desired["kind"] == "agent":
        _set_cron_route(entry, created[0], desired["profile"])
    return created[0]


def _prepare_catalog_workdir(entry: dict, paths: dict, desired: dict) -> str | None:
    """Create or safely fast-forward the dedicated managed agent worktree."""
    workdir = desired.get("workdir")
    if not workdir:
        return None
    exists = _ssh(entry, f"test -d {shlex.quote(workdir)}", timeout=15)
    managed = _MANAGED_CATALOG_WORKTREES.get(str(desired.get("name") or ""))
    if exists.returncode == 0 and not managed:
        return None
    if not managed or desired.get("kind") != "agent":
        raise HermesError("catalog work directory is missing: " + desired["name"], "cron_workdir_missing")
    repository, branch = managed
    source = f"{paths['repo_root']}/{repository}"
    parent = str(PurePosixPath(workdir).parent)
    lock = f"$HOME/.hermes/locks/worktree-{desired['name']}.lock"
    tick_lock = "$HOME/.hermes/cron/.tick.lock"
    lock_command = (
        f"mkdir -p $HOME/.hermes/cron $HOME/.hermes/locks; exec 8>{tick_lock}; flock -w 30 8; "
        f"exec 9>{lock}; flock -w 30 9; "
    )
    if exists.returncode != 0:
        command = (
            f"set -eu; {lock_command}test -d {shlex.quote(source + '/.git')}; "
            f"mkdir -p {shlex.quote(parent)}; "
            f"if git -C {shlex.quote(source)} show-ref --verify --quiet refs/heads/{shlex.quote(branch)}; "
            f"then git -C {shlex.quote(source)} worktree add {shlex.quote(workdir)} {shlex.quote(branch)}; "
            f"else git -C {shlex.quote(source)} worktree add -b {shlex.quote(branch)} {shlex.quote(workdir)} HEAD; fi"
        )
        prepared = _ssh(entry, command, timeout=60)
        if prepared.returncode != 0:
            raise HermesError("could not create the managed cron worktree", "cron_workdir_prepare_failed", True)
        return workdir
    command = (
        f"set -eu; {lock_command}git -C {shlex.quote(workdir)} diff --quiet; "
        f"git -C {shlex.quote(workdir)} diff --cached --quiet; "
        f"target=$(git -C {shlex.quote(source)} rev-parse HEAD); "
        f"git -C {shlex.quote(workdir)} merge --ff-only \"$target\" >/dev/null"
    )
    prepared = _ssh(entry, command, timeout=60)
    if prepared.returncode != 0:
        raise HermesError(
            "managed cron worktree is dirty, divergent, or could not be fast-forwarded; review it before reconciliation",
            "cron_workdir_not_clean", True,
        )
    return workdir


def _bootstrap_lenzora_speckit(entry: dict, paths: dict, desired: dict) -> None:
    """Make the committed Sandbox Spec-Kit workflow available only in the TODO worktree."""
    if desired.get("name") != "lenzora-todo-task":
        return
    workdir = desired.get("workdir")
    if not isinstance(workdir, str) or not workdir.startswith(paths["worktrees"] + "/"):
        raise HermesError("invalid Lenzora TODO worktree", "cron_workdir_prepare_failed")
    runtime = f"{paths['sandbox_home']}/sb-src"
    skills = " ".join(shlex.quote(name) for name in _LENZORA_SPECKIT_SKILLS)
    command = (
        f"set -eu; runtime={shlex.quote(runtime)}; worktree={shlex.quote(workdir)}; "
        "test -d \"$worktree\"; test -d \"$runtime/.specify/templates\"; "
        "test -d \"$runtime/.specify/scripts/bash\"; "
        "mkdir -p \"$worktree/.agents/skills\" \"$worktree/.specify\"; "
        f"for skill in {skills}; do test -f \"$runtime/skills/$skill/SKILL.md\"; "
        "if test ! -e \"$worktree/.agents/skills/$skill\"; then cp -a \"$runtime/skills/$skill\" \"$worktree/.agents/skills/$skill\"; fi; done; "
        "for part in templates scripts; do if test ! -e \"$worktree/.specify/$part\"; "
        "then cp -a \"$runtime/.specify/$part\" \"$worktree/.specify/$part\"; fi; done; "
        "exclude=$(git -C \"$worktree\" rev-parse --git-path info/exclude); mkdir -p \"$(dirname \"$exclude\")\"; touch \"$exclude\"; "
        "for pattern in /.agents/ /.specify/; do grep -Fqx \"$pattern\" \"$exclude\" || printf '%s\\n' \"$pattern\" >> \"$exclude\"; done"
    )
    _checked(entry, command, timeout=60, what="Lenzora Spec-Kit bootstrap failed")


def cron_reconcile(remote_name: str, confirm: bool = False, force_replace: bool = False) -> dict:
    """Preview or apply the committed cron catalog as one controlled replacement."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    try:
        catalog = load_catalog()
        desired = [render_entry(item, paths) for item in catalog["jobs"] if item.enabled]
        plan = reconciliation_plan(catalog, _cron_snapshot(entry)["jobs"], force_replace=force_replace, paths=paths)
    except (ValueError, KeyError, OSError) as exc:
        raise HermesError(str(exc), "invalid_cron_catalog") from exc
    plan["requires_confirm"] = bool(plan["changes"] and not plan["blocked_by"])
    if plan["blocked_by"]:
        return result(False, "cron_reconcile", remote_name, status="blocked", data=plan,
                      error=HermesError("observed cron state cannot be verified safely", "cron_reconcile_blocked"))
    if not confirm or not plan["changes"]:
        return result(True, "cron_reconcile", remote_name,
                      status="planned" if plan["changes"] else "converged", data=plan)

    prepared_workdirs = []
    for item in desired:
        prepared = _prepare_catalog_workdir(entry, paths, item)
        if prepared:
            prepared_workdirs.append(prepared)
            _bootstrap_lenzora_speckit(entry, paths, item)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"$HOME/.hermes/cron/backups/jobs-{stamp}.json"
    _checked(entry, "mkdir -p $HOME/.hermes/cron/backups; chmod 700 $HOME/.hermes/cron/backups; "
             f"if test -f $HOME/.hermes/cron/jobs.json; then cp $HOME/.hermes/cron/jobs.json {backup}; chmod 600 {backup}; fi",
             what="Hermes cron inventory backup failed")
    removed: list[str] = []
    created: list[dict[str, str]] = []
    try:
        for item in plan["remove"]:
            job_id = _valid_cron_job_id(item["id"])
            res = _ssh(entry, f"{paths['launcher']} cron remove {shlex.quote(job_id)}", timeout=30)
            if res.returncode != 0:
                raise HermesError("could not remove cron job " + job_id, "cron_remove_failed", True)
            removed.append(job_id)
        _install_cron_scripts(entry)
        # The pinned Hermes release has one global reasoning-effort setting.
        # The desired catalog intentionally has one agent profile, so setting
        # it separately from the model keeps the effective provider request valid.
        agent_efforts = {scheduled_route(item["profile"]).effort for item in desired if item["kind"] == "agent"}
        if len(agent_efforts) > 1:
            raise HermesError("catalog requires incompatible per-job reasoning efforts", "invalid_cron_catalog")
        if agent_efforts:
            effort = next(iter(agent_efforts))
            _checked(entry, f"{paths['launcher']} config set agent.reasoning_effort {shlex.quote(effort)} >/dev/null",
                     what="Hermes reasoning effort configuration failed")
        for item in desired:
            job_id = _create_catalog_job(entry, paths, item)
            created.append({"id": job_id, "name": item["name"]})
    except HermesError as exc:
        partial = {**plan, "removed_ids": removed, "created": created,
                   "recovery": "rerun confirmed reconciliation; protected pre-change jobs.json was retained"}
        partial["prepared_workdirs"] = prepared_workdirs
        return result(False, "cron_reconcile", remote_name, status="partial", data=partial, error=exc)

    final = reconciliation_plan(catalog, _cron_snapshot(entry)["jobs"], force_replace=False, paths=paths)
    if final["changes"]:
        return result(False, "cron_reconcile", remote_name, status="partial",
                      data={**final, "removed_ids": removed, "created": created,
                            "prepared_workdirs": prepared_workdirs,
                            "recovery": "inspect cron health and rerun reconciliation"},
                      error=HermesError("created cron inventory did not match the catalog", "cron_verify_failed"))
    return result(True, "cron_reconcile", remote_name, status="converged",
                  data={**final, "removed_ids": removed, "created": created,
                        "prepared_workdirs": prepared_workdirs})


def _set_cron_route(entry: dict, job_id: str, profile: str) -> dict:
    route = scheduled_route(profile)
    program = r'''
import base64
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

job_id, provider, model, guard_encoded = sys.argv[1:5]
guard = base64.b64decode(guard_encoded).decode()
root = Path.home() / ".hermes" / "cron"
path = root / "jobs.json"
lock_path = root / ".tick.lock"
with lock_path.open("a+") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    data = json.loads(path.read_text())
    matches = [job for job in data.get("jobs", []) if job.get("id") == job_id]
    if len(matches) != 1:
        raise SystemExit(4)
    job = matches[0]
    job["provider_snapshot"] = provider
    job["model_snapshot"] = model
    if "provider" in job:
        job["provider"] = provider
    if "model" in job:
        job["model"] = model
    prompt = str(job.get("prompt") or "")
    if "<!-- SANDBOX_CRON_GUARD_BEGIN -->" not in prompt:
        job["prompt"] = prompt.rstrip() + "\n\n" + guard + "\n"
    fd, temp_name = tempfile.mkstemp(prefix="jobs.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w") as temp:
            json.dump(data, temp, indent=2, sort_keys=True)
            temp.write("\n")
            temp.flush()
            os.fsync(temp.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
print(json.dumps({"job_id": job_id, "provider": provider, "model": model}))
'''
    command = "python3 -c {} {} {} {} {}".format(
        shlex.quote(program),
        shlex.quote(job_id),
        shlex.quote(route.provider),
        shlex.quote(route.model),
        shlex.quote(base64.b64encode(SCHEDULE_GUARD.encode()).decode()),
    )
    res = _ssh(entry, command, timeout=20)
    if res.returncode == 4:
        raise HermesError("Hermes cron job was not found", "cron_job_not_found")
    if res.returncode != 0:
        detail = _redact(res.stderr or res.stdout or "Hermes cron routing update failed", entry)[:1000]
        raise HermesError(f"Hermes cron routing update failed: {detail}", "cron_route_failed", True)
    return {"job_id": job_id, "profile": route.profile, "provider": route.provider,
            "model": route.model, "effort": route.effort}


def cron_route(remote_name: str, job_id: str, profile: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("cron routing changes require --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    valid_id = _valid_cron_job_id(job_id)
    try:
        route = _set_cron_route(entry, valid_id, profile)
    except ValueError as exc:
        raise HermesError(str(exc), "invalid_cron_profile") from exc
    return result(True, "cron_route", remote_name, status="updated",
                  job_id=valid_id, data=route)


def cron_create(remote_name: str, schedule: str, prompt: str, *, name: str | None,
                workdir: str | None, profile: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("cron creation requires --confirm", "confirmation_required")
    if not isinstance(schedule, str):
        raise HermesError("cron schedule must be between 1 and 128 characters", "invalid_cron_schedule")
    schedule = schedule.strip()
    if not schedule or len(schedule) > 128 or any(char in schedule for char in "\n\r\0"):
        raise HermesError("cron schedule must be between 1 and 128 characters", "invalid_cron_schedule")
    if not isinstance(prompt, str) or not prompt or len(prompt) > 32000 or "\0" in prompt:
        raise HermesError("prompt must be between 1 and 32000 characters", "invalid_prompt")
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 120 or
                             any(char in name for char in "\n\r\0")):
        raise HermesError("cron name must be between 1 and 120 characters", "invalid_cron_name")
    if workdir is not None:
        if not isinstance(workdir, str) or any(char in workdir for char in "\r\n\0"):
            raise HermesError("cron workdir must be an absolute normalized path", "invalid_cron_workdir")
        path = PurePosixPath(workdir)
        if not path.is_absolute() or ".." in path.parts:
            raise HermesError("cron workdir must be an absolute normalized path", "invalid_cron_workdir")
    try:
        scheduled_route(profile)
    except ValueError as exc:
        raise HermesError(str(exc), "invalid_cron_profile") from exc
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    before = {job.get("id") for job in _cron_snapshot(entry)["jobs"]}
    args = ["cron", "create", schedule, prompt, "--deliver", "local"]
    if name:
        args += ["--name", name.strip()]
    if workdir:
        args += ["--workdir", workdir]
    command = paths["launcher"] + " " + " ".join(shlex.quote(part) for part in args)
    res = _ssh(entry, command, timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "Hermes cron creation failed", entry)[:1000],
                          "cron_create_failed", True)
    after = {job.get("id") for job in _cron_snapshot(entry)["jobs"]}
    created = sorted(job_id for job_id in after - before if isinstance(job_id, str))
    if len(created) != 1 or not _CRON_JOB_RE.fullmatch(created[0]):
        raise HermesError("Hermes cron creation did not produce exactly one job", "invalid_cron_response")
    job_id = created[0]
    try:
        route = _set_cron_route(entry, job_id, profile)
    except Exception:
        _ssh(entry, f"{paths['launcher']} cron pause {shlex.quote(job_id)}", timeout=20)
        raise
    return result(True, "cron_create", remote_name, status="scheduled", job_id=job_id,
                  data={"schedule": schedule, "name": name, "workdir": workdir, **route})


def cron_run(remote_name: str, job_id: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("cron run requires --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    valid_id = _valid_cron_job_id(job_id)
    # Hermes queues this command for its next scheduler tick, so it should
    # return quickly. Running it through SSH directly preserves a failed
    # trigger's exit status instead of reporting a detached-process success.
    res = _ssh(entry, f"{paths['launcher']} cron run --accept-hooks {shlex.quote(valid_id)}", timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "Hermes cron trigger failed", entry)[:1000],
                          "cron_run_failed", True)
    return result(True, "cron_run", remote_name, status="triggered", job_id=valid_id)


def _cron_request_evidence(entry: dict, job_id: str, since_epoch: float = 0) -> dict:
    """Reduce correlated request dumps to a safe failure classification remotely."""
    program = r'''
import json, re, sys
from pathlib import Path
job_id, since = sys.argv[1], float(sys.argv[2])
root = Path.home() / ".hermes" / "sessions"
files = sorted(root.glob(f"request_dump_cron_{job_id}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
files = [p for p in files if p.stat().st_mtime >= since][:3]
patterns = [
    (re.compile(r"(?i)not supported|unsupported model"), "unsupported_model"),
    (re.compile(r"(?i)http\s*400|bad request"), "provider_bad_request"),
    (re.compile(r"(?i)authentication|unauthorized|forbidden"), "provider_authentication"),
    (re.compile(r"(?i)rate.?limit|quota exceeded|usage limit|\b429\b"), "provider_quota"),
]
reason = ""
for path in files:
    try: text = path.read_text(errors="replace")[-65536:]
    except OSError: continue
    for pattern, label in patterns:
        if pattern.search(text): reason = label; break
    if reason: break
print(json.dumps({"files_checked": len(files), "failure": bool(reason), "reason": reason}))
'''
    res = _checked(entry, "python3 -c {} {} {}".format(
        shlex.quote(program), shlex.quote(job_id), shlex.quote(str(since_epoch))),
        timeout=20, what="Hermes request evidence inspection failed")
    try:
        evidence = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HermesError("Hermes request evidence was invalid", "invalid_cron_evidence") from exc
    return evidence if isinstance(evidence, dict) else {}


def cron_verify(remote_name: str, job_id: str, timeout: int, confirm: bool) -> dict:
    """Run one cron synchronously and cross-check terminal metadata and request evidence."""
    if not confirm:
        raise HermesError("verified cron run requires --confirm", "confirmation_required")
    if timeout < 10 or timeout > 7200:
        raise HermesError("verified cron timeout must be between 10 and 7200 seconds", "invalid_timeout")
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    valid_id = _valid_cron_job_id(job_id)
    before = next((job for job in _cron_snapshot(entry)["jobs"] if job.get("id") == valid_id), None)
    if before is None:
        raise HermesError("Hermes cron job was not found", "cron_job_not_found")
    route_issues = audit_jobs([before])
    if route_issues:
        raise HermesError(route_issues[0]["reason"], "invalid_cron_routing")
    started = time.time()
    # Hermes queues `cron run` for its next scheduler tick; it is not a
    # synchronous job execution. Poll the durable scheduler metadata instead
    # of treating the trigger command's exit status as a completed run.
    res = _ssh(entry, f"{paths['launcher']} cron run --accept-hooks {shlex.quote(valid_id)}", timeout=30)
    after = before
    deadline = started + timeout
    while True:
        candidate = next((job for job in _cron_snapshot(entry)["jobs"] if job.get("id") == valid_id), None)
        if candidate is not None:
            after = candidate
        if after.get("last_run_at") != before.get("last_run_at") or time.time() >= deadline:
            break
        time.sleep(min(2.0, max(0.0, deadline - time.time())))
    evidence = _cron_request_evidence(entry, valid_id, started - 2)
    derived = effective_job_status(after, evidence.get("reason", "") if evidence.get("failure") else "")
    transitioned = after.get("last_run_at") != before.get("last_run_at")
    ok = res.returncode == 0 and transitioned and derived["effective_status"] in {"ok", "idle_ok"}
    data = {
        "name": after.get("name"), "transitioned": transitioned,
        "prior_run_at": before.get("last_run_at"), "observed_run_at": after.get("last_run_at"),
        "upstream_status": after.get("last_status"), **derived,
        "evidence": evidence, "elapsed_seconds": round(time.time() - started, 2),
    }
    error = None if ok else HermesError(
        "verified cron execution failed: " + (evidence.get("reason") or "no successful terminal transition"),
        "cron_verified_run_failed", True)
    return result(ok, "cron_verify", remote_name, status=derived["effective_status"],
                  job_id=valid_id, data=data, error=error)


def _mcp_contract_probe(paths: dict) -> str:
    """Return a remote command that verifies only the owned MCP contract.

    The upstream display command does not guarantee a raw MCP mapping. The
    actual Hermes config file is deliberately consumed remotely by awk and
    reduced to booleans, so doctor never transports config or credential-like
    values back over SSH.
    """
    program = r'''
BEGIN { in_servers=0; in_sandbox=0; found=0; filtered=0 }
function value(line, key) {
  sub("^[[:space:]]*" key ":[[:space:]]*", "", line)
  gsub(/^[\"']|[\"']$/, "", line)
  return line
}
/^mcp_servers:[[:space:]]*$/ { in_servers=1; next }
in_servers && /^[^[:space:]]/ { in_servers=0; in_sandbox=0 }
in_servers && /^  sandbox:[[:space:]]*$/ { in_sandbox=1; found=1; next }
in_sandbox && /^  [^[:space:]][^:]*:[[:space:]]*/ { in_sandbox=0 }
!in_sandbox { next }
{
  line=$0
  sub(/^[[:space:]]+/, "", line)
  if (line ~ /^SANDBOX_HOME:/) home=(value(line, "SANDBOX_HOME") == expected_home)
  else if (line ~ /^enabled:/) enabled=(value(line, "enabled") == "true")
  else if (line ~ /^connect_timeout:/) connect_timeout=(value(line, "connect_timeout") == "60")
  else if (line ~ /^timeout:/) timeout=(value(line, "timeout") == "1200")
  else if (line ~ /^supports_parallel_tool_calls:/) sequential=(value(line, "supports_parallel_tool_calls") == "false")
  else if (line ~ /^resources:/) resources=(value(line, "resources") == "true")
  else if (line ~ /^prompts:/) prompts=(value(line, "prompts") == "true")
  else if (line ~ /^(include|exclude):/) filtered=1
}
END {
  printf "sandbox_mcp_contract_found=%d\n", found
  printf "sandbox_mcp_contract_home=%d\n", home
  printf "sandbox_mcp_contract_enabled=%d\n", enabled
  printf "sandbox_mcp_contract_connect_timeout=%d\n", connect_timeout
  printf "sandbox_mcp_contract_timeout=%d\n", timeout
  printf "sandbox_mcp_contract_sequential=%d\n", sequential
  printf "sandbox_mcp_contract_resources=%d\n", resources
  printf "sandbox_mcp_contract_prompts=%d\n", prompts
  printf "sandbox_mcp_contract_unfiltered=%d\n", !filtered
  exit !(found && home && enabled && connect_timeout && timeout && sequential && resources && prompts && !filtered)
}
'''
    return (
        "if test -f \"$HOME/.hermes/config.yaml\"; then "
        "cat \"$HOME/.hermes/config.yaml\" 2>/dev/null | awk "
        f"-v expected_home={shlex.quote(paths['sandbox_home'])} "
        f"{shlex.quote(program)}; else exit 1; fi"
    )


def doctor(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    mcp_contract = _mcp_contract_probe(paths)
    command = (
        "for item in git docker python3 systemctl flock setsid; do "
        "if command -v \"$item\" >/dev/null 2>&1; then printf '%s=1\\n' \"$item\"; "
        "else printf '%s=0\\n' \"$item\"; fi; done; "
        f"if test -x {paths['launcher']}; then echo hermes=1; else echo hermes=0; fi; "
        f"if test -x {shlex.quote(paths['sb'])}; then echo sandbox_sb=1; else echo sandbox_sb=0; fi; "
        f"if test -x {paths['launcher']} && {paths['launcher']} mcp list 2>/dev/null | grep -Eq '(^|[[:space:]])sandbox([[:space:]]|$)'; "
        "then echo sandbox_mcp_config=1; else echo sandbox_mcp_config=0; fi; "
        f"if test -x {paths['launcher']}; then {mcp_contract}; mcp_contract_status=$?; "
        "if test \"$mcp_contract_status\" -eq 0; then echo sandbox_mcp_contract=1; else echo sandbox_mcp_contract=0; fi; "
        "else echo sandbox_mcp_contract=0; fi; "
        f"if test -x {paths['launcher']} && {paths['launcher']} mcp test sandbox >/dev/null 2>&1; "
        "then echo sandbox_mcp=1; else echo sandbox_mcp=0; fi; "
        "if test -f \"$HOME/.hermes/sandbox-integration.json\" && test \"$(stat -c %a \"$HOME/.hermes/sandbox-integration.json\")\" = 600; "
        "then echo sandbox_profile=1; else echo sandbox_profile=0; fi; "
        "df -Pk . | tail -n1 | awk '{print \"free_kb=\" $4}'; "
        "awk '/MemAvailable:/ {print \"mem_kb=\" $2}' /proc/meminfo"
    )
    res = _ssh(entry, command, timeout=30)
    checks = dict(line.split("=", 1) for line in (res.stdout or "").splitlines() if "=" in line)
    required = ["git", "docker", "python3", "systemctl", "flock", "setsid", "hermes", "sandbox_sb", "sandbox_mcp_config", "sandbox_mcp_contract", "sandbox_mcp", "sandbox_profile"]
    healthy = res.returncode == 0 and all(checks.get(key) == "1" for key in required)
    return result(healthy, "doctor", remote_name, status="healthy" if healthy else "degraded",
                  data={"checks": checks, "mcp_configured": checks.get("sandbox_mcp_config") == "1",
                        "mcp_contract_complete": checks.get("sandbox_mcp_contract") == "1",
                        "mcp_catalog_complete": checks.get("sandbox_mcp") == "1",
                        "direct_sb": checks.get("sandbox_sb") == "1"},
                  error=None if healthy else HermesError("remote Hermes prerequisites are incomplete", "doctor_failed", True))


def _worktree_snapshot(entry: dict, paths: dict) -> list[dict]:
    program = r'''
import json, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
worktree_root = Path(sys.argv[2]).resolve()
items=[]
if root.is_dir():
  for repo in sorted(root.iterdir()):
    if not (repo / ".git").exists(): continue
    listed=subprocess.run(["git","-C",str(repo),"worktree","list","--porcelain"],text=True,capture_output=True)
    if listed.returncode: continue
    records=[]; current={}
    for line in listed.stdout.splitlines()+[""]:
      if not line:
        if current: records.append(current); current={}
      elif " " in line:
        key,value=line.split(" ",1); current[key]=value
      else: current[line]=True
    for record in records:
      path=Path(record.get("worktree","")).resolve()
      allowed = path == repo.resolve() or root in path.parents or worktree_root in path.parents
      if not allowed: continue
      status=subprocess.run(["git","-C",str(path),"status","--porcelain"],text=True,capture_output=True)
      dirty=[line[3:] for line in status.stdout.splitlines()[:100] if len(line)>3] if status.returncode==0 else []
      items.append({"repository":repo.name,"path":str(path),"head":record.get("HEAD",""),
                    "branch":record.get("branch"),"detached":bool(record.get("detached")),
                    "dirty":bool(dirty),"dirty_paths":dirty,"dirty_truncated":len(status.stdout.splitlines())>100})
print(json.dumps({"worktrees":items},sort_keys=True))
'''
    res = _checked(entry, f"python3 -c {shlex.quote(program)} {shlex.quote(paths['repo_root'])} {shlex.quote(paths['worktrees'])}",
                   timeout=60, what="Hermes worktree inventory failed")
    try:
        payload = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HermesError("Hermes worktree inventory was invalid", "invalid_worktree_inventory") from exc
    items = payload.get("worktrees", [])
    return items if isinstance(items, list) else []


def worktree_list(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    items = _worktree_snapshot(entry, _paths(entry))
    return result(True, "worktree_list", remote_name, status="dirty" if any(item.get("dirty") for item in items) else "clean",
                  data={"worktrees": items, "dirty_count": sum(bool(item.get("dirty")) for item in items)})


def _managed_worktree_path(paths: dict, name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name or ""):
        raise HermesError("managed worktree name is invalid", "invalid_worktree_name")
    return str(PurePosixPath(paths["worktrees"]) / name)


def worktree_inspect(remote_name: str, name: str) -> dict:
    """Return bounded, secret-screened evidence for one managed worktree."""
    entry = _require_remote(remote_name)
    path = _managed_worktree_path(_paths(entry), name)
    program = r'''
import hashlib, json, re, subprocess, sys
from pathlib import Path
path = Path(sys.argv[1])
credential_pattern = sys.argv[2]
def run(*args):
    return subprocess.run(["git", "-C", str(path), *args], text=True, capture_output=True)
if not path.is_dir() or run("rev-parse", "--is-inside-work-tree").returncode:
    raise SystemExit(4)
status = run("status", "--porcelain=v1")
branch = run("symbolic-ref", "--short", "HEAD")
head = run("rev-parse", "HEAD")
check = run("diff", "--check", "HEAD")
stat = run("diff", "--stat", "HEAD")
diff = run("diff", "--no-ext-diff", "--unified=3", "HEAD", "--")
untracked = run("ls-files", "--others", "--exclude-standard", "-z")
untracked_diff = []
for relative in filter(None, untracked.stdout.split("\0")):
    added = subprocess.run(
        ["git", "-C", str(path), "diff", "--no-index", "--unified=3", "/dev/null", relative],
        text=True, capture_output=True,
    )
    untracked_diff.append(added.stdout)
text = diff.stdout + "".join(untracked_diff)
if untracked_diff:
    stat_text = stat.stdout + "".join(
        subprocess.run(
            ["git", "-C", str(path), "diff", "--no-index", "--stat", "/dev/null", relative],
            text=True, capture_output=True,
        ).stdout for relative in filter(None, untracked.stdout.split("\0"))
    )
else:
    stat_text = stat.stdout
secret_like = bool(re.search(credential_pattern, text, re.IGNORECASE))
bounded = text[:65536]
print(json.dumps({"path": str(path), "branch": branch.stdout.strip() or None,
                  "status": status.stdout.splitlines()[:100], "diff_check_ok": check.returncode == 0,
                  "stat": stat_text[:12000], "diff": "" if secret_like else bounded,
                  "secret_like": secret_like, "head": head.stdout.strip(),
                  "review_id": hashlib.sha256((head.stdout.strip() + "\0" + text).encode()).hexdigest(),
                  "truncated": len(text) > len(bounded)}))
'''
    res = _checked(entry, f"python3 -c {shlex.quote(program)} {shlex.quote(path)} {shlex.quote(_CREDENTIAL_PATTERN)}", timeout=30,
                   what="Hermes managed worktree inspection failed")
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise HermesError("Hermes worktree inspection was invalid", "invalid_worktree_inventory") from exc
    if _contains_credential(data.get("diff")):
        data["secret_like"] = True
        data["diff"] = ""
    else:
        data["diff"] = _redact(str(data.get("diff") or ""), entry)
    return result(True, "worktree_inspect", remote_name,
                  status="blocked" if data.get("secret_like") or not data.get("diff_check_ok") else "reviewable",
                  data=data)


def worktree_preserve(remote_name: str, name: str, confirm: bool = False) -> dict:
    """Commit tracked reviewed changes and push the explicit managed branch."""
    inspected = worktree_inspect(remote_name, name)
    data = inspected["data"]
    if data.get("secret_like"):
        raise HermesError("worktree diff contains secret-like material", "secret_like_content")
    if not data.get("diff_check_ok"):
        raise HermesError("worktree diff check failed", "invalid_worktree_diff")
    has_untracked = any(
        str(line).startswith(("?? ", "A ", " A ", "AM ")) for line in data.get("status", [])
    )
    expected_branch = f"hermes/{name}"
    if data.get("branch") != expected_branch:
        raise HermesError("managed worktree is not on its expected branch", "unexpected_worktree_branch")
    reviewed_head = str(data.get("head") or "")
    review_id = str(data.get("review_id") or "")
    if not _COMMIT_RE.fullmatch(reviewed_head) or not re.fullmatch(r"[0-9a-f]{64}", review_id):
        raise HermesError("worktree inspection lacks a stable review identity", "invalid_worktree_review")
    if not data.get("status"):
        return result(True, "worktree_preserve", remote_name, status="clean", data=data)
    if not confirm:
        return result(True, "worktree_preserve", remote_name, status="planned",
                      data={**data, "requires_confirm": True})
    entry = _require_remote(remote_name)
    path = _managed_worktree_path(_paths(entry), name)
    message = f"chore: preserve Hermes worktree {name}"
    credential_scan = (
        "python3 -c "
        + shlex.quote("import re, sys; raise SystemExit(0 if re.search(sys.argv[1], sys.stdin.read(), re.IGNORECASE) else 1)")
        + " " + shlex.quote(_CREDENTIAL_PATTERN)
    )
    lock = f"$HOME/.hermes/locks/worktree-{name}.lock"
    tick_lock = "$HOME/.hermes/cron/.tick.lock"
    review_guard = (
        f"review_id=$( (printf '%s\\0' \"$(git -C {shlex.quote(path)} rev-parse HEAD)\"; "
        f"git -C {shlex.quote(path)} diff --cached --no-ext-diff --unified=3) | sha256sum | awk '{{print $1}}' ); "
        f"test \"$review_id\" = {shlex.quote(review_id)}; "
        if has_untracked else
        f"review_id=$( (printf '%s\\0' \"$(git -C {shlex.quote(path)} rev-parse HEAD)\"; "
        f"git -C {shlex.quote(path)} diff --no-ext-diff HEAD --) | sha256sum | awk '{{print $1}}' ); "
        f"test \"$review_id\" = {shlex.quote(review_id)}; "
    )
    command = (
        f"set -eu; mkdir -p $HOME/.hermes/cron $HOME/.hermes/locks; exec 8>{tick_lock}; flock -w 30 8; "
        f"exec 9>{lock}; flock -n 9; "
        f"test \"$(git -C {shlex.quote(path)} symbolic-ref --short HEAD)\" = {shlex.quote(expected_branch)}; "
        f"test \"$(git -C {shlex.quote(path)} rev-parse HEAD)\" = {shlex.quote(reviewed_head)}; "
        f"git -C {shlex.quote(path)} add -A; " +
        review_guard +
        f"if git -C {shlex.quote(path)} diff --cached | {credential_scan}; then exit 44; fi; "
        f"git -C {shlex.quote(path)} diff --cached --check; "
        f"git -C {shlex.quote(path)} -c user.name='Hermes Preservation' "
        f"-c user.email='hermes-preservation@users.noreply.github.com' commit -m {shlex.quote(message)} >/dev/null; "
        f"git -C {shlex.quote(path)} push -u origin HEAD:{shlex.quote(expected_branch)} >/dev/null; "
        f"git -C {shlex.quote(path)} rev-parse HEAD"
    )
    saved = _checked(entry, command, timeout=180, what="Hermes worktree preservation failed")
    commit = (saved.stdout or "").strip().splitlines()[-1]
    if not _COMMIT_RE.fullmatch(commit):
        raise HermesError("preserved worktree did not return a commit", "invalid_commit")
    return result(True, "worktree_preserve", remote_name, status="pushed", commit=commit,
                  data={"name": name, "branch": expected_branch, "path": path})


def health(remote_name: str) -> dict:
    """Return a bounded operational view without performing repair."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    service_command = (
        "if command -v systemctl >/dev/null 2>&1; then systemctl --user is-active hermes-gateway-sandbox.service 2>/dev/null || true; "
        "else echo unavailable; fi; "
        "if command -v loginctl >/dev/null 2>&1; then loginctl show-user \"$USER\" -p Linger --value 2>/dev/null || true; "
        "else echo unavailable; fi")
    with ThreadPoolExecutor(max_workers=6) as pool:
        diagnostic_future = pool.submit(doctor, remote_name)
        state_future = pool.submit(_remote_state_read, entry, paths)
        service_future = pool.submit(_ssh, entry, service_command, 30)
        gateway_future = pool.submit(_gateway_ownership, entry)
        cron_future = pool.submit(_cron_snapshot, entry)
        worktree_future = pool.submit(_worktree_snapshot, entry, paths)
        diagnostic = diagnostic_future.result()
        state = state_future.result()
        service = service_future.result()
        gateway = gateway_future.result()
        observed = cron_future.result()["jobs"]
        worktrees = worktree_future.result()
    lines = (service.stdout or "").splitlines()
    gateway_state = lines[0].strip() if lines else "unknown"
    linger = lines[1].strip().lower() if len(lines) > 1 else "unknown"
    sessions = state["sessions"].values()
    diagnostic_error = diagnostic["error"]
    cron_health = []
    for job in observed:
        evidence_reason = str(job.get("evidence_reason") or "")
        derived = effective_job_status(job, evidence_reason)
        cron_health.append({
            "id": job.get("id"), "name": job.get("name"), "kind": classify_job(job),
            "upstream_status": job.get("last_status"), **derived,
            "evidence_reason": evidence_reason,
        })
    catalog = load_catalog()
    cron_plan = reconciliation_plan(catalog, observed, paths=paths)
    degraded_reasons = []
    if not diagnostic["ok"]: degraded_reasons.append("prerequisites")
    if not gateway["healthy"]: degraded_reasons.append("gateway_ownership")
    if any(item["effective_status"] in {"failed", "invalid"} for item in cron_health): degraded_reasons.append("cron_failure")
    if cron_plan["changes"]: degraded_reasons.append("cron_drift")
    healthy = not degraded_reasons
    error = None if healthy else HermesError(
        "Hermes health is degraded: " + ", ".join(degraded_reasons), "hermes_health_degraded", True)
    return result(
        healthy, "health", remote_name, status="healthy" if healthy else "degraded",
        data={
            "checks": diagnostic["data"]["checks"],
            "gateway": {**gateway, "state": gateway_state, "linger": linger},
            "cron": {"jobs": cron_health, "drift": cron_plan["changes"],
                     "catalog_fingerprint": cron_plan["catalog_fingerprint"]},
            "worktrees": {"count": len(worktrees),
                          "dirty_count": sum(bool(item.get("dirty")) for item in worktrees)},
            "degraded_reasons": degraded_reasons,
            "sessions": {
                "running": sum(session.get("state") == "running" for session in sessions),
                "stale": sum(session.get("state") == "stale" for session in sessions),
            },
            "completed_job_retention_days": _COMPLETED_JOB_RETENTION_DAYS,
            "v2_gate": _v2_gate(state),
        },
        error=error,
    )


def _v2_gate(state: dict) -> dict:
    """Evaluate only auditable, revision-bound V2 acceptance evidence.

    This intentionally cannot manufacture a passing gate: the live acceptance
    suite is the only writer of ``gates.v2_operations`` with successful
    fault-injection and reboot evidence.
    """
    installation = state.get("installation") or {}
    current_commit = installation.get("commit")
    record = (state.get("gates") or {}).get("v2_operations") or {}
    evidence = record.get("evidence") if isinstance(record, dict) else {}
    if not isinstance(evidence, dict):
        evidence = {}
    missing = [name for name in _V2_ACCEPTANCE_CHECKS if evidence.get(name) != "passed"]
    revision_matches = bool(current_commit and record.get("commit") == current_commit)
    integration_schema_matches = record.get("integration_schema") == STATE_SCHEMA
    if not integration_schema_matches:
        missing.append("integration_schema")
    status = "passed" if record.get("status") == "passed" and revision_matches and integration_schema_matches and not missing else "pending"
    return {
        "status": status,
        "commit": current_commit,
        "recorded_at": record.get("recorded_at"),
        "revision_matches": revision_matches,
        "integration_schema_matches": integration_schema_matches,
        "missing_checks": missing,
    }


def acceptance_v2(remote_name: str) -> dict:
    """Report whether a current, complete V2 acceptance record exists."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    gate = _v2_gate(_remote_state_read(entry, paths))
    if gate["status"] != "passed":
        return result(False, "acceptance_v2", remote_name, commit=gate["commit"], status="pending",
                      data={"gate": gate},
                      error=HermesError("V2 acceptance evidence is incomplete or stale", "v2_gate_incomplete"))
    return result(True, "acceptance_v2", remote_name, commit=gate["commit"], status="passed", data={"gate": gate})


def _validate_policy(policy: dict) -> dict:
    merged = {**_DEFAULT_POLICY, **(policy or {})}
    for key, value in merged.items():
        if not isinstance(value, int) or value < 1 or value > 1_000_000:
            raise HermesError(f"{key} must be a positive integer", "invalid_resource_policy")
    return merged


def policy_show(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    res = _ssh(entry, f"if test -f {paths['policy']}; then cat {paths['policy']}; fi")
    try:
        policy = _validate_policy(json.loads(res.stdout or "{}"))
    except (json.JSONDecodeError, ValueError, HermesError) as exc:
        raise HermesError("stored resource policy is invalid", "invalid_resource_policy") from exc
    return result(True, "policy_show", remote_name, status="ready", data={"policy": policy})


def policy_set(remote_name: str, max_jobs: int | None, max_worktrees: int | None,
               min_free_disk_mb: int | None, min_free_memory_mb: int | None) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    current = policy_show(remote_name)["data"]["policy"]
    requested = {"max_jobs": max_jobs, "max_worktrees": max_worktrees,
                 "min_free_disk_mb": min_free_disk_mb, "min_free_memory_mb": min_free_memory_mb}
    policy = _validate_policy({**current, **{key: value for key, value in requested.items() if value is not None}})
    encoded = base64.b64encode(json.dumps(policy, sort_keys=True).encode()).decode()
    _checked(entry, f"mkdir -p \"$HOME/.hermes\"; echo {shlex.quote(encoded)} | base64 -d > {paths['policy']}; chmod 600 {paths['policy']}",
             what="could not store resource policy")
    return result(True, "policy_set", remote_name, status="configured", data={"policy": policy})


def _resource_preflight(entry: dict, paths: dict) -> dict:
    res = _ssh(entry, f"if test -f {paths['policy']}; then cat {paths['policy']}; fi")
    try:
        policy = _validate_policy(json.loads(res.stdout or "{}"))
    except (json.JSONDecodeError, ValueError, HermesError) as exc:
        raise HermesError("stored resource policy is invalid", "invalid_resource_policy") from exc
    probe = _ssh(entry, (
        f"df -Pm {shlex.quote(paths['sandbox_home'])} | tail -n1 | awk '{{print \"disk_mb=\" $4}}'; "
        "awk '/MemAvailable:/ {print \"memory_mb=\" int($2 / 1024)}' /proc/meminfo; "
        f"find {shlex.quote(paths['jobs'])} -type f -name '*.log' ! -exec sh -c 'test -f \"${{1%.log}}.status\"' sh {{}} \\; 2>/dev/null | wc -l | awk '{{print \"jobs=\" $1}}'; "
        f"(find {shlex.quote(paths['worktrees'])} -mindepth 2 -maxdepth 2 -type d -print 2>/dev/null; "
        f"find {shlex.quote(paths['repo_root'])} -type d -path '*/.worktrees/*' -prune -print 2>/dev/null) | "
        "wc -l | awk '{print \"worktrees=\" $1}'"), timeout=30)
    values = dict(line.split("=", 1) for line in (probe.stdout or "").splitlines() if "=" in line)
    try:
        metrics = {key: int(values.get(key, "0")) for key in ("disk_mb", "memory_mb", "jobs", "worktrees")}
    except ValueError as exc:
        raise HermesError("resource preflight returned invalid data", "resource_preflight_failed", True) from exc
    if metrics["disk_mb"] < policy["min_free_disk_mb"] or metrics["memory_mb"] < policy["min_free_memory_mb"]:
        _record_v2_evidence(entry, paths, "resource_rejection", {"reason": "disk_or_memory_floor"})
        raise HermesError("insufficient remote disk or memory for Hermes", "resource_limit", True)
    if metrics["jobs"] >= policy["max_jobs"] or metrics["worktrees"] >= policy["max_worktrees"]:
        _record_v2_evidence(entry, paths, "resource_rejection", {"reason": "concurrency_limit"})
        raise HermesError("Hermes job or worktree concurrency limit reached", "resource_limit", True)
    return {"policy": policy, "metrics": metrics}


def update_plan(remote_name: str, version: str, commit: str | None = None) -> dict:
    """Compare the installed checkout to an immutable target without mutation."""
    _validate_release_tag(version)
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    target = _resolve_commit(entry, version, _expected_commit(version, commit))
    tag, target = validate_release(version, target)
    res = _checked(entry,
                   "if test -d \"$HOME/.hermes/hermes-agent/.git\"; then "
                   "git -C \"$HOME/.hermes/hermes-agent\" rev-parse HEAD; fi",
                   what="could not read installed Hermes revision")
    current = (res.stdout or "").strip().splitlines()[-1] if (res.stdout or "").strip() else None
    if current and not _COMMIT_RE.fullmatch(current):
        raise HermesError("installed Hermes revision is invalid", "invalid_installed_revision")
    return result(True, "update_plan", remote_name, version=tag, commit=target,
                  status="up_to_date" if current == target else "update_available",
                  data={"current_commit": current, "target_commit": target,
                        "requires_confirm": current != target,
                        "services": ["hermes-gateway-sandbox.service"],
                        "backup": "create verified backup before apply",
                        "health_checks": ["hermes", "sandbox_sb", "sandbox_mcp"],
                        "rollback": "restore pre-update backup on any install or health failure"})


def _backup_archive(paths: dict, backup_id: str) -> str:
    if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", backup_id or ""):
        raise HermesError("invalid backup id", "invalid_backup_id")
    return f"{paths['sandbox_home']}/runtime/hermes-backups/{backup_id}.tar.gz"


def _ensure_backup_space(entry: dict, paths: dict) -> int:
    res = _ssh(entry, f"df -Pm {shlex.quote(paths['sandbox_home'])} | tail -n1 | awk '{{print $4}}'", timeout=30)
    try:
        free_mb = int((res.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise HermesError("could not determine free disk space for Hermes backup", "backup_space_check_failed", True) from exc
    if free_mb < _BACKUP_MIN_FREE_MB:
        raise HermesError("insufficient free disk space for a Hermes backup", "backup_insufficient_space", True)
    return free_mb


def backup_restore(remote_name: str, backup_id: str, confirm: bool,
                   *, create_pre_restore_backup: bool = True) -> dict:
    if not confirm:
        raise HermesError("backup restore requires --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    archive = _backup_archive(paths, backup_id)
    # A pre-restore snapshot is deliberately taken before stopping services or
    # replacing files, so a bad but syntactically valid archive remains
    # recoverable.  Its credentials exclusions match ordinary V2 backups.
    # A normal operator restore takes a new recovery point first. Automatic
    # update rollback already has the verified pre-update archive, and the
    # failed installer may have removed the checkout/launcher needed to make
    # another backup, so it must restore directly from that known-good point.
    # The same exception is necessary for an operator restoring a genuinely
    # missing runtime: do not turn the absent runtime into a restore blocker.
    pre_restore_id = None
    if create_pre_restore_backup:
        current_runtime = _ssh(entry, (
            "test -d \"$HOME/.hermes/hermes-agent/.git\" && "
            "test -x \"$HOME/.hermes/hermes-agent/venv/bin/hermes\" && "
            "test -f \"$HOME/.local/bin/hermes\""), timeout=30)
        if current_runtime.returncode == 0:
            pre_restore_id = backup_create(remote_name)["data"]["backup_id"]
    digest_file = archive + ".sha256"
    command = (
        f"set -eu; test -s {shlex.quote(archive)}; test -s {shlex.quote(digest_file)}; "
        f"expected=$(awk '{{print $1}}' {shlex.quote(digest_file)}); actual=$(sha256sum {shlex.quote(archive)} | awk '{{print $1}}'); "
        "test -n \"$expected\" && test \"$expected\" = \"$actual\"; "
        f"tar -tzf {shlex.quote(archive)} >/dev/null; "
        f"size_mb=$(du -m {shlex.quote(archive)} | awk '{{print $1}}'); free_mb=$(df -Pm {shlex.quote(paths['sandbox_home'])} | tail -n1 | awk '{{print $4}}'); "
        "test \"$free_mb\" -gt \"$size_mb\"; "
        "stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; tar -C \"$stage\" -xzf " + shlex.quote(archive) + "; "
        "if test -d \"$stage/home/.hermes\"; then source=\"$stage/home\"; "
        "elif test -d \"$stage/.hermes\"; then source=\"$stage\"; else exit 1; fi; "
        "commit=$(cat \"$source/.hermes/hermes-agent.commit\"); case \"$commit\" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) exit 1;; esac; "
        "test -s \"$source/.hermes/hermes-agent.pack\"; restore=\"$HOME/.hermes/hermes-agent.restore.$$\"; previous=\"$HOME/.hermes/hermes-agent.previous.$$\"; launcher_previous=\"$HOME/.local/bin/hermes.previous.$$\"; launcher_tmp=\"$HOME/.local/bin/hermes.restore.$$\"; "
        f"rm -rf \"$restore\" \"$previous\"; rm -f \"$launcher_previous\" \"$launcher_tmp\"; git init -q \"$restore\"; printf '%s\\n' \"$commit\" > \"$restore/.git/shallow\"; git -C \"$restore\" index-pack --stdin --fix-thin < \"$source/.hermes/hermes-agent.pack\" >/dev/null; git -C \"$restore\" remote add origin {shlex.quote(HERMES_REPOSITORY_URL)}; tag=$(cat \"$source/.hermes/hermes-agent.tag\" 2>/dev/null || true); if test -z \"$tag\" && test \"$commit\" = {shlex.quote(SUPPORTED_COMMIT)}; then tag={shlex.quote(SUPPORTED_TAG)}; fi; case \"$tag\" in v[0-9]*) git -C \"$restore\" update-ref refs/tags/\"$tag\" \"$commit\";; esac; git -C \"$restore\" update-ref refs/heads/hermes-backup \"$commit\"; "
        "git -C \"$restore\" checkout -q --detach \"$commit\"; test \"$(git -C \"$restore\" rev-parse HEAD)\" = \"$commit\"; tar -C \"$source/.hermes/hermes-agent\" -cf - venv | tar -C \"$restore\" -xf -; "
        "if test -d \"$source/.hermes/hermes-agent/.venv\"; then tar -C \"$source/.hermes/hermes-agent\" -cf - .venv | tar -C \"$restore\" -xf -; fi; test -x \"$restore/venv/bin/hermes\"; "
        "gateway_active=0; dashboard_active=0; if command -v systemctl >/dev/null 2>&1; then "
        "if systemctl --user is-active --quiet hermes-gateway-sandbox.service; then gateway_active=1; systemctl --user stop hermes-gateway-sandbox.service; fi; "
        "if systemctl --user is-active --quiet hermes-dashboard-sandbox.service; then dashboard_active=1; systemctl --user stop hermes-dashboard-sandbox.service; fi; fi; "
        "had_previous=0; had_launcher=0; if test -d \"$HOME/.hermes/hermes-agent\"; then mv \"$HOME/.hermes/hermes-agent\" \"$previous\"; had_previous=1; fi; if test -f \"$HOME/.local/bin/hermes\"; then cp \"$HOME/.local/bin/hermes\" \"$launcher_previous\"; had_launcher=1; fi; if test -f \"$stage/launcher/hermes\"; then cp \"$stage/launcher/hermes\" \"$launcher_tmp\"; else printf '%s\\n' '#!/usr/bin/env bash' 'unset PYTHONPATH' 'unset PYTHONHOME' 'exec \"$HOME/.hermes/hermes-agent/venv/bin/hermes\" \"$@\"' > \"$launcher_tmp\"; fi; chmod 700 \"$launcher_tmp\"; mv \"$restore\" \"$HOME/.hermes/hermes-agent\"; mv \"$launcher_tmp\" \"$HOME/.local/bin/hermes\"; "
        "if ! \"$HOME/.local/bin/hermes\" --version >/dev/null 2>&1; then rm -rf \"$HOME/.hermes/hermes-agent\"; if test \"$had_previous\" = 1; then mv \"$previous\" \"$HOME/.hermes/hermes-agent\"; fi; if test \"$had_launcher\" = 1; then mv \"$launcher_previous\" \"$HOME/.local/bin/hermes\"; else rm -f \"$HOME/.local/bin/hermes\"; fi; if command -v systemctl >/dev/null 2>&1; then if test \"$gateway_active\" = 1; then systemctl --user start hermes-gateway-sandbox.service || true; fi; if test \"$dashboard_active\" = 1; then systemctl --user start hermes-dashboard-sandbox.service || true; fi; fi; exit 1; fi; rm -rf \"$previous\"; rm -f \"$launcher_previous\"; "
        "mkdir -p \"$HOME/.hermes\"; for safe in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json; do "
        "if test -f \"$source/.hermes/$safe\"; then cp \"$source/.hermes/$safe\" \"$HOME/.hermes/$safe\"; fi; done; "
        "if test -d \"$stage/units\"; then mkdir -p \"$HOME/.config/systemd/user\"; cp \"$stage/units/\"*.service \"$HOME/.config/systemd/user/\" 2>/dev/null || true; fi; "
        "if command -v systemctl >/dev/null 2>&1; then systemctl --user daemon-reload 2>/dev/null || true; fi; "
        f"if test -f \"$stage/runtime/hermes.json\"; then tmp={shlex.quote(paths['state'])}.restore.$$; cp \"$stage/runtime/hermes.json\" \"$tmp\"; chmod 600 \"$tmp\"; mv \"$tmp\" {shlex.quote(paths['state'])}; fi; "
        "if command -v systemctl >/dev/null 2>&1; then if test \"$gateway_active\" = 1; then systemctl --user start hermes-gateway-sandbox.service; fi; if test \"$dashboard_active\" = 1; then systemctl --user start hermes-dashboard-sandbox.service; fi; fi"
    )
    _checked(entry, command, timeout=300, what="Hermes backup restore failed")
    # Provider configuration is deliberately excluded from archives. Reapply
    # only the integration-owned MCP/profile settings so a recovered launcher
    # immediately regains direct Sandbox CLI and MCP access without restoring
    # credentials or upstream session state.
    setup(remote_name)
    _record_v2_evidence(entry, paths, "backup_restore", {"backup_id": backup_id})
    return result(True, "backup_restore", remote_name, status="restored",
                  data={"backup_id": backup_id, "pre_restore_backup_id": pre_restore_id})


def update_apply(remote_name: str, version: str, commit: str | None, confirm: bool) -> dict:
    """Install an immutable target with a backup-and-restore safety net."""
    if not confirm:
        raise HermesError("Hermes update requires --confirm", "confirmation_required")
    plan = update_plan(remote_name, version, commit)
    if plan["status"] == "up_to_date":
        return plan | {"action": "update_apply", "data": {**plan["data"], "changed": False}}
    backup = backup_create(remote_name)
    backup_id = backup["data"]["backup_id"]
    entry = _require_remote(remote_name)
    quiesce = _ssh(entry, (
        "if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet hermes-gateway-sandbox.service; "
        "then systemctl --user stop hermes-gateway-sandbox.service && echo active; else echo inactive; fi"), timeout=60)
    if quiesce.returncode != 0:
        raise HermesError(_redact(quiesce.stderr or "could not quiesce Hermes gateway", entry), "update_quiesce_failed", True)
    gateway_was_active = (quiesce.stdout or "").strip().splitlines()[-1:] == ["active"]
    try:
        installed = install(remote_name, version, plan["commit"])
        _install_cron_scripts(entry)
        if gateway_was_active:
            _checked(entry, "systemctl --user start hermes-gateway-sandbox.service", timeout=60,
                     what="could not resume Hermes gateway")
        health_result = health(remote_name)
        blocking_health = [reason for reason in health_result["data"].get("degraded_reasons", [])
                           if reason != "cron_drift"]
        if blocking_health:
            raise HermesError("post-update health check failed", "update_health_failed")
    except HermesError as exc:
        try:
            backup_restore(remote_name, backup_id, True, create_pre_restore_backup=False)
            if gateway_was_active:
                _checked(entry, "systemctl --user start hermes-gateway-sandbox.service", timeout=60,
                         what="could not resume Hermes gateway after rollback")
        except (HermesError, KeyError, TypeError):
            pass
        _record_v2_evidence(entry, _paths(entry), "update_rollback", {"reason": exc.code})
        raise HermesError(f"update failed and restore was attempted: {exc}", "update_rolled_back", True) from exc
    return result(True, "update_apply", remote_name, version=installed["version"], commit=installed["commit"],
                  status="updated", data={"backup_id": backup_id, "changed": True,
                                          "gateway_resumed": gateway_was_active})


def backup_create(remote_name: str) -> dict:
    """Create an owner-only, non-secret Hermes backup on the remote."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    free_mb = _ensure_backup_space(entry, paths)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
    backup_root = f"{paths['sandbox_home']}/runtime/hermes-backups"
    archive = f"{backup_root}/{backup_id}.tar.gz"
    digest_file = archive + ".sha256"
    source_policy = (
        "from sandbox.core._hermes import _backup_forbidden_source_path as forbidden; "
        "import sys; raise SystemExit(0 if any(forbidden(path.strip()) for path in sys.stdin if path.strip()) else 1)"
    )
    command = (
        f"set -eu; mkdir -p {shlex.quote(backup_root)}; chmod 700 {shlex.quote(backup_root)}; "
        "repo=\"$HOME/.hermes/hermes-agent\"; test -d \"$repo/.git\"; "
        "stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; "
        "mkdir -p \"$stage/home/.hermes/hermes-agent\" \"$stage/runtime\" \"$stage/units\" \"$stage/launcher\"; "
        f"if git -C \"$repo\" ls-tree -r --name-only HEAD | PYTHONPATH={shlex.quote(paths['sandbox_home'] + '/sb-src')} python3 -c {shlex.quote(source_policy)}; then exit 1; fi; "
        f"git -C \"$repo\" rev-parse HEAD > \"$stage/home/.hermes/hermes-agent.commit\"; if test -f {shlex.quote(paths['state'])}; then python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print((d.get(\"installation\") or {{}}).get(\"release_tag\") or \"\")' {shlex.quote(paths['state'])} > \"$stage/home/.hermes/hermes-agent.tag\" || true; fi; if ! test -s \"$stage/home/.hermes/hermes-agent.tag\"; then git -C \"$repo\" describe --tags --exact-match HEAD 2>/dev/null > \"$stage/home/.hermes/hermes-agent.tag\" || true; fi; commit=$(cat \"$stage/home/.hermes/hermes-agent.commit\"); printf '%s\\n' \"$commit\" | git -C \"$repo\" pack-objects --stdout --revs > \"$stage/home/.hermes/hermes-agent.pack\"; "
        "test -x \"$repo/venv/bin/hermes\"; tar -C \"$repo\" -cf - venv | tar -C \"$stage/home/.hermes/hermes-agent\" -xf -; "
        "if test -d \"$repo/.venv\"; then tar -C \"$repo\" -cf - .venv | tar -C \"$stage/home/.hermes/hermes-agent\" -xf -; fi; "
        "test -f \"$HOME/.local/bin/hermes\"; cp -L \"$HOME/.local/bin/hermes\" \"$stage/launcher/hermes\"; chmod 700 \"$stage/launcher/hermes\"; "
        "for safe in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json; do "
        "if test -f \"$HOME/.hermes/$safe\"; then cp \"$HOME/.hermes/$safe\" \"$stage/home/.hermes/$safe\"; fi; done; "
        "for unit in hermes-gateway-sandbox.service hermes-dashboard-sandbox.service; do "
        "if test -f \"$HOME/.config/systemd/user/$unit\"; then cp \"$HOME/.config/systemd/user/$unit\" \"$stage/units/$unit\"; fi; done; "
        f"if test -f {shlex.quote(paths['state'])}; then cp {shlex.quote(paths['state'])} \"$stage/runtime/hermes.json\"; fi; "
        f"tar -C \"$stage\" -czf {shlex.quote(archive)} home runtime units launcher; "
        f"if tar -tzf {shlex.quote(archive)} | grep -E '^home/\\.hermes/(auth\\.json|sessions/|checkpoints/|\\.env$|credentials?($|/)|cookies?($|/))' >/dev/null; then rm -f {shlex.quote(archive)}; exit 1; fi; "
        f"chmod 600 {shlex.quote(archive)}; "
        f"sha256sum {shlex.quote(archive)} | tee {shlex.quote(digest_file)}; chmod 600 {shlex.quote(digest_file)}; "
        f"find {shlex.quote(backup_root)} -maxdepth 1 -type f -name '*.tar.gz' -printf '%T@ %p\\n' | sort -nr | "
        f"tail -n +{_BACKUP_RETENTION_COUNT + 1} | cut -d' ' -f2- | while IFS= read -r old; do rm -f \"$old\" \"$old.sha256\"; done"
    )
    res = _checked(entry, command, timeout=300, what="Hermes backup failed")
    digest = (res.stdout or "").split()[0] if (res.stdout or "").split() else ""
    return result(True, "backup_create", remote_name, status="ready",
                  data={"backup_id": backup_id, "sha256": digest, "free_mb": free_mb})


def backup_list(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    backup_root = f"{paths['sandbox_home']}/runtime/hermes-backups"
    res = _ssh(entry,
               f"if test -d {shlex.quote(backup_root)}; then "
               f"find {shlex.quote(backup_root)} -maxdepth 1 -type f -name '*.tar.gz' "
               "-printf '%f\\t%s\\n' | sort; fi")
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not list Hermes backups", entry), "backup_list_failed", True)
    backups = {}
    for line in (res.stdout or "").splitlines():
        name, _, size = line.partition("\t")
        if not name.endswith(".tar.gz"):
            continue
        backups[name.removesuffix(".tar.gz")] = {"archive": f"{backup_root}/{name}", "size_bytes": int(size or 0)}
    return result(True, "backup_list", remote_name, status="ready",
                  data={"backups": backups})


def cleanup(remote_name: str, confirm: bool, dry_run: bool = False) -> dict:
    """List or remove only clean, completed Hermes worktrees.

    Dirty worktrees are deliberately never candidates; the remote reports them
    for operator follow-up instead of using a force remove.
    """
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    state = _remote_state_read(entry, paths)
    state, stale_jobs = _reconcile_sessions(entry, paths, state)
    active_worktrees = {
        session.get("worktree_path") for session in state["sessions"].values()
        if session.get("state") in {"running", "stale"} and session.get("worktree_path")
    }
    command = (
        f"worktree_root={shlex.quote(paths['worktrees'])}; "
        f"if test -d {shlex.quote(paths['repo_root'])}; then "
        f"for repo in {shlex.quote(paths['repo_root'])}/*; do test -d \"$repo/.git\" || continue; "
        "git -C \"$repo\" worktree list --porcelain | awk '/^worktree / {print $2}' | "
        "while IFS= read -r wt; do case \"$wt\" in \"$worktree_root\"/*|\"$repo\"/.worktrees/*) ;; *) continue;; esac; "
        "if test -n \"$(git -C \"$wt\" status --porcelain)\"; then printf 'dirty\\t%s\\t%s\\n' \"$repo\" \"$wt\"; "
        "else printf 'clean\\t%s\\t%s\\n' \"$repo\" \"$wt\"; fi; done; done; fi"
    )
    res = _ssh(entry, command, timeout=60)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not inspect worktrees", entry), "cleanup_scan_failed", True)
    clean, dirty, active = [], [], []
    for line in (res.stdout or "").splitlines():
        kind, _, remainder = line.partition("\t")
        repo, _, path = remainder.partition("\t")
        if kind == "clean" and path:
            (active if path in active_worktrees else clean).append((repo, path))
        elif kind == "dirty" and path:
            dirty.append(path)
    clean_paths = [path for _, path in clean]
    active_paths = [path for _, path in active]
    if not confirm:
        return result(True, "cleanup", remote_name, status="dry_run",
                      data={"clean_candidates": clean_paths, "dirty_retained": dirty,
                            "active_retained": active_paths, "stale_jobs": stale_jobs,
                            "requires_confirm": bool(clean_paths)})
    if dry_run:
        return result(True, "cleanup", remote_name, status="dry_run",
                      data={"clean_candidates": clean_paths, "dirty_retained": dirty,
                            "active_retained": active_paths, "stale_jobs": stale_jobs,
                            "requires_confirm": False})
    removed = []
    for repo, path in clean:
        # Worktree removal is executed from its own containing repository and
        # never passes --force; Git independently rechecks cleanliness.
        remove = _ssh(entry, f"git -C {shlex.quote(repo)} worktree remove {shlex.quote(path)}")
        if remove.returncode == 0:
            removed.append(path)
    prune = _ssh(entry, (
        f"if test -d {shlex.quote(paths['jobs'])}; then find {shlex.quote(paths['jobs'])} -maxdepth 1 -type f -name '*.status' "
        f"-mtime +{_COMPLETED_JOB_RETENTION_DAYS} -print | while IFS= read -r status; do root=${{status%.status}}; "
        "rm -f \"$status\" \"$root.log\" \"$root.pid\" \"$root.worktree\"; done; fi"), timeout=60)
    if prune.returncode != 0:
        raise HermesError(_redact(prune.stderr or "could not prune completed Hermes job artifacts", entry), "cleanup_retention_failed", True)
    return result(True, "cleanup", remote_name, status="completed",
                  data={"removed": removed, "dirty_retained": dirty, "active_retained": active,
                        "stale_jobs": stale_jobs, "completed_job_retention_days": _COMPLETED_JOB_RETENTION_DAYS})


def clone_repo(remote_name: str, url: str, name: str | None = None, ref: str | None = None) -> dict:
    entry = _require_remote(remote_name)
    safe_url = validate_repo_url(url)
    derived = Path(urlsplit(safe_url).path).name.removesuffix(".git") if not safe_url.startswith("git@") else safe_url.rsplit("/", 1)[-1].removesuffix(".git")
    repo_name = validate_repo_name(name or derived)
    paths = _paths(entry)
    destination = f"{paths['repo_root']}/{repo_name}"
    temp = f"{paths['repo_root']}/.{repo_name}.clone-{secrets.token_hex(4)}"
    ref_arg = f" --branch {shlex.quote(ref)}" if ref else ""
    command = (
        f"set -eu; mkdir -p {shlex.quote(paths['repo_root'])}; "
        f"if test -e {shlex.quote(destination)}; then "
        f"if git -C {shlex.quote(destination)} rev-parse --is-inside-work-tree >/dev/null 2>&1 && "
        f"test \"$(git -C {shlex.quote(destination)} remote get-url origin)\" = {shlex.quote(safe_url)}; "
        "then echo EXISTS_MATCH; exit 0; fi; echo EXISTS; exit 3; fi; "
        f"git clone{ref_arg} -- {shlex.quote(safe_url)} {shlex.quote(temp)}; "
        f"git -C {shlex.quote(temp)} submodule update --init --recursive; "
        f"if git -C {shlex.quote(temp)} lfs version >/dev/null 2>&1; then git -C {shlex.quote(temp)} lfs pull; fi; "
        f"git -C {shlex.quote(temp)} rev-parse --is-inside-work-tree >/dev/null; "
        f"mv {shlex.quote(temp)} {shlex.quote(destination)}"
    )
    res = _ssh(entry, command, timeout=900)
    if res.returncode != 0:
        code = "duplicate_repo" if res.returncode == 3 else "clone_failed"
        raise HermesError(_redact(res.stderr or res.stdout or "clone failed", entry)[:1000], code, code == "clone_failed")
    existing = "EXISTS_MATCH" in (res.stdout or "")
    state = _remote_state_read(entry, paths)
    state["repositories"][repo_name] = {
        "canonical_path": destination, "origin": safe_url, "default_ref": ref,
        "state": "ready", "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _remote_state_write(entry, paths, state)
    return result(True, "repo_clone", remote_name, status="ready", repo=repo_name, path=destination,
                  data={"origin": safe_url, "ref": ref, "existing": existing})


def list_repos(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    command = (
        f"if test -d {shlex.quote(paths['repo_root'])}; then "
        f"for dir in {shlex.quote(paths['repo_root'])}/*; do test -d \"$dir\" || continue; "
        "git -C \"$dir\" rev-parse --is-inside-work-tree >/dev/null 2>&1 && basename \"$dir\"; done; fi"
    )
    res = _ssh(entry, command, timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "repository list failed", entry), "repo_list_failed", True)
    repos = sorted({line.strip() for line in (res.stdout or "").splitlines() if _REPO_NAME_RE.fullmatch(line.strip())})
    return result(True, "repo_list", remote_name, status="ready", data={"repositories": repos})


def repo_sync(remote_name: str, repo_name: str, confirm: bool) -> dict:
    """Fast-forward a clean managed checkout and atomically refresh Sandbox runtime source."""
    if not confirm:
        raise HermesError("repository synchronization requires --confirm", "confirmation_required")
    name = validate_repo_name(repo_name)
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    repo = f"{paths['repo_root']}/{name}"
    runtime = f"{paths['sandbox_home']}/sb-src"
    program = r'''
import json, subprocess, sys
from pathlib import Path
repo=Path(sys.argv[1])
def run(args):
    result=subprocess.run(args,text=True,capture_output=True)
    if result.returncode: raise SystemExit(result.stderr or result.stdout or "git operation failed")
    return result.stdout.strip()
if not (repo / ".git").exists(): raise SystemExit("managed repository is missing")
if run(["git","-C",str(repo),"status","--porcelain"]): raise SystemExit("managed repository is dirty")
branch=run(["git","-C",str(repo),"symbolic-ref","--short","HEAD"])
run(["git","-C",str(repo),"fetch","origin",branch])
run(["git","-C",str(repo),"merge","--ff-only",f"origin/{branch}"])
head=run(["git","-C",str(repo),"rev-parse","HEAD"])
print(json.dumps({"branch":branch,"head":head}))
'''
    res = _checked(entry, f"python3 -c {shlex.quote(program)} {shlex.quote(repo)}", timeout=120,
                   what="managed repository synchronization failed")
    try:
        data = json.loads((res.stdout or "").splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise HermesError("repository synchronization returned invalid evidence", "repo_sync_failed") from exc
    if name == "sandbox":
        refresh = (
            f"set -eu; repo={shlex.quote(repo)}; runtime={shlex.quote(runtime)}; "
            "stage=$(mktemp -d \"${runtime}.new.XXXXXX\"); old=\"${runtime}.old.$$\"; had_runtime=0; "
            "trap 'rm -rf \"$stage\"' EXIT; git -C \"$repo\" archive HEAD | tar -xf - -C \"$stage\"; "
            "for rel in .cli-venv mcp/wp-server/.venv; do if test -e \"$runtime/$rel\"; then "
            "mkdir -p \"$stage/$(dirname \"$rel\")\"; cp -a \"$runtime/$rel\" \"$stage/$rel\"; fi; done; "
            "if test -e \"$runtime\"; then mv \"$runtime\" \"$old\"; had_runtime=1; fi; "
            "rollback() { rm -rf \"$runtime\"; if test \"$had_runtime\" = 1 && test -e \"$old\"; then mv \"$old\" \"$runtime\"; fi; }; "
            "if ! mv \"$stage\" \"$runtime\"; then rollback; exit 1; fi; "
            "if \"$runtime/sb\" --help >/dev/null; then test \"$had_runtime\" = 0 || rm -rf \"$old\"; "
            "else rollback; exit 1; fi"
        )
        _checked(entry, refresh, timeout=180, what="Sandbox runtime refresh failed")
    return result(True, "repo_sync", remote_name, status="synced", repo=name,
                  commit=data.get("head"), data={"branch": data.get("branch"),
                                                 "runtime_refreshed": name == "sandbox"})


def _worktree_setup(paths: dict, repo_name: str) -> str:
    """Create one worktree while holding a repository-scoped advisory lock."""
    repo = f"{paths['repo_root']}/{repo_name}"
    lock = f"{paths['locks']}/{repo_name}.lock"
    worktree_root = f"{paths['worktrees']}/{repo_name}"
    return (
        f"mkdir -p {shlex.quote(paths['locks'])} {shlex.quote(worktree_root)}; exec 9>{shlex.quote(lock)}; flock -w 30 9; "
        f"cd {shlex.quote(repo)}; attempt=0; while :; do "
        f"id=$(python3 -c 'import secrets; print(secrets.token_hex(8))'); branch=hermes/hermes-$id; cwd={shlex.quote(worktree_root)}/$id; "
        "if git worktree add -b \"$branch\" \"$cwd\" HEAD; then break; fi; "
        "attempt=$((attempt + 1)); if test \"$attempt\" -ge 3; then exit 1; fi; done; "
        "flock -u 9; worktree=true"
    )


def _worktree_command(paths: dict, repo_name: str, prompt: str, *, worktree: bool, async_: bool) -> str:
    repo = f"{paths['repo_root']}/{repo_name}"
    prompt_b64 = base64.b64encode(prompt.encode()).decode()
    if not prompt or len(prompt) > 32_000:
        raise HermesError("prompt must be between 1 and 32000 characters", "invalid_prompt")
    setup = f"cwd={shlex.quote(repo)}; worktree=false"
    if worktree:
        setup = _worktree_setup(paths, repo_name)
    action = (
        "prompt=$(echo " + shlex.quote(prompt_b64) + " | base64 -d); "
        f"cd \"$cwd\"; HERMES_HOME={paths['hermes_home']} {paths['launcher']} chat -q \"$prompt\""
    )
    if not async_:
        return f"set -eu; test -d {shlex.quote(repo)}; {setup}; {action}"
    child_action = f"{action}; rc=$?; echo \"$rc\" > {shlex.quote(paths['jobs'])}/\"$job\".status"
    return (
        f"set -eu; test -d {shlex.quote(repo)}; {setup}; mkdir -p {shlex.quote(paths['jobs'])}; "
        "job=$(python3 -c 'import secrets; print(secrets.token_hex(8))'); "
        f"echo \"$cwd\" > {shlex.quote(paths['jobs'])}/\"$job\".worktree; "
        f"export cwd job; setsid sh -c {shlex.quote(child_action)} > {shlex.quote(paths['jobs'])}/\"$job\".log 2>&1 & child=$!; "
        f"echo \"$child\" > {shlex.quote(paths['jobs'])}/\"$job\".pid; "
        "printf '%s\\t%s\\n' \"$job\" \"$cwd\""
    )


def run(remote_name: str, repo: str, prompt: str, *, worktree: bool = True,
        async_: bool = True, timeout: int = 1200) -> dict:
    entry = _require_remote(remote_name)
    repo_name = validate_repo_name(repo)
    if timeout < 1 or timeout > 3600:
        raise HermesError("timeout must be between 1 and 3600 seconds", "invalid_timeout")
    paths = _paths(entry)
    preflight = _resource_preflight(entry, paths)
    command = _worktree_command(paths, repo_name, prompt, worktree=worktree, async_=async_)
    res = _ssh(entry, command, timeout=30 if async_ else timeout)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "Hermes run failed", entry)[:2000], "run_failed", True)
    if async_:
        launch = (res.stdout or "").strip().splitlines()[-1] if (res.stdout or "").strip() else ""
        job_id, _, launched_worktree = launch.partition("\t")
        if not _JOB_RE.fullmatch(job_id):
            raise HermesError("Hermes launch did not return a valid job id", "invalid_job_response")
        expected_prefix = f"{paths['worktrees']}/{repo_name}/"
        if worktree and not launched_worktree.startswith(expected_prefix):
            raise HermesError("Hermes launch returned an invalid worktree path", "invalid_worktree_response")
        state = _remote_state_read(entry, paths)
        state["sessions"][job_id] = {
            "repository": repo_name, "mode": "oneshot", "worktree": worktree,
            "worktree_path": launched_worktree if worktree else None,
            "state": "running", "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _remote_state_write(entry, paths, state)
        return result(True, "run", remote_name, status="queued", repo=repo_name, job_id=job_id,
                      data={"worktree": worktree, "worktree_path": launched_worktree if worktree else None,
                            "preflight": preflight})
    return result(True, "run", remote_name, status="completed", repo=repo_name,
                  data={"worktree": worktree, "output": _redact(res.stdout, entry)[-4000:]})


def _valid_job_id(job_id: str) -> str:
    if not _JOB_RE.fullmatch(job_id or ""):
        raise HermesError("invalid Hermes job id", "invalid_job_id")
    return job_id


def _record_session_completion(entry: dict, paths: dict, job_id: str, exit_code: int | None) -> None:
    """Persist a terminal job state without recording prompt or log content."""
    state = _remote_state_read(entry, paths)
    session = state["sessions"].get(job_id)
    if not session or session.get("state") != "running":
        return
    session["state"] = "completed"
    session["completed_at"] = datetime.now(timezone.utc).isoformat()
    session["exit_code"] = exit_code
    _remote_state_write(entry, paths, state)


def _reconcile_sessions(entry: dict, paths: dict, state: dict) -> tuple[dict, list[str]]:
    """Mark only provably dead sessions stale; retain their worktrees intact."""
    running = [job_id for job_id, session in state["sessions"].items()
               if _JOB_RE.fullmatch(job_id) and session.get("state") == "running"]
    if not running:
        return state, []
    checks = " ".join(shlex.quote(job_id) for job_id in running)
    command = (
        f"for job in {checks}; do root={shlex.quote(paths['jobs'])}/\"$job\"; "
        "if test -f \"$root.status\"; then printf 'completed\\t%s\\n' \"$job\"; "
        "elif test -f \"$root.pid\" && kill -0 \"$(cat \"$root.pid\")\" 2>/dev/null; then printf 'running\\t%s\\n' \"$job\"; "
        "else printf 'stale\\t%s\\n' \"$job\"; fi; done"
    )
    res = _ssh(entry, command, timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not reconcile Hermes sessions", entry), "session_reconcile_failed", True)
    stale, changed = [], False
    for line in (res.stdout or "").splitlines():
        observed, _, job_id = line.partition("\t")
        session = state["sessions"].get(job_id)
        if session is None:
            continue
        if observed == "completed" and session.get("state") == "running":
            session["state"] = "completed"
            session["completed_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
        elif observed == "stale" and session.get("state") == "running":
            session["state"] = "stale"
            session["requires_manual_review"] = True
            session["reconciled_at"] = datetime.now(timezone.utc).isoformat()
            stale.append(job_id)
            changed = True
    if changed:
        _remote_state_write(entry, paths, state)
        if stale:
            _record_v2_evidence(entry, paths, "stale_reconciliation", {"jobs": ",".join(stale)})
    return state, stale


def job_status(remote_name: str, job_id: str, offset: int = 0) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    job_id = _valid_job_id(job_id)
    if offset < 0:
        raise HermesError("job output offset must not be negative", "invalid_offset")
    root = f"{paths['jobs']}/{job_id}"
    command = (
        f"if test ! -f {shlex.quote(root + '.log')} && test ! -f {shlex.quote(root + '.status')}; then echo not_found; exit 0; fi; "
        f"if test -f {shlex.quote(root + '.status')}; then echo completed; cat {shlex.quote(root + '.status')}; "
        "else echo running; fi; "
        f"if test -f {shlex.quote(root + '.log')}; then tail -c +{offset + 1} {shlex.quote(root + '.log')} | head -c 1048576; fi"
    )
    res = _ssh(entry, command, timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not read Hermes job", entry), "job_status_failed", True)
    lines = (res.stdout or "").splitlines()
    state = lines[0] if lines else "not_found"
    if state == "not_found":
        return {"job_id": job_id, "status": "not_found"}
    exit_code = None
    body_index = 1
    if state == "completed" and len(lines) > 1:
        try:
            exit_code = int(lines[1])
        except ValueError:
            pass
        body_index = 2
    if state == "completed":
        _record_session_completion(entry, paths, job_id, exit_code)
    output = "\n".join(lines[body_index:])
    return {"job_id": job_id, "status": state, "exit_code": exit_code,
            "stdout": _redact(output, entry)[-1_048_576:], "truncated": len(output.encode()) > 1_048_576}


def job_kill(remote_name: str, job_id: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    job_id = _valid_job_id(job_id)
    root = f"{paths['jobs']}/{job_id}"
    command = (
        f"if test ! -f {shlex.quote(root + '.pid')}; then echo not_found; exit 0; fi; "
        f"if test -f {shlex.quote(root + '.status')}; then echo completed; exit 0; fi; "
        f"pid=$(cat {shlex.quote(root + '.pid')}); case \"$pid\" in ''|*[!0-9]*) echo invalid; exit 0;; esac; "
        f"kill -- -\"$pid\" 2>/dev/null || true; echo 143 > {shlex.quote(root + '.status')}; echo killed"
    )
    res = _ssh(entry, command, timeout=30)
    state = (res.stdout or "").strip().splitlines()[-1] if (res.stdout or "").strip() else "unknown"
    if state == "not_found":
        return {"job_id": job_id, "status": "not_found"}
    if state == "completed":
        return {"job_id": job_id, "status": "completed", "killed": False}
    if res.returncode != 0 or state != "killed":
        raise HermesError(_redact(res.stderr or "could not cancel Hermes job", entry), "job_kill_failed", True)
    _record_session_completion(entry, paths, job_id, 143)
    return {"job_id": job_id, "status": "completed", "exit_code": 143, "killed": True}


def chat(remote_name: str, repo: str, *, worktree: bool = True) -> dict:
    """Open Hermes through an interactive SSH TTY, optionally in a worktree."""
    entry = _require_remote(remote_name)
    repo_name = validate_repo_name(repo)
    paths = _paths(entry)
    repo_path = f"{paths['repo_root']}/{repo_name}"
    if worktree:
        prepare = f"set -eu; test -d {shlex.quote(repo_path)}; {_worktree_setup(paths, repo_name)}; echo \"$cwd\""
        res = _ssh(entry, prepare, timeout=60)
        if res.returncode != 0 or not (res.stdout or "").strip():
            raise HermesError(_redact(res.stderr or res.stdout or "worktree creation failed", entry), "worktree_failed", True)
        cwd = (res.stdout or "").strip().splitlines()[-1]
    else:
        cwd = repo_path
    parts = remote.remote_ssh_parts(entry)
    command = f"cd {shlex.quote(cwd)} && HERMES_HOME={paths['hermes_home']} {paths['launcher']} chat"
    argv = ["ssh"]
    if parts["port"]:
        argv.extend(["-p", str(parts["port"])])
    argv.extend(["-tt", parts["target"], command])
    try:
        rc = subprocess.run(argv, check=False).returncode
    except OSError as exc:
        raise HermesError(_redact(str(exc), entry), "chat_failed", True) from exc
    if rc != 0:
        raise HermesError("interactive Hermes session exited unsuccessfully", "chat_failed", True)
    return result(True, "chat", remote_name, status="completed", repo=repo_name, path=cwd,
                  data={"worktree": worktree})


def _gateway_unit(paths: dict) -> str:
    """Render a systemd-user unit without relying on shell expansion."""
    return (
        "[Unit]\nDescription=Hermes Sandbox gateway\n[Service]\n"
        f"Environment=HERMES_HOME=%h/.hermes\nWorkingDirectory={paths['repo_root']}\n"
        "ExecStart=%h/.local/bin/hermes gateway run --replace\n"
        "Restart=on-failure\n[Install]\nWantedBy=default.target\n"
    )


def _gateway_install_command(unit: str, body: str) -> str:
    """Install a user unit with rollback if enable or reboot recovery fails."""
    encoded = base64.b64encode(body.encode()).decode()
    return (
        "set -eu; mkdir -p $HOME/.config/systemd/user; "
        f"target=\"$HOME/.config/systemd/user/{unit}\"; tmp=\"$target.tmp.$$\"; backup=\"$target.backup.$$\"; had=0; was_enabled=0; "
        "if test -f \"$target\"; then cp \"$target\" \"$backup\"; had=1; fi; "
        f"if systemctl --user is-enabled {shlex.quote(unit)} >/dev/null 2>&1; then was_enabled=1; fi; "
        f"echo {shlex.quote(encoded)} | base64 -d > \"$tmp\"; chmod 600 \"$tmp\"; mv \"$tmp\" \"$target\"; "
        "rollback() { if test \"$had\" = 1; then mv \"$backup\" \"$target\"; else rm -f \"$target\"; fi; "
        f"if test \"$was_enabled\" = 0; then systemctl --user disable {shlex.quote(unit)} >/dev/null 2>&1 || true; fi; "
        "systemctl --user daemon-reload >/dev/null 2>&1 || true; }; "
        f"if systemctl --user daemon-reload && systemctl --user enable {shlex.quote(unit)} && loginctl enable-linger \"$USER\"; "
        "then rm -f \"$backup\"; else rc=$?; rollback; exit \"$rc\"; fi"
    )


def _gateway_ownership(entry: dict) -> dict:
    program = r'''
import json, os, subprocess
from pathlib import Path
processes=[]
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit(): continue
    try: args=proc.joinpath("cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError: continue
    if any(arg.endswith("hermes") or "/hermes" in arg for arg in args) and "gateway" in args and "run" in args:
        processes.append(int(proc.name))
units={}
for unit in ("hermes-gateway-sandbox.service","hermes-gateway.service"):
    result=subprocess.run(["systemctl","--user","show",unit,"-p","ActiveState","-p","UnitFileState","-p","NRestarts"],text=True,capture_output=True)
    values={}
    for line in result.stdout.splitlines():
        key,sep,value=line.partition("=")
        if sep: values[key]=value
    units[unit]={"active_state":values.get("ActiveState","unknown"),
                 "unit_file_state":values.get("UnitFileState","unknown"),
                 "restart_count":int(values.get("NRestarts","0") or 0)}
print(json.dumps({"gateway_pids": sorted(processes),"units":units},sort_keys=True))
'''
    res = _ssh(entry, f"python3 -c {shlex.quote(program)}", timeout=30)
    try:
        process_data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        process_data = {}
    units = process_data.get("units") if isinstance(process_data, dict) else {}
    units = units if isinstance(units, dict) else {}
    pids = process_data.get("gateway_pids") if isinstance(process_data, dict) else []
    pids = pids if isinstance(pids, list) else []
    expected_active = units.get(GATEWAY_UNIT, {}).get("active_state") == "active"
    legacy_state = units.get("hermes-gateway.service", {}).get("active_state")
    # Transitional states are not quiescent: a restart policy can move a
    # deactivating legacy unit straight back to activating.
    legacy_quiescent = legacy_state in {"inactive", "failed"}
    conflict = not legacy_quiescent or len(pids) != 1 or not expected_active
    return {"expected_unit": GATEWAY_UNIT, "units": units, "gateway_process_count": len(pids),
            "conflict": conflict, "healthy": not conflict}


def _gateway_stability(entry: dict, *, paths: dict, stability_seconds: int,
                       sample_interval: int) -> dict:
    """Collect bounded gateway and scheduler samples through one SSH session."""
    if not isinstance(stability_seconds, int) or stability_seconds < 0:
        raise HermesError("gateway stability seconds must be a non-negative integer", "invalid_stability_window")
    if not isinstance(sample_interval, int) or sample_interval < 1:
        raise HermesError("gateway sample interval must be a positive integer", "invalid_stability_interval")

    expected_samples = 1 + ((stability_seconds + sample_interval - 1) // sample_interval)
    if expected_samples > GATEWAY_STABILITY_MAX_SAMPLES:
        raise HermesError("gateway stability window exceeds the bounded sample limit", "invalid_stability_window")
    program = f'''
import json, os, subprocess, time
from pathlib import Path

MANAGED = {GATEWAY_UNIT!r}
LEGACY = "hermes-gateway.service"
LAUNCHER = os.path.expandvars({paths["launcher"]!r})
ACTIVE_STATES = {{"active", "activating", "deactivating", "failed", "inactive", "reloading"}}
UNIT_FILE_STATES = {{"alias", "bad", "disabled", "enabled", "generated", "indirect", "linked", "linked-runtime", "masked", "static", "transient"}}

def state(value, allowed):
    return value if value in allowed else "unknown"

def count(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return -1
    return value if 0 <= value <= 1000000 else -1

def unit(unit):
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "UnitFileState", "-p", "NRestarts"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2,
        )
        values = {{}}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value
    except (OSError, subprocess.TimeoutExpired):
        values = {{}}
    return {{"active_state": state(values.get("ActiveState", "unknown"), ACTIVE_STATES),
            "unit_file_state": state(values.get("UnitFileState", "unknown"), UNIT_FILE_STATES),
            "restart_count": count(values.get("NRestarts", 1000000))}}

def gateway_process_count():
    processes = 0
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                args = proc.joinpath("cmdline").read_bytes().decode(errors="replace").split("\\0")
            except OSError:
                continue
            if any(arg.endswith("hermes") or "/hermes" in arg for arg in args) and "gateway" in args and "run" in args:
                processes += 1
    except OSError:
        return -1
    return min(processes, 1000000)

def sample():
    try:
        scheduler_ok = subprocess.run([LAUNCHER, "cron", "status"], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, timeout=2).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        scheduler_ok = False
    return {{"managed": unit(MANAGED), "legacy": unit(LEGACY),
            "gateway_process_count": gateway_process_count(), "scheduler_ok": scheduler_ok}}

samples = [sample()]
elapsed = 0
while elapsed < {stability_seconds}:
    delay = min({sample_interval}, {stability_seconds} - elapsed)
    time.sleep(delay)
    elapsed += delay
    samples.append(sample())
print(json.dumps({{"samples": samples}}, sort_keys=True, separators=(",", ":")))
'''

    def unavailable(*, malformed: bool = False) -> dict:
        return {"stable": False, "observation_seconds": stability_seconds, "sample_count": 0,
                "restart_counts": [], "ownership_present": False, "scheduler": {"available": False},
                "scheduler_present": False, "malformed_evidence": malformed}

    try:
        response = _ssh(entry, f"python3 -c {shlex.quote(program)}",
                        timeout=stability_seconds + GATEWAY_STABILITY_TIMEOUT_MARGIN)
    except HermesError:
        return unavailable()
    if response.returncode != 0 or not isinstance(response.stdout, str) or len(response.stdout) > 16384:
        return unavailable(malformed=True)
    try:
        evidence = json.loads(response.stdout)
    except json.JSONDecodeError:
        return unavailable(malformed=True)
    samples = evidence.get("samples") if isinstance(evidence, dict) and set(evidence) == {"samples"} else None
    if not isinstance(samples, list) or len(samples) != expected_samples:
        return unavailable(malformed=True)

    restart_counts = []
    ownership = []
    scheduler = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"managed", "legacy", "gateway_process_count", "scheduler_ok"}:
            return unavailable(malformed=True)
        managed, legacy = sample["managed"], sample["legacy"]
        if (not isinstance(managed, dict) or not isinstance(legacy, dict)
                or set(managed) != {"active_state", "unit_file_state", "restart_count"}
                or set(legacy) != {"active_state", "unit_file_state", "restart_count"}):
            return unavailable(malformed=True)
        restart_count = managed["restart_count"]
        if (type(restart_count) is not int or not 0 <= restart_count <= 1000000
                or type(legacy["restart_count"]) is not int or not 0 <= legacy["restart_count"] <= 1000000
                or type(sample["gateway_process_count"]) is not int or not 0 <= sample["gateway_process_count"] <= 1000000
                or type(sample["scheduler_ok"]) is not bool
                or managed["active_state"] not in {"active", "activating", "deactivating", "failed", "inactive", "reloading", "unknown"}
                or legacy["active_state"] not in {"active", "activating", "deactivating", "failed", "inactive", "reloading", "unknown"}
                or managed["unit_file_state"] not in {"alias", "bad", "disabled", "enabled", "generated", "indirect", "linked", "linked-runtime", "masked", "static", "transient", "unknown"}
                or legacy["unit_file_state"] not in {"alias", "bad", "disabled", "enabled", "generated", "indirect", "linked", "linked-runtime", "masked", "static", "transient", "unknown"}):
            return unavailable(malformed=True)
        restart_counts.append(restart_count)
        ownership.append(managed["active_state"] == "active"
                         and legacy["active_state"] in {"inactive", "failed"}
                         and sample["gateway_process_count"] == 1)
        scheduler.append(sample["scheduler_ok"])

    ownership_present = all(ownership)
    scheduler_present = all(scheduler)
    stable = (ownership_present and scheduler_present
              and all(count == restart_counts[0] for count in restart_counts[1:]))
    return {"stable": stable, "observation_seconds": stability_seconds,
            "sample_count": len(samples), "restart_counts": restart_counts,
            "ownership_present": ownership_present, "scheduler": {"available": scheduler[-1]},
            "scheduler_present": scheduler_present, "malformed_evidence": False}


def gateway_converge(remote_name: str, confirm: bool = False, *,
                     stability_seconds: int = GATEWAY_STABILITY_SECONDS,
                     sample_interval: int = GATEWAY_STABILITY_INTERVAL,
                     sleeper=time.sleep) -> dict:
    """Preview or establish the Sandbox user service as the sole gateway owner."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    before = _gateway_ownership(entry)
    actions = []
    legacy = before["units"].get("hermes-gateway.service", {})
    if legacy.get("active_state") not in {"inactive", "failed"}:
        actions.append("stop legacy hermes-gateway.service")
    if legacy.get("unit_file_state") == "enabled":
        actions.append("disable legacy hermes-gateway.service")
    if before["gateway_process_count"] != (1 if before["healthy"] else 0):
        actions.append("stop unmanaged gateway processes")
    if not before["healthy"]:
        actions += ["install hermes-gateway-sandbox.service", "start managed gateway", "verify stable ownership"]
    if not confirm:
        return result(True, "gateway_converge", remote_name,
                      status="converged" if before["healthy"] else "planned",
                      data={"before": before, "actions": actions, "requires_confirm": bool(actions)})
    after = before
    if not before["healthy"]:
        killer = r'''
import os, signal
from pathlib import Path
me={os.getpid(), os.getppid()}
for proc in Path("/proc").iterdir():
    if not proc.name.isdigit() or int(proc.name) in me: continue
    try: args=proc.joinpath("cmdline").read_bytes().decode(errors="replace").split("\0")
    except OSError: continue
    if any(arg.endswith("hermes") or "/hermes" in arg for arg in args) and "gateway" in args and "run" in args:
        try: os.kill(int(proc.name), signal.SIGTERM)
        except (ProcessLookupError, PermissionError): pass
'''
        command = (
            "set -eu; systemctl --user stop hermes-gateway-sandbox.service 2>/dev/null || true; "
            "systemctl --user stop hermes-gateway.service 2>/dev/null || true; "
            "systemctl --user disable hermes-gateway.service >/dev/null 2>&1 || true; "
            f"python3 -c {shlex.quote(killer)}; sleep 2; "
            + _gateway_install_command(GATEWAY_UNIT, _gateway_unit(paths)) + "; "
            f"systemctl --user restart {GATEWAY_UNIT}; sleep 5"
        )
        _checked(entry, command, timeout=60, what="Hermes gateway convergence failed")
        after = _gateway_ownership(entry)
    if not after["healthy"]:
        return result(False, "gateway_converge", remote_name, status="degraded",
                      data={"before": before, "after": after, "actions": actions},
                      error=HermesError("gateway ownership did not converge", "gateway_conflict", True))
    stability = _gateway_stability(entry, paths=paths, stability_seconds=stability_seconds,
                                   sample_interval=sample_interval)
    if not stability["stable"]:
        return result(False, "gateway_converge", remote_name, status="degraded",
                      data={"before": before, "after": after, "actions": actions, "stability": stability},
                      error=HermesError("gateway scheduler did not remain stable", "gateway_stability_failed", True))
    return result(True, "gateway_converge", remote_name, status="converged",
                  data={"before": before, "after": after, "actions": actions, "stability": stability})


def gateway(remote_name: str, action: str, allowlist: list[str] | None = None, lines: int = 200) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    unit = GATEWAY_UNIT
    allowlist_path = "$HOME/.hermes/sandbox-gateway-allowlist.json"
    if action == "setup":
        policy = validate_gateway_allowlist(allowlist)
        payload = base64.b64encode(json.dumps({"allowlist": policy}).encode()).decode()
        _checked(entry,
                 f"mkdir -p \"$HOME/.hermes\"; echo {shlex.quote(payload)} | base64 -d > {allowlist_path}; chmod 600 {allowlist_path}",
                 what="could not store gateway allowlist")
        return result(True, "gateway_setup", remote_name, status="configured", data={"allowlist_entries": len(policy)})
    if action in {"install", "start", "restart"}:
        if allowlist:
            policy = validate_gateway_allowlist(allowlist)
        else:
            res = _ssh(entry, f"if test -f {allowlist_path}; then cat {allowlist_path}; fi")
            try:
                policy = validate_gateway_allowlist(json.loads(res.stdout or "{}").get("allowlist"))
            except (ValueError, json.JSONDecodeError, HermesError) as exc:
                raise HermesError("gateway setup requires a non-empty explicit allowlist", "unsafe_gateway_allowlist") from exc
    if action == "install":
        body = _gateway_unit(paths)
        command = _gateway_install_command(unit, body)
    elif action == "status":
        res = _ssh(entry, f"systemctl --user is-active {unit} 2>/dev/null || true", timeout=30)
        observed = (res.stdout or "").strip().splitlines()[-1:] or ["unknown"]
        return result(True, "gateway_status", remote_name, status=observed[0], data={"service": unit})
    elif action in {"start", "stop", "restart"}:
        command = f"systemctl --user {action} {unit}"
    elif action == "logs":
        if lines < 1 or lines > 1000:
            raise HermesError("log lines must be between 1 and 1000", "invalid_log_limit")
        command = f"journalctl --user -u {unit} -n {lines} --no-pager"
    else:
        raise HermesError("unknown gateway action", "invalid_gateway_action")
    res = _ssh(entry, command, timeout=60)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "gateway action failed", entry)[:2000], "gateway_failed", True)
    output = _redact(res.stdout, entry) if action == "logs" else ""
    return result(True, f"gateway_{action}", remote_name, status="active" if action in {"start", "restart"} else "configured",
                  data={"output": output[-4000:], "truncated": len(output) > 4000} if action == "logs" else {})


def validate_dashboard_port(port: int | str | None) -> int:
    try:
        value = int(port or DASHBOARD_PORT)
    except (TypeError, ValueError) as exc:
        raise HermesError("dashboard port must be an integer", "invalid_dashboard_port") from exc
    if value < 1024 or value > 65535:
        raise HermesError("dashboard port must be between 1024 and 65535", "invalid_dashboard_port")
    return value


def validate_dashboard_fqdn(fqdn: str) -> str:
    value = (fqdn or "").strip().lower().rstrip(".")
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
        raise HermesError("dashboard FQDN must be a normalized hostname", "invalid_dashboard_fqdn")
    return value


def _public_config() -> dict:
    """Return non-secret attach-only references from the personal local config."""
    local = _local_yaml()
    value = ((local.get("hermes") or {}).get("public_access") or {})
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item).strip() for key, item in value.items() if item not in (None, "")}


def _secret_reference(value: str | None, field: str) -> str:
    name = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise HermesError(f"public exposure requires a valid {field} secret reference", "public_exposure_secret_missing")
    return name


def _public_config_errors(config: dict) -> list[str]:
    required = ("account_id", "access_application_id", "access_policy_id", "tunnel_id", "zone_id",
                "dns_record_id", "access_token_secret", "tunnel_api_token_secret", "zone_token_secret",
                "connector_token_secret")
    return [key for key in required if not config.get(key)]


def _public_caddy_fragment(fqdn: str, basic_enabled: bool) -> str:
    auth = ""
    if basic_enabled:
        auth = f"    basic_auth argon2id {{\n        import {PUBLIC_BASIC_FRAGMENT}\n    }}\n"
    return (
        f"http://:{PUBLIC_PROXY_PORT} {{\n"
        "    bind 127.0.0.1\n"
        f"    @dashboard host {fqdn}\n"
        "    handle @dashboard {\n"
        f"{auth}"
        "        reverse_proxy 127.0.0.1:9119 {\n"
        "            header_up Host {upstream_hostport}\n"
        "            header_up Origin http://127.0.0.1:9119\n"
        "            header_up X-Forwarded-Proto https\n"
        "        }\n"
        "    }\n"
        "    handle {\n"
        "        respond 404\n"
        "    }\n"
        "}\n"
    )


def _public_remote_write(entry: dict, path: str, text: str, mode: str = "0600") -> None:
    encoded = base64.b64encode(text.encode()).decode()
    command = (
        f"tmp=/tmp/hermes-public.$$.tmp; echo {shlex.quote(encoded)} | base64 -d > \"$tmp\"; "
        "if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; "
        f"$SUDO install -D -m {shlex.quote(mode)} \"$tmp\" {shlex.quote(path)}; rm -f \"$tmp\""
    )
    _checked(entry, command, what="could not write public exposure configuration")


def _public_caddy_apply(entry: dict, fragment: str) -> None:
    _public_remote_write(entry, PUBLIC_CADDY_FRAGMENT, fragment, "0644")
    _checked(entry, "if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; $SUDO caddy validate --config /etc/caddy/Caddyfile && $SUDO systemctl reload caddy",
             what="could not validate public dashboard Caddy route")


def _public_caddy_remove(entry: dict) -> None:
    _checked(entry, f"if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; $SUDO rm -f {shlex.quote(PUBLIC_CADDY_FRAGMENT)} {shlex.quote(PUBLIC_BASIC_FRAGMENT)}; $SUDO caddy validate --config /etc/caddy/Caddyfile && $SUDO systemctl reload caddy",
             what="could not remove public dashboard Caddy route")


def _public_validate_cloudflare(config: dict, fqdn: str) -> dict:
    missing = _public_config_errors(config)
    if missing:
        return {"configured": False, "missing": missing}
    try:
        access_token = resolve_secret(_secret_reference(config.get("access_token_secret"), "Access API"))
        tunnel_token = resolve_secret(_secret_reference(config.get("tunnel_api_token_secret"), "Tunnel API"))
        zone_token = resolve_secret(_secret_reference(config.get("zone_token_secret"), "zone DNS"))
        if not access_token or not tunnel_token or not zone_token:
            return {"configured": False, "missing": ["configured secret value"]}
        access = cloudflare_access.Client(access_token)
        app = cloudflare_access.validate_application(access.application(config["account_id"], config["access_application_id"]), fqdn)
        policy = cloudflare_access.validate_policy(access.policy(config["account_id"], config["access_policy_id"]))
        tunnel = cloudflare_tunnel.Client(tunnel_token)
        target = f"http://127.0.0.1:{PUBLIC_PROXY_PORT}"
        route = cloudflare_tunnel.validate_configuration(tunnel.configuration(config["account_id"], config["tunnel_id"]), fqdn, target)
        dns = next((record for record in cloudflare_zone.Client(zone_token).records(config["zone_id"], fqdn)
                    if str(record.get("id") or "") == config["dns_record_id"]), None)
        if not dns or dns.get("name") != fqdn or dns.get("proxied") is not True:
            raise HermesError("Cloudflare DNS reference must be the exact proxied dashboard hostname", "public_exposure_conflict")
        return {"configured": True, "access": app, "policy": policy,
                "tunnel": {"id": config["tunnel_id"], **route},
                "dns": {"id": config["dns_record_id"], "name": fqdn, "proxied": True}}
    except (cloudflare_access.AccessError, cloudflare_tunnel.TunnelError, cloudflare_zone.CloudflareError, HermesError) as exc:
        return {"configured": True, "valid": False, "error": _redact(str(exc))}


def _public_plan(entry: dict, paths: dict, state: dict, fqdn: str) -> dict:
    if fqdn != PUBLIC_DASHBOARD_FQDN:
        raise HermesError("only hermes.asb.bd is supported for public dashboard access", "invalid_dashboard_fqdn")
    config = _public_config()
    cloudflare = _public_validate_cloudflare(config, fqdn)
    dashboard = _dashboard_status(entry, paths, DASHBOARD_PORT)
    listeners = _dashboard_listeners(entry, DASHBOARD_PORT)
    exposure = state.get("public_exposure") or {}
    basic = exposure.get("basic_auth") or {}
    ready = bool(dashboard["active"] and listeners["expected_loopback"] and not listeners["public_listener"] and cloudflare.get("configured") and cloudflare.get("valid", True))
    return {"fqdn": fqdn, "mode": exposure.get("mode", "ssh-only"), "ready": ready,
            "dashboard": {"active": dashboard["active"], "loopback_only": listeners["expected_loopback"] and not listeners["public_listener"]},
            "cloudflare": cloudflare, "proxy": {"host": DASHBOARD_LOOPBACK_HOST, "port": PUBLIC_PROXY_PORT,
            "target": f"127.0.0.1:{DASHBOARD_PORT}", "basic_auth_enabled": bool(basic.get("enabled"))},
            "rollback": {"caddy_fragment": PUBLIC_CADDY_FRAGMENT, "connector_unit": PUBLIC_TUNNEL_UNIT},
            "attach_only": True}


def _public_require_ready(plan: dict, config: dict) -> str:
    if not plan.get("ready"):
        raise HermesError("public exposure prerequisites are not healthy", "public_exposure_failed")
    name = _secret_reference(config.get("connector_token_secret"), "connector")
    token = resolve_secret(name)
    if not token:
        raise HermesError("public exposure connector secret is missing", "public_exposure_secret_missing")
    return token


def _public_install_connector(entry: dict, token: str) -> None:
    command = f"mkdir -p $HOME/.hermes; umask 077; cat > {PUBLIC_TUNNEL_TOKEN_FILE}; chmod 600 {PUBLIC_TUNNEL_TOKEN_FILE}"
    res = _ssh_stdin(entry, command, token.encode(), timeout=60)
    if res.returncode != 0:
        raise HermesError("could not store Cloudflare Tunnel connector token", "public_exposure_failed", True)
    _checked(entry, "test -x /usr/bin/cloudflared && /usr/bin/cloudflared tunnel run --help | grep -q -- --token-file",
             what="cloudflared 2025.4.0 or newer with token-file support is required")
    unit = cloudflare_tunnel.service_unit(PUBLIC_TUNNEL_UNIT, PUBLIC_TUNNEL_TOKEN_UNIT_FILE)
    encoded = base64.b64encode(unit.encode()).decode()
    _checked(entry, (
        "mkdir -p $HOME/.config/systemd/user; "
        f"echo {shlex.quote(encoded)} | base64 -d > $HOME/.config/systemd/user/{PUBLIC_TUNNEL_UNIT}; "
        f"chmod 600 $HOME/.config/systemd/user/{PUBLIC_TUNNEL_UNIT}; systemctl --user daemon-reload; "
        f"systemctl --user enable --now {PUBLIC_TUNNEL_UNIT}"
    ), what="could not start Cloudflare Tunnel connector")


def _public_stop_connector(entry: dict) -> None:
    _ssh(entry, f"systemctl --user disable --now {PUBLIC_TUNNEL_UNIT} >/dev/null 2>&1 || true; rm -f $HOME/.config/systemd/user/{PUBLIC_TUNNEL_UNIT} $HOME/.hermes/cloudflared-token; systemctl --user daemon-reload", timeout=60)


def _dashboard_unit(port: int) -> str:
    """Render the upstream dashboard as a loopback-only user service."""
    return (
        "[Unit]\nDescription=Hermes Sandbox dashboard\nAfter=network-online.target\n"
        "[Service]\n"
        "Environment=HERMES_HOME=%h/.hermes\n"
        f"ExecStart=%h/.local/bin/hermes dashboard --host {DASHBOARD_LOOPBACK_HOST} --port {port} --no-open --tui\n"
        "TimeoutStartSec=180\n"
        "Restart=on-failure\nRestartSec=5\n"
        "NoNewPrivileges=true\nPrivateTmp=true\n"
        "[Install]\nWantedBy=default.target\n"
    )


def _dashboard_install_command(unit: str, body: str) -> str:
    encoded = base64.b64encode(body.encode()).decode()
    return (
        "set -eu; mkdir -p $HOME/.config/systemd/user; "
        "if command -v loginctl >/dev/null 2>&1; then loginctl enable-linger \"$USER\"; fi; "
        f"target=\"$HOME/.config/systemd/user/{unit}\"; tmp=\"$target.tmp.$$\"; backup=\"$target.backup.$$\"; had=0; "
        "if test -f \"$target\"; then cp \"$target\" \"$backup\"; had=1; fi; "
        f"echo {shlex.quote(encoded)} | base64 -d > \"$tmp\"; chmod 600 \"$tmp\"; mv \"$tmp\" \"$target\"; "
        "rollback() { if test \"$had\" = 1; then mv \"$backup\" \"$target\"; else rm -f \"$target\"; fi; systemctl --user daemon-reload >/dev/null 2>&1 || true; }; "
        f"if systemctl --user daemon-reload && systemctl --user enable {shlex.quote(unit)}; then rm -f \"$backup\"; else rc=$?; rollback; exit \"$rc\"; fi"
    )


def _dashboard_gate(entry: dict, paths: dict) -> dict:
    gate = _v2_gate(_remote_state_read(entry, paths))
    if gate["status"] != "passed":
        missing = ", ".join(gate["missing_checks"]) or "a current acceptance record"
        raise HermesError(f"dashboard is blocked until V2 acceptance passes ({missing})", "v2_gate_required")
    return gate


def _dashboard_status(entry: dict, paths: dict, port: int) -> dict:
    res = _ssh(entry, (
        f"active=$(systemctl --user is-active {DASHBOARD_UNIT} 2>/dev/null || true); "
        f"enabled=$(systemctl --user is-enabled {DASHBOARD_UNIT} 2>/dev/null || true); "
        f"pid=$(systemctl --user show {DASHBOARD_UNIT} -p MainPID --value 2>/dev/null || true); "
        f"printf 'active=%s\\nenabled=%s\\npid=%s\\nport=%s\\n' \"$active\" \"$enabled\" \"$pid\" {port}"
    ), timeout=30)
    values = dict(line.split("=", 1) for line in (res.stdout or "").splitlines() if "=" in line)
    active = values.get("active") == "active"
    return {"installed": values.get("enabled") in {"enabled", "static"} or active,
            "enabled": values.get("enabled") in {"enabled", "static"},
            "active": active, "substate": values.get("active", "unknown"),
            "pid": int(values.get("pid", "0") or 0), "port": port,
            "host": DASHBOARD_LOOPBACK_HOST, "last_health": "healthy" if active else "unknown"}


def _dashboard_listeners(entry: dict, port: int) -> dict:
    """Inspect only TCP listeners for the selected port without exposing them."""
    res = _ssh(entry, "command -v ss >/dev/null 2>&1 && ss -ltnH", timeout=30)
    if res.returncode != 0:
        raise HermesError("dashboard listener probe is unavailable", "dashboard_listener_probe_failed", True)
    suffix = f":{port}"
    listeners = []
    for line in (res.stdout or "").splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[3].endswith(suffix):
            listeners.append(fields[3])
    loopback = {f"127.0.0.1:{port}", f"[::1]:{port}"}
    return {
        "listeners": listeners,
        "expected_loopback": any(item in loopback for item in listeners),
        "public_listener": any(item not in loopback for item in listeners),
    }


def _dashboard_port_preflight(entry: dict, port: int) -> None:
    if _dashboard_listeners(entry, port)["listeners"]:
        raise HermesError("dashboard port is already in use", "dashboard_port_in_use")


def _dashboard_lifecycle_command(action: str, port: int) -> str:
    """Wait for the expected private listener and stop a failed launch."""
    unit = shlex.quote(DASHBOARD_UNIT)
    if action == "stop":
        return f"systemctl --user stop {unit}"
    return (
        f"set -eu; systemctl --user {action} {unit}; "
        "for attempt in $(seq 1 30); do "
        f"if systemctl --user is-active --quiet {unit} && command -v ss >/dev/null 2>&1 && "
        f"ss -ltnH | awk -v port={port} '$4 ~ (\":\" port \"$\") {{ "
        "if ($4 == \"127.0.0.1:\" port || $4 == \"[::1]:\" port) local_listener=1; else public_listener=1 } "
        "END { exit (local_listener && !public_listener) ? 0 : 1 }'; then exit 0; fi; "
        "sleep 2; done; "
        f"systemctl --user stop {unit} >/dev/null 2>&1 || true; exit 1"
    )


def _dashboard_forward(remote_name: str, port: int) -> str:
    """Return a safe operator instruction without exposing SSH target details."""
    return f"ssh -N -L {port}:127.0.0.1:{port} <configured-{remote_name}-ssh-target>"


def dashboard_action(remote_name: str, action: str, *, port: int | str | None = None,
                     fqdn: str | None = None, confirm: bool = False,
                     plan: bool = False, lines: int = 200, target: str | None = None,
                     basic_auth_user: str | None = None, basic_auth_secret: str | None = None) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    selected_port = validate_dashboard_port(port)
    gate = _dashboard_gate(entry, paths)
    state = _remote_state_read(entry, paths)
    dashboard = state.setdefault("dashboard", {})
    if action == "exposure-status":
        exposure = state.get("public_exposure") or {}
        plan_data = _public_plan(entry, paths, state, exposure.get("fqdn", PUBLIC_DASHBOARD_FQDN))
        return result(True, "dashboard_exposure_status", remote_name, status=exposure.get("mode", "ssh-only"),
                      commit=gate["commit"], data=plan_data)
    if action == "install":
        command = (
            "set -eu; cd \"$HOME/.hermes/hermes-agent\"; "
            "if ! test -x .venv/bin/pip; then python3 -m venv .venv; fi; "
            ".venv/bin/pip install --disable-pip-version-check -e '.[web,pty]'"
        )
        _checked(entry, command, timeout=1800, what="Hermes dashboard dependency installation failed")
        dashboard.update({"installed": True, "port": selected_port, "host": DASHBOARD_LOOPBACK_HOST,
                          "unit": DASHBOARD_UNIT, "auth_mode": "upstream"})
        state["dashboard"] = dashboard
        _remote_state_write(entry, paths, state)
        return result(True, "dashboard_install", remote_name, status="installed", commit=gate["commit"],
                      data={"extras": ["web", "pty"], "host": DASHBOARD_LOOPBACK_HOST, "port": selected_port})
    if action == "setup":
        if not dashboard.get("installed"):
            raise HermesError("dashboard dependencies are not installed", "dashboard_not_installed")
        dashboard.update({"port": selected_port, "host": DASHBOARD_LOOPBACK_HOST, "unit": DASHBOARD_UNIT,
                          "auth_mode": "upstream"})
        body = _dashboard_unit(selected_port)
        _checked(entry, _dashboard_install_command(DASHBOARD_UNIT, body), timeout=90,
                 what="could not install dashboard service")
        state["dashboard"] = dashboard
        _remote_state_write(entry, paths, state)
        return result(True, "dashboard_setup", remote_name, status="configured", commit=gate["commit"],
                      data={"unit": DASHBOARD_UNIT, "host": DASHBOARD_LOOPBACK_HOST, "port": selected_port,
                            "auth_mode": "upstream", "ssh_forward": _dashboard_forward(remote_name, selected_port)})
    if action in {"start", "stop", "restart"}:
        if not dashboard.get("installed"):
            raise HermesError("dashboard dependencies are not installed", "dashboard_not_installed")
        before = _dashboard_status(entry, paths, selected_port)
        if action in {"start", "restart"} and not before["active"]:
            _dashboard_port_preflight(entry, selected_port)
        command = _dashboard_lifecycle_command(action, selected_port)
        _checked(entry, command, timeout=90, what=f"dashboard {action} failed")
        after = _dashboard_status(entry, paths, selected_port)
        if action != "stop":
            listeners = _dashboard_listeners(entry, selected_port)
            if not after["active"] or not listeners["expected_loopback"] or listeners["public_listener"]:
                _ssh(entry, f"systemctl --user stop {shlex.quote(DASHBOARD_UNIT)} >/dev/null 2>&1 || true", timeout=30)
                raise HermesError("dashboard did not reach a healthy loopback listener", "dashboard_start_failed", True)
        return result(True, f"dashboard_{action}", remote_name, status="active" if action != "stop" else "inactive",
                      commit=gate["commit"], data={**after,
                                                  "ssh_forward": _dashboard_forward(remote_name, selected_port)})
    if action == "status":
        return result(True, "dashboard_status", remote_name, status="ready", commit=gate["commit"],
                      data={**_dashboard_status(entry, paths, selected_port),
                            "ssh_forward": _dashboard_forward(remote_name, selected_port)})
    if action == "doctor":
        status_data = _dashboard_status(entry, paths, selected_port)
        listeners = _dashboard_listeners(entry, selected_port)
        status_data["loopback_only"] = bool(status_data["active"] and listeners["expected_loopback"] and not listeners["public_listener"])
        status_data["auth_mode"] = dashboard.get("auth_mode", "upstream")
        status_data["ssh_forward"] = _dashboard_forward(remote_name, selected_port)
        ok = status_data["active"] and status_data["loopback_only"] and status_data["auth_mode"] == "upstream"
        return result(ok, "dashboard_doctor", remote_name, status="healthy" if ok else "degraded",
                      commit=gate["commit"], data=status_data,
                      error=None if ok else HermesError("dashboard loopback/authentication checks failed", "dashboard_health_failed"))
    if action == "logs":
        lines = int(lines or 200)
        if lines < 1 or lines > 1000:
            raise HermesError("log lines must be between 1 and 1000", "invalid_log_limit")
        logs = _checked(entry, f"journalctl --user -u {DASHBOARD_UNIT} -n {lines} --no-pager", timeout=60,
                        what="could not read dashboard logs")
        return result(True, "dashboard_logs", remote_name, status="ready", commit=gate["commit"],
                      data={"output": _redact(logs.stdout, entry)[-4000:]})
    if action == "expose":
        host = validate_dashboard_fqdn(fqdn or "")
        plan_data = _public_plan(entry, paths, state, host)
        if plan or not confirm:
            return result(True, "dashboard_expose", remote_name, status="plan", commit=gate["commit"], data={"plan": plan_data})
        config = _public_config()
        connector_token = _public_require_ready(plan_data, config)
        previous = _ssh(entry, f"if test -f {PUBLIC_CADDY_FRAGMENT}; then cat {PUBLIC_CADDY_FRAGMENT}; fi", timeout=30)
        basic = (state.get("public_exposure") or {}).get("basic_auth") or {}
        try:
            _public_caddy_apply(entry, _public_caddy_fragment(host, bool(basic.get("enabled"))))
            _public_install_connector(entry, connector_token)
            probe = _ssh(entry, f"curl -fsS -H 'Host: {host}' http://127.0.0.1:{PUBLIC_PROXY_PORT}/ >/dev/null", timeout=30)
            if probe.returncode != 0:
                raise HermesError("loopback public proxy health check failed", "public_exposure_failed", True)
        except HermesError:
            _public_stop_connector(entry)
            if previous.stdout:
                _public_caddy_apply(entry, previous.stdout)
            else:
                _public_caddy_remove(entry)
            raise
        state["public_exposure"] = {"fqdn": host, "mode": "public", "proxy_port": PUBLIC_PROXY_PORT,
                                    "basic_auth": basic, "attach_only": True,
                                    "rollback": {"caddy_fragment_present": bool(previous.stdout)}}
        _remote_state_write(entry, paths, state)
        return result(True, "dashboard_expose", remote_name, status="public", commit=gate["commit"], data=plan_data)
    if action == "unexpose":
        exposure = state.get("public_exposure") or {}
        if plan or not confirm:
            return result(True, "dashboard_unexpose", remote_name, status="plan", commit=gate["commit"],
                          data={"mode": exposure.get("mode", "ssh-only"), "removes": ["local_caddy", "local_connector"], "attach_only": True})
        if exposure.get("mode") != "public":
            raise HermesError("no integration-owned public route exists", "dashboard_not_exposed")
        _public_stop_connector(entry)
        _public_caddy_remove(entry)
        state["public_exposure"] = {"fqdn": exposure.get("fqdn", PUBLIC_DASHBOARD_FQDN), "mode": "ssh-only", "attach_only": True}
        _remote_state_write(entry, paths, state)
        return result(True, "dashboard_unexpose", remote_name, status="ssh-only", commit=gate["commit"],
                      data={"ssh_forward": _dashboard_forward(remote_name, selected_port)})
    if action == "basic-auth":
        if target not in {"set", "remove"}:
            raise HermesError("dashboard basic-auth action must be set or remove", "invalid_dashboard_basic_auth")
        if not confirm:
            raise HermesError("dashboard Basic Auth changes require --confirm", "confirmation_required")
        exposure = state.setdefault("public_exposure", {"fqdn": PUBLIC_DASHBOARD_FQDN, "mode": "ssh-only", "attach_only": True})
        if target == "remove":
            _ssh(entry, f"if [ \"$(id -u)\" = 0 ]; then SUDO=; else SUDO=sudo; fi; $SUDO rm -f {PUBLIC_BASIC_FRAGMENT}", timeout=30)
            exposure["basic_auth"] = {"enabled": False}
        else:
            user = (basic_auth_user or "").strip()
            secret_name = _secret_reference(basic_auth_secret, "Basic Auth")
            password = resolve_secret(secret_name)
            if not user or not password:
                raise HermesError("dashboard Basic Auth user or secret is missing", "public_exposure_secret_missing")
            hashed = _ssh_stdin(entry, "caddy hash-password --algorithm argon2id", password.encode(), timeout=60)
            if hashed.returncode != 0 or not (hashed.stdout or "").strip():
                raise HermesError("could not generate Basic Auth verifier", "public_exposure_failed", True)
            _public_remote_write(entry, PUBLIC_BASIC_FRAGMENT, f"{user} {(hashed.stdout or '').strip()}\n", "0640")
            exposure["basic_auth"] = {"enabled": True, "user": user}
        if exposure.get("mode") == "public":
            _public_caddy_apply(entry, _public_caddy_fragment(exposure.get("fqdn", PUBLIC_DASHBOARD_FQDN), bool(exposure.get("basic_auth", {}).get("enabled"))))
        state["public_exposure"] = exposure
        _remote_state_write(entry, paths, state)
        return result(True, "dashboard_basic_auth", remote_name, status="configured", commit=gate["commit"],
                      data={"enabled": bool(exposure.get("basic_auth", {}).get("enabled"))})
    raise HermesError("unknown dashboard action", "invalid_dashboard_action")


def _dashboard_authorization_source() -> Path:
    source = Path(__file__).resolve().parents[1] / "hermes" / "dashboard_authorizations"
    if not (source / "dashboard" / "manifest.json").is_file():
        raise HermesError("dashboard authorization plugin bundle is missing", "dashboard_ui_bundle_missing")
    return source


def _dashboard_authorization_catalog(path: str | None = None) -> dict:
    """Return the deliberately small catalog the dashboard companion may authorize."""
    from_default = not path
    if path:
        try:
            raw = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise HermesError("authorization catalog is invalid", "invalid_authorization_catalog") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("jobs"), list):
            raise HermesError("authorization catalog must contain schema_version 1 and jobs", "invalid_authorization_catalog")
        jobs = raw["jobs"]
    else:
        jobs = [{"name": item.name, "kind": item.kind, "enabled": item.enabled, "prompt": item.prompt}
                for item in load_catalog()["jobs"]]
    normalized, names = [], set()
    for item in jobs:
        if not isinstance(item, dict):
            raise HermesError("authorization catalog job is invalid", "invalid_authorization_catalog")
        name, prompt = item.get("name"), item.get("prompt")
        if from_default and (item.get("kind") != "agent" or item.get("enabled") is not True):
            continue
        if (not isinstance(name, str) or not _REPO_NAME_RE.fullmatch(name) or name in names
                or item.get("kind") != "agent" or item.get("enabled") is not True
                or not isinstance(prompt, str) or not prompt.strip() or _contains_credential(prompt)):
            raise HermesError("authorization catalog must contain unique enabled non-secret agent jobs", "invalid_authorization_catalog")
        names.add(name)
        normalized.append({"name": name, "kind": "agent", "enabled": True, "prompt": prompt})
    return {"schema_version": 1, "jobs": normalized}


def _dashboard_authorization_archive(catalog: dict, state_path: str) -> bytes:
    source = _dashboard_authorization_source()
    config = {"state_path": state_path, "catalog_path": "${HOME}/.hermes/sandbox-authorizations/catalog.json"}
    with io.BytesIO() as data:
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            for path in source.rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    archive.add(path, arcname=f"{DASHBOARD_AUTHORIZATION_PLUGIN}/{path.relative_to(source)}")
            for name, payload in (("catalog.json", catalog), ("sandbox-authorization-config.json", config)):
                encoded = json.dumps(payload, sort_keys=True, indent=2).encode() + b"\n"
                info = tarfile.TarInfo(f"{DASHBOARD_AUTHORIZATION_PLUGIN}/{name}")
                info.size, info.mode = len(encoded), 0o600
                archive.addfile(info, io.BytesIO(encoded))
        return data.getvalue()


def dashboard_ui_action(remote_name: str, action: str, *, catalog_path: str | None = None,
                        confirm: bool = False, port: int | str | None = None) -> dict:
    """Install the Sandbox-owned dashboard plugin without modifying Hermes itself."""
    if action == "catalog":
        catalog = _dashboard_authorization_catalog(catalog_path)
        return result(True, "dashboard_ui_catalog", remote_name, status="valid",
                      data={"jobs": [item["name"] for item in catalog["jobs"]]})
    if action not in {"install", "status", "upgrade", "uninstall"}:
        raise HermesError("dashboard-ui action must be install, status, upgrade, uninstall, or catalog", "invalid_dashboard_ui_action")
    if action in {"install", "upgrade", "uninstall"} and not confirm:
        raise HermesError("dashboard UI changes require --confirm", "confirmation_required")
    entry = remote.get_remote(remote_name)
    if not entry or not entry.get("ssh"):
        raise HermesError(f"no SSH remote named '{remote_name}'", "unknown_remote")
    managed = bool(entry.get("provisioned"))
    paths = _paths(entry) if managed else {"state": "${HOME}/.hermes/sandbox-authorizations/state.json"}
    selected_port = validate_dashboard_port(port)
    plugin_root = f"$HOME/.hermes/plugins/{DASHBOARD_AUTHORIZATION_PLUGIN}"
    config_root = "$HOME/.hermes/sandbox-authorizations"
    if action == "status":
        command = (
            f"test -f {plugin_root}/dashboard/manifest.json && "
            f"test -f {plugin_root}/sandbox-authorization-config.json && "
            f"test -f {config_root}/catalog.json"
        )
        observed = _ssh(entry, command, timeout=30)
        return result(observed.returncode == 0, "dashboard_ui_status", remote_name,
                      status="installed" if observed.returncode == 0 else "not_installed",
                      data={"plugin": DASHBOARD_AUTHORIZATION_PLUGIN, "version": DASHBOARD_AUTHORIZATION_VERSION})
    if action == "uninstall":
        command = f'''set -eu
hermes_bin="${{HERMES_BIN:-}}"
if test -z "$hermes_bin"; then hermes_bin="$(command -v hermes || true)"; fi
if test -z "$hermes_bin" && test -x "$HOME/.hermes/hermes-agent/venv/bin/hermes"; then
  hermes_bin="$HOME/.hermes/hermes-agent/venv/bin/hermes"
fi
test -n "$hermes_bin"
target={plugin_root}
config={config_root}
if test -d "$target"; then
  "$hermes_bin" plugins disable {DASHBOARD_AUTHORIZATION_PLUGIN} >/dev/null
fi
rm -rf "$target" "$config"
curl -fsS http://127.0.0.1:{selected_port}/api/dashboard/plugins/rescan >/dev/null || true'''
        _checked(entry, command, timeout=60, what="could not uninstall dashboard authorization plugin")
        return result(True, "dashboard_ui_uninstall", remote_name, status="uninstalled",
                      data={"plugin": DASHBOARD_AUTHORIZATION_PLUGIN})
    if not managed and not catalog_path:
        raise HermesError("standalone dashboard installation requires --authorization-catalog", "authorization_catalog_required")
    catalog = _dashboard_authorization_catalog(catalog_path)
    archive = _dashboard_authorization_archive(catalog, paths["state"])
    command = f'''set -eu
hermes_bin="${{HERMES_BIN:-}}"
if test -z "$hermes_bin"; then hermes_bin="$(command -v hermes || true)"; fi
if test -z "$hermes_bin" && test -x "$HOME/.hermes/hermes-agent/venv/bin/hermes"; then
  hermes_bin="$HOME/.hermes/hermes-agent/venv/bin/hermes"
fi
test -n "$hermes_bin"
"$hermes_bin" dashboard --status >/dev/null
root="$HOME/.hermes/plugins"
config="$HOME/.hermes/sandbox-authorizations"
target="$root/{DASHBOARD_AUTHORIZATION_PLUGIN}"
stage="$(mktemp -d "$root/.sandbox-authorizations.XXXXXX")"
backup="$root/.sandbox-authorizations.previous"
cleanup() {{ rm -rf "$stage"; }}
trap cleanup EXIT
tar -xzf - -C "$stage"
test -f "$stage/{DASHBOARD_AUTHORIZATION_PLUGIN}/dashboard/manifest.json"
test -f "$stage/{DASHBOARD_AUTHORIZATION_PLUGIN}/dashboard/plugin_api.py"
mkdir -p "$config"
chmod 700 "$config"
rm -rf "$backup"
if test -e "$target"; then mv "$target" "$backup"; fi
mv "$stage/{DASHBOARD_AUTHORIZATION_PLUGIN}" "$target"
mv "$target/catalog.json" "$config/catalog.json"
chmod 600 "$config/catalog.json" "$target/sandbox-authorization-config.json"
"$hermes_bin" plugins enable {DASHBOARD_AUTHORIZATION_PLUGIN} --no-allow-tool-override >/dev/null
activation=restart_required
if curl -fsS http://127.0.0.1:{selected_port}/api/dashboard/plugins/rescan >/dev/null; then
  activation=rescanned
fi
printf 'activation=%s\n' "$activation"
rm -rf "$backup"'''
    _checked(entry, "mkdir -p \"$HOME/.hermes/plugins\"", timeout=30, what="could not prepare Hermes plugin directory")
    uploaded = _ssh_stdin(entry, command, archive, timeout=120)
    if uploaded.returncode != 0:
        raise HermesError(_redact(uploaded.stderr.decode(errors="replace") or "dashboard plugin install failed", entry)[:1000],
                          "dashboard_ui_install_failed", True)
    output = (uploaded.stdout or b"").decode(errors="replace") if isinstance(uploaded.stdout, bytes) else str(uploaded.stdout or "")
    activation = "rescanned" if "activation=rescanned" in output else "restart_required"
    return result(True, f"dashboard_ui_{action}", remote_name,
                  status="installed" if activation == "rescanned" else "pending_activation",
                  data={"plugin": DASHBOARD_AUTHORIZATION_PLUGIN, "version": DASHBOARD_AUTHORIZATION_VERSION,
                        "catalog_jobs": [item["name"] for item in catalog["jobs"]], "activation": activation,
                        "next": None if activation == "rescanned" else "restart the Hermes dashboard to activate the plugin"})
