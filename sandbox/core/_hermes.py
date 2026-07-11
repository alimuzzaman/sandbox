"""Hermes control-plane helpers for a configured Sandbox remote.

The module deliberately keeps Hermes host-native: every remote command runs as
the existing Sandbox account, uses that account's ``SANDBOX_HOME``, and invokes
the remote ``sb`` executable directly.  It never creates a second WordPress
registry or exposes a new network listener.
"""
from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import sandbox.core._remote as remote


SUPPORTED_TAG = "v2026.7.7.2"
SUPPORTED_COMMIT = "9de9c25f620ff7f1ce0fd5457d596052d5159596"
GATEWAY_UNIT = "hermes-gateway-sandbox.service"
DASHBOARD_LOOPBACK_HOST = "127.0.0.1"
DASHBOARD_PORT = 9119
STATE_SCHEMA = 1
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_JOB_RE = re.compile(r"^[0-9a-f]{16}$")
_SECRET_RE = re.compile(r"(?i)\b(token|password|secret|authorization)\b\s*[=:]\s*(?:bearer\s+)?[^\s,]+")
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


def _redact(value: str, entry: dict | None = None) -> str:
    text = remote.redact_ssh_connection(str(value or ""), entry)
    return _SECRET_RE.sub(lambda m: m.group(1) + "=[redacted]", text)


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
        "version": version,
        "commit": commit,
        "status": status,
        "repo": repo,
        "path": path,
        "job_id": job_id,
        "data": data or {},
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


