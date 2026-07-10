"""Personal shell-secret file support for permanent Sandbox hosting."""
from __future__ import annotations

import os
import re
import secrets
import shlex
from pathlib import Path


DEFAULT_SECRET_FILE = Path.home() / ".zshrc.secrets"
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class SecretError(ValueError):
    pass


def secret_file() -> Path:
    """Return the user-selected file without resolving or exposing its values."""
    return Path(os.environ.get("SANDBOX_SECRETS_FILE", DEFAULT_SECRET_FILE)).expanduser()


def read_secret_file(path: Path | None = None) -> dict[str, str]:
    """Read simple shell assignments; commands and expansions are rejected."""
    path = path or secret_file()
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(raw)
        if not match:
            raise SecretError(f"unsupported syntax in {path} line {number}")
        key, encoded = match.groups()
        quoted_literal = encoded.strip().startswith("'") and encoded.strip().endswith("'")
        if "`" in encoded or "$(" in encoded or ("$" in encoded and not quoted_literal):
            raise SecretError(f"shell expansion is not allowed in {path} line {number}")
        try:
            words = shlex.split(encoded, posix=True)
        except ValueError as exc:
            raise SecretError(f"invalid quoted value in {path} line {number}") from exc
        if len(words) > 1:
            raise SecretError(f"unquoted whitespace in {path} line {number}")
        values[key] = words[0] if words else ""
    return values


def resolve_secret(key: str, path: Path | None = None) -> str | None:
    """Environment wins for CI; the personal file supports non-login CLI runs."""
    value = os.environ.get(key)
    if value:
        return value
    return read_secret_file(path).get(key) or None


def write_secret(key: str, value: str, path: Path | None = None) -> None:
    """Set one exported key atomically without returning its value."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
        raise SecretError("invalid secret key")
    path = path or secret_file()
    values = read_secret_file(path)
    values[key] = value
    lines = ["# Personal secrets. Keep this file out of Git."]
    lines.extend(f"export {name}={shlex.quote(values[name])}" for name in sorted(values))
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text("\n".join(lines) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def generate_secret() -> str:
    return secrets.token_urlsafe(36)


def migrate_zshrc(path: Path | None = None, zshrc: Path | None = None) -> list[str]:
    """Move known credential exports into the sourced personal secret file."""
    path = path or secret_file()
    zshrc = zshrc or Path.home() / ".zshrc"
    names = {
        "TEMPLATELY_API_KEY", "TEMPLATELY_API_KEY_DEV", "TEMPLATELY_API_KEY_FREE",
        "TEMPLATELY_API_KEY_FREE_DEV", "TEMPLATELY_API_KEY_UNVERIFIED_DEV",
        "WORKOS_CLIENT_ID", "WORKOS_API_KEY", "WORKOS_REDIRECT_URI", "OPENAI_API_KEY",
    }
    source = f'[[ -r "$HOME/{path.name}" ]] && source "$HOME/{path.name}"'
    existing = read_secret_file(path)
    kept: list[str] = []
    moved: list[str] = []
    for raw in zshrc.read_text().splitlines():
        match = _ASSIGNMENT.fullmatch(raw)
        if match and match.group(1) in names:
            key = match.group(1)
            encoded = match.group(2)
            try:
                words = shlex.split(encoded, posix=True)
            except ValueError as exc:
                raise SecretError(f"cannot migrate malformed {key}") from exc
            existing[key] = words[0] if words else ""
            moved.append(key)
            continue
        kept.append(raw)
    if source not in kept:
        kept.append("")
        kept.append("# Personal API and deployment secrets (chmod 0600).")
        kept.append(source)
    if moved:
        lines = ["# Personal secrets. Keep this file out of Git."]
        lines.extend(f"export {name}={shlex.quote(existing[name])}" for name in sorted(existing))
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
        zshrc.write_text("\n".join(kept) + "\n")
    return moved
