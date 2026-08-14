"""Shared fail-closed redaction for public and durable Sandbox output.

Detection is syntactic plus exact-match. Arbitrarily transformed or encoded
output remains outside the guarantee and must not be described as safe.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REDACTED = "[REDACTED]"
REDACTION_FAILED = "[REDACTION_FAILED]"


class RedactionError(RuntimeError):
    """Stable internal failure with no source text attached."""


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SENSITIVE_NAME = re.compile(
    r"(?i)(?:authorization|cookie|credential|password|passphrase|secret|token|"
    r"api[_-]?key|private[_-]?key|basic[_-]?auth|access[_-]?key(?:[_-]?id)?)s?$"
)
_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?P<name>[A-Za-z0-9_-]*(?:authorization|cookie|credential|password|passphrase|"
    r"secret|token|api[_-]?key|private[_-]?key|basic[_-]?auth|"
    r"access[_-]?key(?:[_-]?id)?)[A-Za-z0-9_-]*)"
    r"(?P<separator>\s*[=:]\s*)(?:bearer\s+|basic\s+)?[^\s,;&\]\[}\{]+"
)
_AUTH_HEADER = re.compile(
    r"(?i)\b(?P<name>authorization)\s*:\s*(?:bearer|basic)\s+[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{4,}")
_PROVIDER_SECRET = re.compile(
    r"(?i)(?:"
    r"github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{12,}|rk_(?:live|test)_[A-Za-z0-9]{12,}|"
    r"pk_(?:live|test)_[A-Za-z0-9]{12,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,}|"
    r"(?:xoxe\.)?xox[baprs]-[A-Za-z0-9-]{12,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
    r"FwoGZXIvYXdz[A-Za-z0-9+/=]{20,}|"
    r"AIza[0-9A-Za-z_-]{30,}|ya29\.[0-9A-Za-z._-]{12,}|1//[0-9A-Za-z._-]{12,}|"
    r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----|"
    r"BEGIN (?:RSA|OPENSSH|EC|DSA|PGP|PRIVATE) KEY"
    r")"
)
_URL = re.compile(r"(?i)\bhttps?://[^\s<>'\"]+")
_SENSITIVE_QUERY_NAME = re.compile(
    r"(?i)(?:access[_-]?token|auth|authorization|credential|key|password|"
    r"passphrase|secret|signature|sig|token|api[_-]?key)s?$"
)
_SENSITIVE_FLAG = re.compile(
    r"(?i)^--?(?:authorization|cookie|credential|password|passphrase|secret|token|"
    r"api[-_]?key|private[-_]?key|basic[-_]?auth|access[-_]?key(?:[-_]?id)?)$"
)
_PENDING_CREDENTIAL_CONTEXT = re.compile(
    rb"(?i)(?<![A-Za-z0-9])(?:"
    rb"authorization[ \t]*:[ \t]*(?:(?:bearer|basic)[ \t]+)?[^\r\n,;&\]\[}{]*|"
    rb"bearer[ \t]+[^\r\n,;&\]\[}{]*|"
    rb"[A-Za-z0-9_-]*(?:authorization|cookie|credential|password|passphrase|"
    rb"secret|token|api[_-]?key|private[_-]?key|basic[_-]?auth|"
    rb"access[_-]?key(?:[_-]?id)?)[A-Za-z0-9_-]*"
    rb"(?:[ \t]*[=:][ \t]*(?:(?:bearer|basic)[ \t]+)?[^\r\n,;&\]\[}{]*)?"
    rb")[ \t]*\Z"
)


def _redact_url_impl(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return REDACTION_FAILED
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return REDACTION_FAILED
    netloc = f"{host}{port}"
    if parsed.username is not None or parsed.password is not None:
        netloc = f"{REDACTED}@{netloc}"
    query = []
    for name, item in parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False):
        if _SENSITIVE_QUERY_NAME.search(name):
            query.append((name, REDACTED))
        else:
            query.append((name, _redact_text_impl(item, redact_urls=False)))
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query), ""))


def redact_url(value: object) -> str:
    """Normalize one URL, returning a marker if it cannot be handled safely."""
    try:
        if not isinstance(value, str):
            return REDACTION_FAILED
        return _redact_url_impl(value)
    except Exception:
        return REDACTION_FAILED


def _redact_text_impl(value: str, *, exact_values: Iterable[str | bytes] = (),
                      redact_urls: bool = True) -> str:
    text = _CONTROL.sub("", value)
    normalized: list[str] = []
    for item in exact_values:
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="replace")
        if not isinstance(item, str):
            raise RedactionError("exact redaction values must be text or bytes")
        if item:
            normalized.append(item)
    for item in sorted(set(normalized), key=len, reverse=True):
        text = text.replace(item, REDACTED)
    if redact_urls:
        text = _URL.sub(lambda match: _redact_url_impl(match.group(0)), text)
    text = _AUTH_HEADER.sub(lambda match: f"{match.group('name')}: {REDACTED}", text)
    text = _ASSIGNMENT.sub(
        lambda match: f"{match.group('name')}{match.group('separator')}{REDACTED}", text,
    )
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    return _PROVIDER_SECRET.sub(REDACTED, text)


def redact_text(value: object, *, exact_values: Iterable[str | bytes] = ()) -> str:
    """Redact text without ever returning raw input after an internal failure."""
    try:
        if not isinstance(value, str):
            return REDACTION_FAILED
        return _redact_text_impl(value, exact_values=exact_values)
    except Exception:
        return REDACTION_FAILED


def _redact_structure_impl(value: Any, *, exact_values: Iterable[str | bytes],
                           depth: int, seen: set[int]) -> Any:
    if depth > 32:
        return REDACTION_FAILED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text_impl(value, exact_values=exact_values)
    if isinstance(value, bytes):
        return _redact_text_impl(value.decode("utf-8", errors="replace"), exact_values=exact_values)
    identity = id(value)
    if identity in seen:
        return REDACTION_FAILED
    if isinstance(value, BaseException):
        seen.add(identity)
        try:
            result: dict[str, Any] = {
                "type": type(value).__name__,
                "message": _redact_text_impl(str(value), exact_values=exact_values),
            }
            chained = value.__cause__ or (None if value.__suppress_context__ else value.__context__)
            if chained is not None:
                result["cause"] = _redact_structure_impl(
                    chained, exact_values=exact_values, depth=depth + 1, seen=seen,
                )
            return result
        finally:
            seen.remove(identity)
    if isinstance(value, Mapping):
        seen.add(identity)
        try:
            output = {}
            for key, item in value.items():
                safe_key = _redact_text_impl(str(key), exact_values=exact_values)
                output[safe_key] = (
                    REDACTED if _SENSITIVE_NAME.search(str(key)) else
                    _redact_structure_impl(item, exact_values=exact_values, depth=depth + 1, seen=seen)
                )
            return output
        finally:
            seen.remove(identity)
    if isinstance(value, (list, tuple, set, frozenset)):
        seen.add(identity)
        try:
            items = [
                _redact_structure_impl(item, exact_values=exact_values, depth=depth + 1, seen=seen)
                for item in value
            ]
            return tuple(items) if isinstance(value, tuple) else items
        finally:
            seen.remove(identity)
    return REDACTION_FAILED


def redact_structure(value: Any, *, exact_values: Iterable[str | bytes] = ()) -> Any:
    """Recursively sanitize JSON-like values and bounded exception chains."""
    try:
        return _redact_structure_impl(value, exact_values=tuple(exact_values), depth=0, seen=set())
    except Exception:
        return REDACTION_FAILED


def argv_contains_credentials(argv: object) -> bool:
    """Return true when argv would persist recognizable credential material."""
    try:
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
            return True
        items = list(argv)
        if any(not isinstance(item, str) for item in items):
            return True
        for index, item in enumerate(items):
            if _text_contains_credentials(item):
                return True
            if _SENSITIVE_FLAG.fullmatch(item) and index + 1 < len(items):
                return True
        return False
    except Exception:
        return True


def _text_contains_credentials(value: str) -> bool:
    """Classify credential syntax without treating URL normalization as removal."""
    if any(pattern.search(value) for pattern in (
        _AUTH_HEADER, _ASSIGNMENT, _BEARER, _PROVIDER_SECRET,
    )):
        return True
    for match in _URL.finditer(value):
        parsed = urlsplit(match.group(0))
        if parsed.username is not None or parsed.password is not None:
            return True
        if any(_SENSITIVE_QUERY_NAME.search(name) for name, _item in parse_qsl(
                parsed.query, keep_blank_values=True, strict_parsing=False)):
            return True
    return False


def require_safe_argv(argv: object) -> None:
    if argv_contains_credentials(argv):
        raise ValueError("command arguments contain credential-like material and cannot be persisted")


class StreamingRedactor:
    """Token-buffered redaction shared by child and durable output paths.

    The current lexical token and every suffix that could complete an exact
    secret are withheld across chunks. An overlong token is replaced rather
    than partially emitted, keeping malformed output fail-closed.
    """

    max_pending_bytes = 65_536

    def __init__(self, secrets: Iterable[bytes | str] = ()) -> None:
        normalized = []
        for value in secrets:
            raw = value.encode() if isinstance(value, str) else value
            if not isinstance(raw, bytes) or not raw:
                raise RedactionError("redaction secrets must be non-empty bytes")
            normalized.append(raw.decode("utf-8", errors="replace"))
        self._exact_values = tuple(normalized)
        self._pending = bytearray()
        self._discarding = False

    def feed(self, chunk: bytes, *, final: bool = False) -> bytes:
        if not isinstance(chunk, bytes):
            raise RedactionError("output chunk must be bytes")
        if self._discarding:
            if final:
                self._discarding = False
                return b""
            boundary = next(
                (index for index, item in enumerate(chunk) if chr(item).isspace()), None,
            )
            if boundary is None:
                return b""
            self._discarding = False
            chunk = chunk[boundary + 1:]
        data = bytes(self._pending) + chunk
        self._pending.clear()
        if final:
            return redact_text(
                data.decode("utf-8", errors="replace"), exact_values=self._exact_values,
            ).encode()
        # Retain the final non-whitespace token because it may be the prefix of
        # a provider token, assignment, or credential-bearing URL. Also retain
        # a complete credential context through its trailing horizontal
        # whitespace and partial value. Otherwise a split immediately after
        # ``Authorization: Bearer `` or ``token = `` could emit the context
        # before the next chunk makes the value recognizable.
        boundary = max((index for index, item in enumerate(data) if chr(item).isspace()), default=-1)
        keep_from = boundary + 1
        context = _PENDING_CREDENTIAL_CONTEXT.search(data)
        if context is not None:
            keep_from = min(keep_from, context.start())
        for secret in self._exact_values:
            raw = secret.encode()
            for size in range(1, min(len(raw) - 1, len(data)) + 1):
                if data.endswith(raw[:size]):
                    keep_from = min(keep_from, len(data) - size)
        complete, pending = data[:keep_from], data[keep_from:]
        if len(pending) > self.max_pending_bytes:
            self._discarding = True
            return redact_text(
                complete.decode("utf-8", errors="replace"), exact_values=self._exact_values,
            ).encode() + REDACTION_FAILED.encode()
        self._pending.extend(pending)
        return redact_text(
            complete.decode("utf-8", errors="replace"), exact_values=self._exact_values,
        ).encode()

    def finish(self) -> bytes:
        return self.feed(b"", final=True)


__all__ = [
    "REDACTED", "REDACTION_FAILED", "RedactionError", "StreamingRedactor",
    "argv_contains_credentials", "redact_structure", "redact_text", "redact_url",
    "require_safe_argv",
]
