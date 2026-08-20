"""Owner-only audit journal for secret-broker intent and outcomes."""

from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SecretBrokerError


_ALLOWED_FIELDS = frozenset({
    "schema_version", "event_id", "correlation_id", "phase", "operation",
    "source", "keys", "surface", "actor", "profile", "decision",
    "reason_code", "at", "revision", "count", "input_channel",
})
_FORBIDDEN_TOKENS = (
    "value", "secret", "preview", "mask", "length", "hash", "excerpt",
    "output", "content", "candidate", "temporary",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SecretAudit:
    def __init__(self, path: str | Path, *, actor: str | None = None) -> None:
        self.path = Path(path)
        self.actor = actor or str(os.geteuid())

    def intent(
        self,
        operation: str,
        source: str,
        keys: list[str],
        *,
        surface: str,
        profile: str | None = None,
        input_channel: str | None = None,
    ) -> str:
        correlation_id = uuid.uuid4().hex
        self._append({
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "correlation_id": correlation_id,
            "phase": "intent",
            "operation": operation,
            "source": source,
            "keys": list(keys),
            "surface": surface,
            "actor": self.actor,
            "profile": profile,
            "decision": "requested",
            "reason_code": None,
            "at": _now(),
            "input_channel": input_channel,
        })
        return correlation_id

    def outcome(
        self,
        correlation_id: str,
        operation: str,
        source: str,
        keys: list[str],
        *,
        surface: str,
        decision: str,
        reason_code: str | None = None,
        profile: str | None = None,
        revision: str | None = None,
        count: int | None = None,
        input_channel: str | None = None,
    ) -> None:
        self._append({
            "schema_version": 1,
            "event_id": uuid.uuid4().hex,
            "correlation_id": correlation_id,
            "phase": "outcome",
            "operation": operation,
            "source": source,
            "keys": list(keys),
            "surface": surface,
            "actor": self.actor,
            "profile": profile,
            "decision": decision,
            "reason_code": reason_code,
            "at": _now(),
            "revision": revision,
            "count": count,
            "input_channel": input_channel,
        })

    def _append(self, event: dict[str, Any]) -> None:
        if set(event) - _ALLOWED_FIELDS:
            raise SecretBrokerError("audit_invalid", "audit event contains unsupported fields")
        if any(
            token in key.lower()
            for key in event
            for token in _FORBIDDEN_TOKENS
        ):
            raise SecretBrokerError("audit_invalid", "audit event contains a forbidden field")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            parent_stat = self.path.parent.stat()
            if parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o077:
                raise SecretBrokerError("audit_unavailable", "secret audit directory is unsafe")
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.path, flags, 0o600)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or info.st_nlink != 1
                ):
                    raise SecretBrokerError("audit_unavailable", "secret audit file is unsafe")
                payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
        except SecretBrokerError:
            raise
        except OSError as exc:
            raise SecretBrokerError(
                "audit_unavailable", "secret audit evidence could not be recorded", retryable=True
            ) from exc
