"""Git provenance and explicitly selected unpublished-state capture."""
from __future__ import annotations

from pathlib import Path
import os
import re
import stat
import tempfile
from typing import Protocol, Sequence

from .errors import RecoveryError


class GitRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            env=None, timeout: float | None = None): ...


_SENSITIVE_NAMES = (".env", "id_rsa", "id_ed25519", ".pem", ".key", "credentials")
_URL_USERINFO = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")


def _redact_remote_url(value: str) -> str:
    """Keep repository identity while removing URL credential and query material."""
    value = _URL_USERINFO.sub(r"\g<scheme>", value)
    if "://" in value:
        value = value.split("?", 1)[0].split("#", 1)[0]
    return value


def _validate_git_token(value: str, field: str, *, allow_dash: bool = False) -> str:
    if (not isinstance(value, str) or not value or
            (not allow_dash and value.startswith("-")) or
            any(char in value for char in "\r\n\0")):
        raise RecoveryError(f"Git {field} is invalid", f"invalid_git_{field}")
    return value


def _validate_destination(target: Path) -> None:
    if target.is_symlink() or (target.exists() and not stat.S_ISREG(target.lstat().st_mode)):
        raise RecoveryError("Git destination is not a regular file", "invalid_git_destination")


def _temporary_destination(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".pending", dir=target.parent)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    return Path(name)


class GitCapture:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def _text(self, argv: Sequence[str], root: str | Path) -> str:
        result = self.runner.run(argv, cwd=str(root), timeout=30)
        if result.returncode != 0:
            raise RecoveryError("git provenance command failed", "git_command_failed")
        # Porcelain status deliberately uses leading spaces as a status column.
        return result.stdout.rstrip()

    def provenance(self, root: str | Path, remote: str = "origin") -> dict:
        root = Path(root).resolve()
        remote = _validate_git_token(remote, "remote")
        revision = self._text(("git", "rev-parse", "HEAD"), root)
        remote_url = _redact_remote_url(self._text(("git", "remote", "get-url", remote), root))
        status = self._text(("git", "status", "--porcelain=v1", "--untracked-files=all"), root)
        dirty = tuple(line for line in status.splitlines() if line)
        ignored_sensitive = tuple(line for line in dirty if any(name in line.lower() for name in _SENSITIVE_NAMES))
        included_dirty = tuple(line for line in dirty if line not in ignored_sensitive)
        return {"root": str(root), "revision": revision, "remote": remote_url,
                "dirty": included_dirty, "ignored_sensitive": ignored_sensitive}

    def create_bundle(self, root: str | Path, destination: str | Path, revision: str) -> Path:
        target = Path(destination)
        _validate_git_token(revision, "revision")
        if target.name.startswith("-") or any(char in str(target) for char in "\r\n\0"):
            raise RecoveryError("Git bundle destination is invalid", "invalid_git_destination")
        _validate_destination(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary_destination(target)
        try:
            result = self.runner.run(("git", "bundle", "create", str(temporary), revision), cwd=str(root), timeout=300)
            if result.returncode != 0:
                raise RecoveryError("git bundle creation failed", "git_bundle_failed")
            check = self.runner.run(("git", "bundle", "verify", str(temporary)), cwd=str(root), timeout=60)
            if check.returncode != 0 or not temporary.is_file() or not temporary.stat().st_size:
                raise RecoveryError("git bundle verification failed", "git_bundle_invalid")
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def create_patch(self, root: str | Path, destination: str | Path, paths: tuple[str, ...]) -> Path:
        if (not paths or any(not path or Path(path).is_absolute() or ".." in Path(path).parts or
                             any(ord(char) < 32 or ord(char) == 127 for char in path) for path in paths)):
            raise RecoveryError("Git patch paths are invalid", "invalid_git_patch")
        if any(any(name in path.lower() for name in _SENSITIVE_NAMES) for path in paths):
            raise RecoveryError("sensitive files cannot be added to a Git patch", "sensitive_git_patch")
        if any(char in str(destination) for char in "\r\n\0"):
            raise RecoveryError("Git patch destination is invalid", "invalid_git_destination")
        target = Path(destination)
        _validate_destination(target)
        result = self.runner.run(("git", "diff", "--binary", "--", *paths), cwd=str(root), timeout=60)
        if result.returncode != 0 or not isinstance(result.stdout, str) or not result.stdout:
            raise RecoveryError("Git patch generation failed", "git_patch_failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary_destination(target)
        try:
            temporary.write_text(result.stdout)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target
