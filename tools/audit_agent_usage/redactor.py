"""Pure, bounded redaction helpers for the audit projection boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .schema import MAX_FORMULA_VALUE_LENGTH, MAX_STRING_LENGTH, bounded_text


REDACTED = "redacted"
FORMULA_MARKER = "formula_safe"
FORMULA_CANDIDATE_MARKER = "formula_candidate"
_FORMULA_PREFIXES = ("=", "+", "-", "@")

# Match words rather than only exact keys so nested forms such as
# ``access-token`` and ``privateKey`` are covered too.
_SENSITIVE_KEY_RE = re.compile(
    r"(?:access|refresh|id|client)?[_-]?(?:token|secret|password|passwd|apikey|api[_-]?key|"
    r"authorization|cookie|credential|private[_-]?key|bearer|session[_-]?id)$",
    re.IGNORECASE,
)
_SENSITIVE_PART_RE = re.compile(
    r"(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|"
    r"authorization|cookie|credential|secret|bearer)",
    re.IGNORECASE,
)


def is_sensitive_key(key: Any) -> bool:
    text = bounded_text(key, limit=MAX_STRING_LENGTH)
    if text is None:
        return False
    compact = re.sub(r"[^A-Za-z0-9_-]", "", text)
    return bool(_SENSITIVE_KEY_RE.search(compact) or _SENSITIVE_PART_RE.search(compact))


def is_formula_candidate(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_FORMULA_PREFIXES)


def formula_safe(value: Any) -> str | None:
    """Prefix formula-leading strings for safe later tabular writing."""

    text = bounded_text(value, limit=MAX_FORMULA_VALUE_LENGTH)
    if text is None:
        return None
    if is_formula_candidate(text):
        return ("'" + text)[:MAX_FORMULA_VALUE_LENGTH]
    # A leading apostrophe is already safe; retain it without adding another.
    return text[:MAX_FORMULA_VALUE_LENGTH]


def _bounded_key(key: Any) -> str | None:
    text = bounded_text(key, limit=MAX_STRING_LENGTH)
    if text is None:
        return None
    # Keys are only useful when they can be represented safely in JSON output.
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", text):
        return None
    return text


def redact_nested(value: Any, *, depth: int = 0, max_depth: int = 3) -> Any:
    """Return a bounded JSON-compatible redacted projection.

    Sensitive-looking keys are retained only with the literal ``redacted``
    marker.  Unknown scalars are bounded, and deep or oversized structures are
    collapsed to the same marker.  The function never raises for hostile input.
    """

    if depth > max_depth:
        return REDACTED
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = _bounded_key(raw_key)
            if key is None:
                continue
            if is_sensitive_key(key):
                result[key] = REDACTED
                continue
            result[key] = redact_nested(value[raw_key], depth=depth + 1, max_depth=max_depth)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        if len(items) > 16:
            return REDACTED
        return [redact_nested(item, depth=depth + 1, max_depth=max_depth) for item in items]
    if isinstance(value, str):
        return formula_safe(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return REDACTED


def target_class(value: Any) -> str:
    """Classify a target without retaining the target string itself."""

    text = bounded_text(value, limit=MAX_STRING_LENGTH)
    if text is None:
        return "unknown"
    lowered = text.lower()
    if "remote" in lowered:
        return "remote"
    if "local" in lowered:
        return "local"
    if "synthetic" in lowered or "fixture" in lowered or "page" in lowered:
        return "synthetic"
    if lowered in {"remote", "local", "synthetic"}:
        return lowered
    return "unknown"


__all__ = [
    "FORMULA_CANDIDATE_MARKER",
    "FORMULA_MARKER",
    "REDACTED",
    "formula_safe",
    "is_formula_candidate",
    "is_sensitive_key",
    "redact_nested",
    "target_class",
]
