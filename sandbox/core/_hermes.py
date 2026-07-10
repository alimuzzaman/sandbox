"""Hermes control-plane helpers for a configured Sandbox remote.

The module deliberately keeps Hermes host-native: every remote command runs as
the existing Sandbox account, uses that account's ``SANDBOX_HOME``, and invokes
the remote ``sb`` executable directly.  It never creates a second WordPress
registry or exposes a new network listener.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import sandbox.core._remote as remote


SUPPORTED_TAG = "v2026.7.7.2"
STATE_SCHEMA = 1
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_JOB_RE = re.compile(r"^[0-9a-f]{16}$")
_SECRET_RE = re.compile(r"(?i)(token|password|secret|authorization)\s*[=:]\s*[^\s,]+")


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


def validate_release(tag: str, commit: str) -> tuple[str, str]:
    tag = (tag or "").strip()
    commit = (commit or "").strip().lower()
    if not tag.startswith("v") or tag in {"main", "master", "latest"}:
        raise HermesError("Hermes release must be an immutable version tag", "invalid_release")
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
        "hermes_home": "$HOME/.hermes",
        "launcher": "$HOME/.local/bin/hermes",
    }


def read_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA, "repositories": {}, "sessions": {}, "gates": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HermesError(f"invalid Hermes state: {exc}", "invalid_state") from exc
    if not isinstance(data, dict) or data.get("schema_version") != STATE_SCHEMA:
        raise HermesError("unsupported Hermes state schema", "invalid_state")
    for key in ("repositories", "sessions", "gates"):
        data.setdefault(key, {})
    return data


def write_state(path: Path, state: dict) -> None:
    if state.get("schema_version") != STATE_SCHEMA:
        raise HermesError("cannot write an unsupported Hermes state schema", "invalid_state")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    os.chmod(temp_name, 0o600)
    Path(temp_name).replace(path)
    path.chmod(0o600)


def _remote_state_write(entry: dict, paths: dict, state: dict) -> None:
    payload = base64.b64encode((json.dumps(state, sort_keys=True) + "\n").encode()).decode()
    target = shlex.quote(paths["state"])
    command = (
        f"mkdir -p {shlex.quote(paths['sandbox_home'] + '/runtime')}; "
        f"tmp={target}.tmp.$$; echo {shlex.quote(payload)} | base64 -d > \"$tmp\"; "
        f"chmod 600 \"$tmp\"; mv \"$tmp\" {target}"
    )
    _checked(entry, command, what="could not write Hermes state")


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


def install(remote_name: str, version: str = SUPPORTED_TAG, commit: str | None = None) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    resolved = _resolve_commit(entry, version, commit)
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
    state = {"schema_version": STATE_SCHEMA, "repositories": {}, "sessions": {}, "gates": {},
             "installation": {"release_tag": tag, "commit": resolved, "status": "installed"}}
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
{paths['launcher']} mcp remove sandbox >/dev/null 2>&1 || true
{paths['launcher']} mcp add sandbox --command {shlex.quote(paths['sb'])} --args mcp >/dev/null
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
    state = {"schema_version": STATE_SCHEMA, "repositories": {}, "sessions": {}, "gates": {},
             "installation": {"release_tag": None, "commit": None, "status": "configured"},
             "profile": {"sandbox_home": paths["sandbox_home"], "sandbox_sb": paths["sb"]}}
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
    return result(healthy, "status", remote_name, status="healthy" if healthy else "absent",
                  data={"direct_sb": res.returncode == 0,
                        "reported_version": _redact((res.stdout or "").strip(), entry)[:200]},
                  error=None if healthy else HermesError("Hermes is not installed or Sandbox sb is unavailable", "not_ready"))


def doctor(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    command = (
        f"for item in git docker python3 systemctl {paths['launcher']} {shlex.quote(paths['sb'])}; do "
        "if command -v \"$item\" >/dev/null 2>&1 || test -x \"$item\"; then printf '%s=1\\n' \"$item\"; "
        "else printf '%s=0\\n' \"$item\"; fi; done; "
        "df -Pk . | tail -n1 | awk '{print \"free_kb=\" $4}'; "
        "awk '/MemAvailable:/ {print \"mem_kb=\" $2}' /proc/meminfo"
    )
    res = _ssh(entry, command, timeout=30)
    checks = dict(line.split("=", 1) for line in (res.stdout or "").splitlines() if "=" in line)
    required = ["git", "docker", "python3", "systemctl", paths["launcher"], paths["sb"]]
    healthy = res.returncode == 0 and all(checks.get(key) == "1" for key in required)
    return result(healthy, "doctor", remote_name, status="healthy" if healthy else "degraded",
                  data={"checks": checks, "mcp_catalog_complete": None, "direct_sb": checks.get(paths["sb"]) == "1"},
                  error=None if healthy else HermesError("remote Hermes prerequisites are incomplete", "doctor_failed", True))


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
        f"if test -e {shlex.quote(destination)}; then echo EXISTS; exit 3; fi; "
        f"git clone -- {shlex.quote(safe_url)} {shlex.quote(temp)}{ref_arg}; "
        f"git -C {shlex.quote(temp)} rev-parse --is-inside-work-tree >/dev/null; "
        f"mv {shlex.quote(temp)} {shlex.quote(destination)}"
    )
    res = _ssh(entry, command, timeout=900)
    if res.returncode != 0:
        code = "duplicate_repo" if res.returncode == 3 else "clone_failed"
        raise HermesError(_redact(res.stderr or res.stdout or "clone failed", entry)[:1000], code, code == "clone_failed")
    return result(True, "repo_clone", remote_name, status="ready", repo=repo_name, path=destination,
                  data={"origin": safe_url, "ref": ref})


def list_repos(remote_name: str) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    command = (
        f"if test -d {shlex.quote(paths['repo_root'])}; then "
        f"find {shlex.quote(paths['repo_root'])} -mindepth 1 -maxdepth 1 -type d -name .worktrees -prune -o "
        f"-type d -exec sh -c 'test -d \"$1/.git\" || git -C \"$1\" rev-parse --git-dir >/dev/null 2>&1; "
        "if [ $? -eq 0 ]; then basename \"$1\"; fi' sh {} \\; ; fi"
    )
    res = _ssh(entry, command, timeout=30)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or "repository list failed", entry), "repo_list_failed", True)
    repos = sorted({line.strip() for line in (res.stdout or "").splitlines() if _REPO_NAME_RE.fullmatch(line.strip())})
    return result(True, "repo_list", remote_name, status="ready", data={"repositories": repos})


def _worktree_command(paths: dict, repo_name: str, prompt: str, *, worktree: bool, async_: bool) -> str:
    repo = f"{paths['repo_root']}/{repo_name}"
    prompt_b64 = base64.b64encode(prompt.encode()).decode()
    if not prompt or len(prompt) > 32_000:
        raise HermesError("prompt must be between 1 and 32000 characters", "invalid_prompt")
    setup = f"cwd={shlex.quote(repo)}; worktree=false"
    if worktree:
        setup = (
            f"cd {shlex.quote(repo)}; mkdir -p .worktrees; id=$(python3 -c 'import secrets; print(secrets.token_hex(8))'); "
            "branch=hermes/hermes-$id; cwd=\"$PWD/.worktrees/$id\"; "
            "git worktree add -b \"$branch\" \"$cwd\" HEAD; worktree=true"
        )
    action = (
        "prompt=$(echo " + shlex.quote(prompt_b64) + " | base64 -d); "
        f"cd \"$cwd\"; HERMES_HOME={paths['hermes_home']} {paths['launcher']} chat -q \"$prompt\""
    )
    if not async_:
        return f"set -eu; test -d {shlex.quote(repo)}; {setup}; {action}"
    return (
        f"set -eu; test -d {shlex.quote(repo)}; {setup}; mkdir -p {shlex.quote(paths['jobs'])}; "
        "job=$(python3 -c 'import secrets; print(secrets.token_hex(8))'); "
        f"( {action} > {shlex.quote(paths['jobs'])}/\"$job\".log 2>&1; echo $? > {shlex.quote(paths['jobs'])}/\"$job\".status ) & "
        "echo \"$job\""
    )


def run(remote_name: str, repo: str, prompt: str, *, worktree: bool = True,
        async_: bool = True, timeout: int = 1200) -> dict:
    entry = _require_remote(remote_name)
    repo_name = validate_repo_name(repo)
    if timeout < 1 or timeout > 3600:
        raise HermesError("timeout must be between 1 and 3600 seconds", "invalid_timeout")
    paths = _paths(entry)
    command = _worktree_command(paths, repo_name, prompt, worktree=worktree, async_=async_)
    res = _ssh(entry, command, timeout=30 if async_ else timeout)
    if res.returncode != 0:
        raise HermesError(_redact(res.stderr or res.stdout or "Hermes run failed", entry)[:2000], "run_failed", True)
    if async_:
        job_id = (res.stdout or "").strip().splitlines()[-1] if (res.stdout or "").strip() else ""
        if not _JOB_RE.fullmatch(job_id):
            raise HermesError("Hermes launch did not return a valid job id", "invalid_job_response")
        return result(True, "run", remote_name, status="queued", repo=repo_name, job_id=job_id,
                      data={"worktree": worktree})
    return result(True, "run", remote_name, status="completed", repo=repo_name,
                  data={"worktree": worktree, "output": _redact(res.stdout, entry)[-4000:]})


def chat(remote_name: str, repo: str, *, worktree: bool = True) -> dict:
    """Open Hermes through an interactive SSH TTY, optionally in a worktree."""
    entry = _require_remote(remote_name)
    repo_name = validate_repo_name(repo)
    paths = _paths(entry)
    repo_path = f"{paths['repo_root']}/{repo_name}"
    if worktree:
        prepare = (
            f"set -eu; cd {shlex.quote(repo_path)}; mkdir -p .worktrees; "
            "id=$(python3 -c 'import secrets; print(secrets.token_hex(8))'); "
            "cwd=\"$PWD/.worktrees/$id\"; git worktree add -b \"hermes/hermes-$id\" \"$cwd\" HEAD; echo \"$cwd\""
        )
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


def gateway(remote_name: str, action: str, allowlist: list[str] | None = None, lines: int = 200) -> dict:
    entry = _require_remote(remote_name)
    paths = _paths(entry)
    unit = "hermes-gateway-sandbox.service"
    if action in {"setup", "install", "start", "restart"}:
        validate_gateway_allowlist(allowlist)
    if action == "install":
        body = (
            "[Unit]\\nDescription=Hermes Sandbox gateway\\n[Service]\\n"
            f"Environment=HERMES_HOME={paths['hermes_home']}\\nExecStart={paths['launcher']} gateway run\\n"
            "Restart=on-failure\\n[Install]\\nWantedBy=default.target\\n"
        )
        encoded = base64.b64encode(body.encode()).decode()
        command = ("mkdir -p $HOME/.config/systemd/user; echo " + shlex.quote(encoded) +
                   " | base64 -d > $HOME/.config/systemd/user/" + unit +
                   "; systemctl --user daemon-reload; systemctl --user enable " + unit)
    elif action in {"start", "stop", "restart", "status"}:
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
    return result(True, f"gateway_{action}", remote_name, status="active" if action in {"start", "restart", "status"} else "configured",
                  data={"output": _redact(res.stdout, entry)[-4000:] if action == "logs" else ""})


def dashboard_action(remote_name: str, action: str, **_kwargs) -> dict:
    """V3 is intentionally unavailable until a real V2 acceptance gate exists."""
    raise HermesError("dashboard is blocked until the V2 operations gate passes", "v2_gate_required")
