"""Source eligibility and generation-fatal credential policy."""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Iterable


class CredentialDetected(RuntimeError):
    """A path or value looked credential-like; details are intentionally absent."""

    code = "credential_detected"

    def __init__(self, count: int = 1) -> None:
        self.count = max(1, int(count))
        super().__init__("credential-like input was detected")


_SENSITIVE_BASENAME = re.compile(
    r"(?i)^(?:\.env(?:\.(?!example$|sample$|template$).+)?|"
    r"credentials?(?:\.[^.]+)?|auth(?:\.[^.]+)?|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|"
    r"[^/]*(?:private[_-]?key|access[_-]?token|api[_-]?key)[^/]*)$"
)
_SENSITIVE_SUFFIX = re.compile(r"(?i)\.(?:pem|key|p12|pfx|jks|keystore)$")
_SENSITIVE_COMPONENTS = frozenset({".ssh", ".aws", ".gnupg"})
_PROVIDER_SECRET = re.compile(
    rb"(?i)(?:github_pat_[a-z0-9_]{20,}|gh[pousr]_[a-z0-9]{20,}|"
    rb"sk-(?:proj-)?[a-z0-9_-]{20,}|"
    rb"(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
    rb"xox[baprs]-[A-Za-z0-9-]{12,}|"
    rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----|"
    rb"BEGIN (?:RSA|OPENSSH|EC|DSA|PGP|PRIVATE) KEY)"
)
_ASSIGNMENT_SECRET = re.compile(
    rb"(?i)(?:authorization|cookie|credential|password|passphrase|secret|token|"
    rb"api[_-]?key|private[_-]?key|access[_-]?key(?:[_-]?id)?)"
    rb"\s*[:=]\s*(?:bearer\s+|basic\s+)?['\"]?[^\s,'\";}{\]]{8,}"
)

_EXCLUDED_COMPONENTS = frozenset({
    ".git", ".sandbox", ".cache", ".pytest_cache", ".mypy_cache",
    "node_modules", "vendor", "build", "dist", "out", "coverage",
    "runtime", "cache", "caches", "logs", "tmp", "temp", "uploads", "storage",
    "__pycache__", ".venv", "venv",
})
_EXCLUDED_SUFFIXES = frozenset({
    ".db", ".sqlite", ".sqlite3", ".log", ".cache", ".tmp", ".swp", ".pyc",
})


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("source path is unsafe")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("source path is unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source path is unsafe")
    normalized = path.as_posix()
    if normalized != value:
        raise ValueError("source path is not canonical")
    return normalized


class SyncPolicy:
    """Fail-closed credential screen plus ordinary non-source exclusions."""

    def credential_name(self, relative_path: str) -> bool:
        path = PurePosixPath(validate_relative_path(relative_path))
        return (
            any(part.lower() in _SENSITIVE_COMPONENTS for part in path.parts) or
            bool(_SENSITIVE_BASENAME.fullmatch(path.name)) or
            bool(_SENSITIVE_SUFFIX.search(path.name)) or
            path.as_posix().lower() in {"sandbox.local.yml", "sandbox.local.yaml"}
        )

    def credential_content(self, content: bytes) -> bool:
        if not isinstance(content, bytes):
            return True
        return bool(_PROVIDER_SECRET.search(content) or _ASSIGNMENT_SECRET.search(content))

    def screen(self, relative_path: str, content: bytes | None = None) -> None:
        if self.credential_name(relative_path) or (
                content is not None and self.credential_content(content)):
            raise CredentialDetected()

    def ordinary_exclusion(self, relative_path: str, *, is_symlink: bool = False) -> bool:
        path = PurePosixPath(validate_relative_path(relative_path))
        lowered = tuple(part.lower() for part in path.parts)
        if is_symlink or any(part in _EXCLUDED_COMPONENTS for part in lowered):
            return True
        if any(path.name.lower().endswith(suffix) for suffix in _EXCLUDED_SUFFIXES):
            return True
        return len(lowered) >= 2 and lowered[-2:] == ("wp-content", "uploads")

    def screen_all(self, items: Iterable[tuple[str, bytes | None]]) -> None:
        findings = 0
        for relative_path, content in items:
            if self.credential_name(relative_path) or (
                    content is not None and self.credential_content(content)):
                findings += 1
        if findings:
            raise CredentialDetected(findings)


__all__ = ["CredentialDetected", "SyncPolicy", "validate_relative_path"]
