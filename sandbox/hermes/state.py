from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import base64
import re
import shlex


class HermesStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesState:
    schema_version: int = 1
    installation: dict[str, Any] | None = None
    sessions: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value = dict(self.extra or {})
        value.update({
            "schema_version": self.schema_version,
            "installation": dict(self.installation or {}),
            "sessions": dict(self.sessions or {}),
        })
        return value


class HermesStateRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def read(self) -> HermesState:
        if not self.path.exists():
            return HermesState()
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise HermesStateError(f"invalid Hermes state: {exc}") from exc
        if not isinstance(value, dict):
            raise HermesStateError("invalid Hermes state: root must be an object")
        if (isinstance(value.get("schema_version", 1), bool) or
                value.get("schema_version", 1) != 1):
            raise HermesStateError("unsupported Hermes state schema")
        for field in ("installation", "sessions"):
            if field in value and value[field] is not None and not isinstance(value[field], dict):
                raise HermesStateError(f"invalid Hermes state: {field} must be an object")
        known = {"schema_version", "installation", "sessions"}
        return HermesState(
            schema_version=1,
            installation=value.get("installation") or {},
            sessions=value.get("sessions") or {},
            extra={key: item for key, item in value.items() if key not in known},
        )

    def write(self, state: HermesState) -> None:
        if (isinstance(state.schema_version, bool) or state.schema_version != 1 or
                not isinstance(state.installation, (dict, type(None))) or
                not isinstance(state.sessions, (dict, type(None))) or
                not isinstance(state.extra, (dict, type(None)))):
            raise HermesStateError("unsupported Hermes state schema")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(state.as_dict(), stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
                directory_fd = os.open(str(self.path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)


_STATE_MANIFEST_FILES = {
    "manifest.json",
    "hermes/sandbox-integration.json",
    "hermes/sandbox-resource-policy.json",
    "hermes/sandbox-gateway-allowlist.json",
    "hermes/SOUL.md",
    "sandbox/hermes.json",
}
_FORBIDDEN_NAMES = re.compile(
    r"(?i)(?:^|/)(?:auth\.json|credentials[^/]*|cookies[^/]*|sessions[^/]*|"
    r"checkpoints[^/]*|state\.db[^/]*|[^/]*\.(?:pem|key))$"
)
_SECRET_CONTENT = re.compile(
    r"(?ix)(?:github_pat_[a-z0-9_]{20,}|gh[pousr]_[a-z0-9]{20,}|"
    r"sk-(?:proj-)?[a-z0-9_-]{20,}|BEGIN\s+(?:RSA|OPENSSH|EC|PRIVATE)\s+KEY|"
    r"(?:api[_-]?key|token|password|passphrase|secret|authorization)\s*[:=]\s*\S{8,})"
)


def state_restore_command(paths: dict[str, str], repository: str) -> str:
    """Build the remote state restore transaction without following symlinks.

    The compatibility caller owns transport and result envelopes.  This module
    owns validation and the staged swap so the restore can be tested without a
    remote or a state repository credential.
    """
    validator = r'''
import json, os, re, stat, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest_path = root / "manifest.json"
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(40)
if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
    raise SystemExit(40)
revision = manifest.get("revision")
if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
    raise SystemExit(40)
allowed = {
    "manifest.json", "hermes/sandbox-integration.json",
    "hermes/sandbox-resource-policy.json", "hermes/sandbox-gateway-allowlist.json",
    "hermes/SOUL.md", "sandbox/hermes.json",
}
for path in root.rglob("*"):
    relative = path.relative_to(root).as_posix()
    if relative == ".git" or relative.startswith(".git/"):
        continue
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise SystemExit(41)
    if stat.S_ISDIR(mode):
        if relative not in {"hermes", "hermes/memories", "sandbox"} and not relative.startswith("hermes/memories/"):
            raise SystemExit(41)
        continue
    if relative not in allowed and not relative.startswith("hermes/memories/"):
        raise SystemExit(41)
    if re.search(r"(?i)(?:^|/)(?:auth\.json|credentials[^/]*|cookies[^/]*|sessions[^/]*|checkpoints[^/]*|state\.db[^/]*|[^/]*\.(?:pem|key))$", relative):
        raise SystemExit(42)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raise SystemExit(41)
    if re.search(r"(?ix)(?:github_pat_[a-z0-9_]{20,}|gh[pousr]_[a-z0-9]{20,}|sk-(?:proj-)?[a-z0-9_-]{20,}|BEGIN\s+(?:RSA|OPENSSH|EC|PRIVATE)\s+KEY|(?:api[_-]?key|token|password|passphrase|secret|authorization)\s*[:=]\s*\S{8,})", content):
        raise SystemExit(43)
'''
    encoded = base64.b64encode(validator.encode()).decode()
    repo = shlex.quote(repository)
    sandbox_home = shlex.quote(paths["sandbox_home"])
    state = shlex.quote(paths["state"])
    return (
        "set -eu; umask 077; stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; "
        f"git clone --quiet --depth=1 {repo} \"$stage/repo\"; "
        f"python3 -c 'import base64,sys; exec(compile(base64.b64decode(sys.argv[1]), \"<state-validator>\", \"exec\"))' {shlex.quote(encoded)} \"$stage/repo\"; "
        "mkdir -p \"$stage/new-hermes\" \"$stage/new-runtime\"; "
        "for safe in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json SOUL.md; do "
        "test ! -f \"$stage/repo/hermes/$safe\" || install -m 600 \"$stage/repo/hermes/$safe\" \"$stage/new-hermes/$safe\"; done; "
        "test ! -d \"$stage/repo/hermes/memories\" || cp -a --no-dereference \"$stage/repo/hermes/memories\" \"$stage/new-hermes/memories\"; "
        f"test ! -f \"$stage/repo/sandbox/hermes.json\" || install -m 600 \"$stage/repo/sandbox/hermes.json\" \"$stage/new-runtime/hermes.json\"; "
        f"mkdir -p \"$HOME\" {sandbox_home}/runtime; old_home=\"$HOME/.hermes.state-old.$$\"; old_state={state}.state-old.$$; had_home=0; had_state=0; committed=0; "
        "rollback() { rc=$?; if test \"$committed\" = 0; then set +e; if test \"$had_home\" = 1; then rm -rf \"$HOME/.hermes\"; test ! -e \"$old_home\" || mv \"$old_home\" \"$HOME/.hermes\"; else rm -rf \"$HOME/.hermes\"; fi; if test \"$had_state\" = 1; then rm -f " + state + "; test ! -e \"$old_state\" || mv \"$old_state\" " + state + "; else rm -f " + state + "; fi; fi; rm -rf \"$stage\"; exit \"$rc\"; }; trap rollback EXIT; "
        "test ! -e \"$HOME/.hermes\" || { mv \"$HOME/.hermes\" \"$old_home\"; had_home=1; }; "
        f"test ! -e {state} || {{ mv {state} \"$old_state\"; had_state=1; }}; "
        "mv \"$stage/new-hermes\" \"$HOME/.hermes\"; "
        f"test ! -e \"$stage/new-runtime/hermes.json\" || mv \"$stage/new-runtime/hermes.json\" {state}; "
        "committed=1; rm -rf \"$old_home\"; rm -f \"$old_state\"; printf '%s\\n' restored"
    )


def state_sync_command(paths: dict[str, str], repository: str) -> str:
    """Export only owned state files while serializing mutations per repository."""
    repo = shlex.quote(repository)
    lock = shlex.quote(f"{paths['locks']}/state-sync.lock")
    state = shlex.quote(paths["state"])
    return (
        "set -eu; umask 077; stage=$(mktemp -d); trap 'rm -rf \"$stage\"' EXIT; "
        f"mkdir -p {shlex.quote(paths['locks'])}; exec 9>{lock}; flock -w 30 9; "
        f"git clone --quiet {repo} \"$stage/repo\"; mkdir -p \"$stage/repo/hermes\" \"$stage/repo/sandbox\"; "
        "for src in sandbox-integration.json sandbox-resource-policy.json sandbox-gateway-allowlist.json SOUL.md; do "
        "test ! -f \"$HOME/.hermes/$src\" || install -m 600 \"$HOME/.hermes/$src\" \"$stage/repo/hermes/$src\"; done; "
        "test ! -d \"$HOME/.hermes/memories\" || cp -a --no-dereference \"$HOME/.hermes/memories\" \"$stage/repo/hermes/memories\"; "
        f"test ! -f {state} || install -m 600 {state} \"$stage/repo/sandbox/hermes.json\"; "
        "if find \"$stage/repo/hermes\" \"$stage/repo/sandbox\" -type l -o -type f \\( -iname 'auth.json' -o -iname 'credentials*' -o -iname 'cookies*' -o -iname 'sessions*' -o -iname 'checkpoints*' -o -iname '*.pem' -o -iname '*.key' -o -iname 'state.db*' \\) | grep -q .; then exit 42; fi; "
        "if grep -RIEq 'github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|BEGIN (RSA|OPENSSH|EC|PRIVATE) KEY' \"$stage/repo/hermes\" \"$stage/repo/sandbox\" 2>/dev/null; then exit 43; fi; "
        "revision=$(git -C \"$stage/repo\" rev-parse HEAD); printf '{\"schema_version\":1,\"revision\":\"%s\"}\\n' \"$revision\" > \"$stage/repo/manifest.json\"; "
        "cd \"$stage/repo\"; git add -- manifest.json hermes sandbox; if git diff --cached --quiet; then revision=$(git rev-parse HEAD); printf 'unchanged:%s\\n' \"$revision\"; else git -c user.name='Hermes State Backup' -c user.email='hermes-state@users.noreply.github.com' commit -qm 'chore: sync sanitized Hermes state'; git push -q origin HEAD; revision=$(git rev-parse HEAD); printf 'pushed:%s\\n' \"$revision\"; fi"
    )