def _ssh(entry: dict, command: str, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return remote.ssh_run(entry, command, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise HermesError(_redact(str(exc), entry), "remote_unavailable", True) from exc


def _checked(entry: dict, command: str, timeout: int = 60, *, what: str) -> subprocess.CompletedProcess:
    res = _ssh(entry, command, timeout)
    if res.returncode != 0:
        detail = _redact(res.stderr or res.stdout or what, entry)[:1000]
        raise HermesError(f"{what}: {detail}", "remote_command_failed", True)
    return res


def _sandbox_home(entry: dict) -> str:
    try:
        return remote.resolve_sandbox_home(entry)
    except RuntimeError as exc:
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
    return {"schema_version": STATE_SCHEMA, "repositories": {}, "sessions": {}, "gates": {}}


def _normalize_state(data: dict) -> dict:
    """Migrate the original unversioned state shape before using it."""
    if not isinstance(data, dict):
        raise HermesError("unsupported Hermes state schema", "invalid_state")
    schema = data.get("schema_version", 0)
    if schema == 0:
        data = dict(data)
        data["schema_version"] = STATE_SCHEMA
    elif schema != STATE_SCHEMA:
        raise HermesError("unsupported Hermes state schema", "invalid_state")
    for key in ("repositories", "sessions", "gates"):
        data.setdefault(key, {})
        if not isinstance(data[key], dict):
            raise HermesError("invalid Hermes state collection", "invalid_state")
    return data


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


def _remote_state_write(entry: dict, paths: dict, state: dict) -> None:
    payload = base64.b64encode((json.dumps(state, sort_keys=True) + "\n").encode()).decode()
    target = shlex.quote(paths["state"])
    lock = shlex.quote(paths["state"] + ".lock")
    command = (
        f"mkdir -p {shlex.quote(paths['sandbox_home'] + '/runtime')}; "
        f"exec 9>{lock}; flock -w 30 9; "
        f"tmp={target}.tmp.$$; echo {shlex.quote(payload)} | base64 -d > \"$tmp\"; "
        f"chmod 600 \"$tmp\"; mv \"$tmp\" {target}"
    )
    _checked(entry, command, what="could not write Hermes state")


def _remote_state_read(entry: dict, paths: dict) -> dict:
    res = _ssh(entry, f"if test -f {shlex.quote(paths['state'])}; then cat {shlex.quote(paths['state'])}; fi")
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not read Hermes state", entry), "state_read_failed", True)
    raw = (res.stdout or "").strip()
    if not raw:
        return _new_state()
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HermesError("remote Hermes state is invalid", "invalid_state") from exc
    try:
        return _normalize_state(state)
    except HermesError as exc:
        raise HermesError("remote Hermes state schema is unsupported", "invalid_state") from exc


def render_profile(sandbox_home: str, sb_path: str) -> dict:
    """Return the integration-owned Hermes config values without secrets."""
    return {
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


def _resolve_commit(entry: dict, tag: str, expected: str | None) -> str:
    res = _checked(entry,
                   f"git ls-remote https://github.com/NousResearch/hermes-agent.git refs/tags/{shlex.quote(tag)}^{{}}",
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


def install(remote_name: str, version: str = SUPPORTED_TAG, commit: str | None = None) -> dict:
    _validate_release_tag(version)
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    resolved = _resolve_commit(entry, version, _expected_commit(version, commit))
    tag, resolved = validate_release(version, resolved)
    command = (
        "set -eu; "
        f"mkdir -p {shlex.quote(paths['repo_root'])}; "
        "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- "
        f"--branch {shlex.quote(tag)} --commit {shlex.quote(resolved)} --skip-setup --non-interactive "
        f"--dir \"$HOME/.hermes/hermes-agent\" --hermes-home \"$HOME/.hermes\""
    )
    _checked(entry, command, timeout=1800, what="Hermes installation failed")
    version_res = _checked(entry, f"{paths['launcher']} --version", what="Hermes version check failed")
    state = _remote_state_read(entry, paths)
    state["installation"] = {"release_tag": tag, "commit": resolved, "status": "installed"}
    _remote_state_write(entry, paths, state)
    return result(True, "install", remote_name, version=tag, commit=resolved, status="installed",
                  data={"launcher": paths["launcher"], "reported_version": (version_res.stdout or "").strip()[:200]})


def setup(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    profile = render_profile(paths["sandbox_home"], paths["sb"])
    payload = base64.b64encode(json.dumps(profile).encode()).decode()
    # Hermes' managed virtualenv supplies PyYAML; use it remotely so unrelated
    # upstream config keys survive the merge rather than concatenating YAML.
    command = f"""set -eu
mkdir -p \"$HOME/.hermes\" {shlex.quote(paths['repo_root'])}
test -x {paths['launcher']}
payload={shlex.quote(payload)}
{paths['launcher']} --version >/dev/null
if test -f "$HOME/.hermes/sandbox-integration.json"; then
  cp "$HOME/.hermes/sandbox-integration.json" "$HOME/.hermes/sandbox-integration.json.backup"
  chmod 600 "$HOME/.hermes/sandbox-integration.json.backup"
fi
{paths['launcher']} mcp remove sandbox >/dev/null 2>&1 || true
{paths['launcher']} mcp add sandbox --command {shlex.quote(paths['sb'])} --args mcp >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.env.SANDBOX_HOME {shlex.quote(paths['sandbox_home'])} >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.enabled true >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.connect_timeout 60 >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.timeout 1200 >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.supports_parallel_tool_calls false >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.tools.resources true >/dev/null
{paths['launcher']} config set mcp_servers.sandbox.tools.prompts true >/dev/null
{paths['launcher']} config set terminal.backend local >/dev/null
{paths['launcher']} config set terminal.home_mode real >/dev/null
{paths['launcher']} config set terminal.cwd {shlex.quote(paths['repo_root'])} >/dev/null
{paths['launcher']} config set approvals.mode manual >/dev/null
{paths['launcher']} config set approvals.cron_mode deny >/dev/null
{paths['launcher']} config set approvals.mcp_reload_confirm true >/dev/null
{paths['launcher']} config set approvals.destructive_slash_confirm true >/dev/null
python3 - <<'PY'
import base64, json, pathlib
p = pathlib.Path.home() / '.hermes' / 'sandbox-integration.json'
p.write_text(base64.b64decode({payload!r}).decode())
p.chmod(0o600)
PY
"""
    _checked(entry, command, timeout=180, what="Hermes setup failed")
    state = _remote_state_read(entry, paths)
    state.setdefault("installation", {})["status"] = "configured"
    state["profile"] = {"sandbox_home": paths["sandbox_home"], "sandbox_sb": paths["sb"]}
    _remote_state_write(entry, paths, state)
    return result(True, "setup", remote_name, status="configured",
                  data={"mcp_server": "sandbox", "parallel_calls": False, "full_catalog": True})


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


def health(remote_name: str) -> dict:
    """Return a bounded operational view without performing repair."""
    diagnostic = doctor(remote_name)
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    state = _remote_state_read(entry, paths)
    state, stale_jobs = _reconcile_sessions(entry, paths, state)
    service = _ssh(entry, (
        "if command -v systemctl >/dev/null 2>&1; then systemctl --user is-active hermes-gateway-sandbox.service 2>/dev/null || true; "
        "else echo unavailable; fi; "
        "if command -v loginctl >/dev/null 2>&1; then loginctl show-user \"$USER\" -p Linger --value 2>/dev/null || true; "
        "else echo unavailable; fi"), timeout=30)
    lines = (service.stdout or "").splitlines()
    gateway_state = lines[0].strip() if lines else "unknown"
    linger = lines[1].strip().lower() if len(lines) > 1 else "unknown"
    sessions = state["sessions"].values()
    diagnostic_error = diagnostic["error"]
    error = None if diagnostic_error is None else HermesError(
        diagnostic_error["message"], diagnostic_error["code"], diagnostic_error["retryable"])
    return result(
        diagnostic["ok"], "health", remote_name, status="healthy" if diagnostic["ok"] else "degraded",
        data={
            "checks": diagnostic["data"]["checks"],
            "gateway": {"state": gateway_state, "linger": linger},
            "sessions": {
                "running": sum(session.get("state") == "running" for session in sessions),
                "stale": sum(session.get("state") == "stale" for session in sessions),
            },
            "stale_jobs": stale_jobs,
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
    status = "passed" if record.get("status") == "passed" and revision_matches and not missing else "pending"
    return {
        "status": status,
        "commit": current_commit,
        "recorded_at": record.get("recorded_at"),
        "revision_matches": revision_matches,
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
        f"find {shlex.quote(paths['repo_root'])} -type d -path '*/.worktrees/*' 2>/dev/null | wc -l | awk '{{print \"worktrees=\" $1}}'"), timeout=30)
    values = dict(line.split("=", 1) for line in (probe.stdout or "").splitlines() if "=" in line)
    try:
        metrics = {key: int(values.get(key, "0")) for key in ("disk_mb", "memory_mb", "jobs", "worktrees")}
    except ValueError as exc:
        raise HermesError("resource preflight returned invalid data", "resource_preflight_failed", True) from exc
    if metrics["disk_mb"] < policy["min_free_disk_mb"] or metrics["memory_mb"] < policy["min_free_memory_mb"]:
        raise HermesError("insufficient remote disk or memory for Hermes", "resource_limit", True)
    if metrics["jobs"] >= policy["max_jobs"] or metrics["worktrees"] >= policy["max_worktrees"]:
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


def backup_restore(remote_name: str, backup_id: str, confirm: bool) -> dict:
    if not confirm:
        raise HermesError("backup restore requires --confirm", "confirmation_required")
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    archive = _backup_archive(paths, backup_id)
    # A pre-restore snapshot is deliberately taken before stopping services or
    # replacing files, so a bad but syntactically valid archive remains
    # recoverable.  Its credentials exclusions match ordinary V2 backups.
    pre_restore = backup_create(remote_name)
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
        "if command -v systemctl >/dev/null 2>&1; then systemctl --user stop hermes-gateway-sandbox.service 2>/dev/null || true; fi; "
        "tar -C \"$source\" -cf - .hermes | tar -C \"$HOME\" -xf -; "
        f"if test -f \"$stage/runtime/hermes.json\"; then tmp={shlex.quote(paths['state'])}.restore.$$; cp \"$stage/runtime/hermes.json\" \"$tmp\"; chmod 600 \"$tmp\"; mv \"$tmp\" {shlex.quote(paths['state'])}; fi; "
        "if command -v systemctl >/dev/null 2>&1; then systemctl --user start hermes-gateway-sandbox.service 2>/dev/null || true; fi"
    )
    _checked(entry, command, timeout=300, what="Hermes backup restore failed")
    return result(True, "backup_restore", remote_name, status="restored",
                  data={"backup_id": backup_id, "pre_restore_backup_id": pre_restore["data"]["backup_id"]})


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
        health_result = health(remote_name)
        if not health_result["ok"]:
            raise HermesError("post-update health check failed", "update_health_failed")
        if gateway_was_active:
            _checked(entry, "systemctl --user start hermes-gateway-sandbox.service", timeout=60,
                     what="could not resume Hermes gateway")
    except HermesError as exc:
        try:
            backup_restore(remote_name, backup_id, True)
        except HermesError:
            pass
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
    command = (
        f"set -eu; mkdir -p {shlex.quote(backup_root)}; chmod 700 {shlex.quote(backup_root)}; "
        "test -d \"$HOME/.hermes\"; "
        "stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; mkdir -p \"$stage/home\" \"$stage/runtime\"; "
        "tar -C \"$HOME\" --exclude='.hermes/auth.json' --exclude='.hermes/sessions' "
        "--exclude='.hermes/checkpoints' -cf - .hermes | tar -C \"$stage/home\" -xf -; "
        f"if test -f {shlex.quote(paths['state'])}; then cp {shlex.quote(paths['state'])} \"$stage/runtime/hermes.json\"; fi; "
        f"tar -C \"$stage\" -czf {shlex.quote(archive)} home runtime; chmod 600 {shlex.quote(archive)}; "
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
        f"if test -d {shlex.quote(paths['repo_root'])}; then "
        f"for repo in {shlex.quote(paths['repo_root'])}/*; do test -d \"$repo/.git\" || continue; "
        "git -C \"$repo\" worktree list --porcelain | awk '/^worktree / {print $2}' | "
        "while IFS= read -r wt; do case \"$wt\" in \"$repo\"/.worktrees/*) ;; *) continue;; esac; "
        "if test -n \"$(git -C \"$wt\" status --porcelain)\"; then printf 'dirty\\t%s\\n' \"$wt\"; "
        "else printf 'clean\\t%s\\n' \"$wt\"; fi; done; done; fi"
    )
    res = _ssh(entry, command, timeout=60)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "could not inspect worktrees", entry), "cleanup_scan_failed", True)
    clean, dirty, active = [], [], []
    for line in (res.stdout or "").splitlines():
        kind, _, path = line.partition("\t")
        if kind == "clean" and path:
            (active if path in active_worktrees else clean).append(path)
        elif kind == "dirty" and path:
            dirty.append(path)
    if not confirm:
        return result(True, "cleanup", remote_name, status="dry_run",
                      data={"clean_candidates": clean, "dirty_retained": dirty,
                            "active_retained": active, "stale_jobs": stale_jobs,
                            "requires_confirm": bool(clean)})
    if dry_run:
        return result(True, "cleanup", remote_name, status="dry_run",
                      data={"clean_candidates": clean, "dirty_retained": dirty,
                            "active_retained": active, "stale_jobs": stale_jobs,
                            "requires_confirm": False})
    removed = []
    for path in clean:
        # Worktree removal is executed from its own containing repository and
        # never passes --force; Git independently rechecks cleanliness.
        parent = path.split("/.worktrees/", 1)[0]
        remove = _ssh(entry, f"git -C {shlex.quote(parent)} worktree remove {shlex.quote(path)}")
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


def _worktree_setup(paths: dict, repo_name: str) -> str:
    """Create one worktree while holding a repository-scoped advisory lock."""
    repo = f"{paths['repo_root']}/{repo_name}"
    lock = f"{paths['locks']}/{repo_name}.lock"
    return (
        f"mkdir -p {shlex.quote(paths['locks'])}; exec 9>{shlex.quote(lock)}; flock -w 30 9; "
        f"cd {shlex.quote(repo)}; mkdir -p .worktrees; attempt=0; while :; do "
        "id=$(python3 -c 'import secrets; print(secrets.token_hex(8))'); branch=hermes/hermes-$id; cwd=\"$PWD/.worktrees/$id\"; "
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
        expected_prefix = f"{paths['repo_root']}/{repo_name}/.worktrees/"
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
        "ExecStart=%h/.local/bin/hermes gateway run\n"
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


def dashboard_action(remote_name: str, action: str, **_kwargs) -> dict:
    """V3 is intentionally unavailable until a real V2 acceptance gate exists."""
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    gate = _v2_gate(_remote_state_read(entry, paths))
    if gate["status"] != "passed":
        missing = ", ".join(gate["missing_checks"]) or "a current acceptance record"
        raise HermesError(f"dashboard is blocked until V2 acceptance passes ({missing})", "v2_gate_required")
    raise HermesError("dashboard implementation has not started", "dashboard_not_implemented")
