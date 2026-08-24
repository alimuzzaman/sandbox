"""Versioned, audit-only data contracts for synthetic agent events.

This module deliberately contains only immutable constants and small pure
helpers.  It is not imported by Sandbox runtime code, command registration, or
an MCP adapter.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


FIXTURE_SCHEMA = "audit-fixture-v1"
NORMALIZED_SCHEMA = "audit-normalized-v1"
ACCOUNTING_SCHEMA = "audit-accounting-v1"

SUPPORTED_RECORD_KINDS = frozenset({"tool_event", "assistant_message"})
SOURCE_LABELS = frozenset(
    {
        "CODEX-LOCAL-EXACT-CWD",
        "CLAUDE-SANDBOX",
        "CLAUDE-T3-WORKTREE",
        "T3-SAFE-METADATA",
        "HISTORICAL-CODEX-PATTERN",
        "unknown",
    }
)
TERMINAL_STATES = frozenset({"malformed", "duplicate", "excluded", "emitted"})
TRANSPORT_STATUSES = frozenset({"completed", "partial", "unavailable", "unknown"})
TOOL_CALL_STATUSES = frozenset({"completed", "failed", "partial", "unknown"})
COMMAND_EXIT_STATUSES = frozenset({"success", "failure", "timeout", "unknown"})
TASK_OUTCOMES = frozenset(
    {"completed", "blocked", "unverified", "ambiguous", "unknown"}
)
TIMESTAMP_STATES = frozenset({"present", "missing", "inversion", "invalid"})
RELATION_SIGNALS = frozenset({"none", "parent_candidate", "child_candidate"})
RELATION_STATES = frozenset({"unknown"})

# These limits apply to every string that can survive projection.  Raw input
# strings are never copied into a derived row.
MAX_STRING_LENGTH = 128
MAX_FORMULA_VALUE_LENGTH = 256
MAX_ARGUMENT_KEYS = 16

_SAFE_REF_RE = re.compile(r"^(?:SYN-[A-Z0-9][A-Z0-9-]{0,63}|CODEX-SRC-[0-9a-f]{20,64}|SAFE-SRC-[0-9a-f]{20,64})$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def bounded_text(value: Any, *, limit: int = MAX_STRING_LENGTH) -> str | None:
    """Return a bounded, control-free string or ``None``.

    This is intentionally conservative: non-string values and empty values do
    not get coerced into output.  Newlines and control characters are replaced
    before truncation so a value cannot create extra JSONL records.
    """

    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFC", value)
    normalized = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char for char in normalized
    ).strip()
    if not normalized:
        return None
    return normalized[:limit]


def safe_name(value: Any) -> str:
    """Keep a short identifier or return the stable ``unknown`` marker."""

    text = bounded_text(value, limit=64)
    if text is None or not _SAFE_NAME_RE.fullmatch(text):
        return "unknown"
    return text


def safe_file_name(value: Any) -> str:
    """Project only a harmless basename; never emit an absolute input path."""

    text = bounded_text(value, limit=80)
    if text is None or not _SAFE_FILE_RE.fullmatch(text) or text.startswith("rollout-"):
        return "input.jsonl"
    return text


def safe_source_label(value: Any) -> str:
    text = bounded_text(value, limit=64)
    if text in SOURCE_LABELS and text is not None:
        return text
    return "unknown"


def is_safe_source_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(_SAFE_REF_RE.fullmatch(value))


def safe_source_ref(value: Any) -> str:
    """Return a deterministic, non-reversible source reference.

    Synthetic fixture references are already safe and are retained so the
    expected fixture rows remain readable.  Any other value is hashed in
    memory with the approved audit domain; the raw value never enters output.
    """

    text = bounded_text(value, limit=512)
    if text is not None and is_safe_source_ref(text):
        return text
    digest_input = "sandbox-agent-tool-audit:v1:" + (text or "<missing>")
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:20]
    return "SAFE-SRC-" + digest


def safe_event_key(value: Any) -> str | None:
    """Create an in-memory deduplication key without exposing the raw key."""

    text = bounded_text(value, limit=512)
    if text is None:
        return None
    digest = hashlib.sha256(
        ("sandbox-agent-tool-audit:event:v1:" + text).encode("utf-8")
    ).hexdigest()[:24]
    return digest


def is_uuid_like(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))


__all__ = [
    "ACCOUNTING_SCHEMA",
    "COMMAND_EXIT_STATUSES",
    "FIXTURE_SCHEMA",
    "MAX_ARGUMENT_KEYS",
    "MAX_FORMULA_VALUE_LENGTH",
    "MAX_STRING_LENGTH",
    "NORMALIZED_SCHEMA",
    "RELATION_SIGNALS",
    "RELATION_STATES",
    "SOURCE_LABELS",
    "SUPPORTED_RECORD_KINDS",
    "TASK_OUTCOMES",
    "TERMINAL_STATES",
    "TIMESTAMP_STATES",
    "TOOL_CALL_STATUSES",
    "TRANSPORT_STATUSES",
    "bounded_text",
    "is_safe_source_ref",
    "is_uuid_like",
    "safe_event_key",
    "safe_file_name",
    "safe_name",
    "safe_source_label",
    "safe_source_ref",
]
