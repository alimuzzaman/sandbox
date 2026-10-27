"""Names-first, continuity-checked views of registered dotenv sources.

Every read goes through ``SourceRegistry.read_with_stat``; this module never
opens a source path itself. v1 ownership supports registered file sources in
dotenv format only; every other kind or format is ``source_unsupported``.
"""

from __future__ import annotations

from collections.abc import Sequence
import re
import time

from sandbox.secrets.adapter_manifest import source_kind
from sandbox.secrets.models import SecretBrokerError
from sandbox.secrets.parser import SecretParseError, parse_document

from .continuity import capture
from .models import SourceContinuity


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _require_dotenv(registry, alias: str) -> None:
    try:
        policy = registry.policy(alias)
    except SecretBrokerError as exc:
        if exc.code != "source_unknown":
            raise
        raise SecretBrokerError(
            "source_unregistered", "secret source alias is not registered",
            remedy="register the source under secrets.sources before mapping it",
        ) from None
    kind = source_kind(policy)
    if kind != "file" or getattr(policy, "format", None) != "dotenv":
        raise SecretBrokerError(
            "source_unsupported",
            "secret ownership supports registered dotenv file sources only",
        )


def _snapshot(registry, alias: str):
    _require_dotenv(registry, alias)
    safe, info = registry.read_with_stat(alias)
    try:
        document = parse_document(safe.content)
    except SecretParseError as exc:
        code = "duplicate_key" if exc.code == "duplicate_key" else "syntax_unsupported"
        raise SecretBrokerError(
            code, "registered secret source could not be parsed safely",
        ) from None
    continuity = capture(alias, info, document.entries.keys(), now_ns=time.time_ns())
    return document, continuity


def read_names(registry, alias: str) -> tuple[SourceContinuity, tuple[str, ...]]:
    """Return the continuity snapshot and sorted entry names; no value leaves."""
    document, continuity = _snapshot(registry, alias)
    return continuity, tuple(sorted(document.entries))


def read_values(registry, alias: str, keys: Sequence[str],
                expected: SourceContinuity) -> dict[str, bytearray]:
    """Return selected values as ``bytearray`` only if the source is unchanged.

    Keys and the expected snapshot are validated before any value is touched.
    The caller owns the returned buffers and must wipe them after use.
    """
    if (isinstance(keys, (str, bytes)) or not keys
            or any(not isinstance(key, str) or not _ENV_NAME.fullmatch(key) for key in keys)
            or len(set(keys)) != len(keys)):
        raise SecretBrokerError("selection_invalid", "secret ownership key selection is invalid")
    if not isinstance(expected, SourceContinuity) or expected.alias != alias:
        raise SecretBrokerError("source_changed", "registered secret source changed since approval")
    if not expected.reliable:
        raise SecretBrokerError(
            "continuity_unreliable", "registered secret source continuity is unreliable",
        )
    document, current = _snapshot(registry, alias)
    if not current.reliable:
        raise SecretBrokerError(
            "continuity_unreliable", "registered secret source continuity is unreliable",
        )
    if not current.same_as(expected):
        raise SecretBrokerError("source_changed", "registered secret source changed since approval")
    if any(key not in document.entries for key in keys):
        raise SecretBrokerError("key_missing", "secret key does not exist")
    values: dict[str, bytearray] = {}
    for key in keys:
        record = document.entries[key]
        if record.value is None:
            for buffer in values.values():
                buffer[:] = b"\0" * len(buffer)
            raise SecretBrokerError("value_unavailable", "secret value is not available for this format")
        values[key] = bytearray(record.value.encode("utf-8"))
    return values


__all__ = ["read_names", "read_values"]
