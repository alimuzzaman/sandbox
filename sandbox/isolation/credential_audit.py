"""Append-only, secret-free Credential Vault lifecycle audit."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import threading
from typing import Any


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_OPERATIONS = frozenset({"create", "update", "request", "revoke", "expire", "recover", "cleanup", "restart"})
_DECISIONS = frozenset({"allow", "deny", "complete", "indeterminate"})
_SENSITIVE = frozenset({
    "authorization", "body", "credential", "header", "password", "secret",
    "token", "value", "content", "argv", "environment", "env",
})


def _safe_text(value: Any, pattern: re.Pattern[str], fallback: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(fallback)
    return value


def _safe_nested(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or key.lower() in _SENSITIVE or any(term in key.lower() for term in _SENSITIVE):
                raise ValueError("audit record contains sensitive fields")
            result[key] = _safe_nested(item)
        return result
    if isinstance(value, (list, tuple)):
        return tuple(_safe_nested(item) for item in value)
    if isinstance(value, str):
        if len(value) > 512 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("audit record contains unsafe text")
        return value
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    raise ValueError("audit record contains unsupported data")


class CredentialAuditError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = _safe_text(code, _CODE, "audit code is invalid")
        self.message = message if isinstance(message, str) and 0 < len(message) <= 256 else "credential audit failed"
        super().__init__(self.message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class LifecycleRecord:
    """Validated event fields accepted by an audit sink."""

    __slots__ = ("_value",)

    def __init__(self, value: dict[str, Any]) -> None:
        self._value = dict(value)

    def to_dict(self) -> dict[str, Any]:
        return dict(self._value)

    def __repr__(self) -> str:
        return (
            "LifecycleRecord("
            f"operation={self._value['operation']!r}, decision={self._value['decision']!r}, "
            f"binding_id={self._value['binding_id']!r})"
        )


class CredentialAuditLog:
    """Write validated lifecycle records exactly once through ``sink``."""

    def __init__(self, *, sink=None, clock=None) -> None:
        if sink is not None and not callable(sink):
            raise ValueError("credential audit sink is invalid")
        self.sink = sink or (lambda _record: None)
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        self._lock = threading.Lock()
        self._records: list[LifecycleRecord] = []

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(record.to_dict() for record in self._records)

    def _timestamp(self) -> str:
        value = self.clock()
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise CredentialAuditError("audit_clock_invalid", "credential audit clock is unavailable")
            return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        if not isinstance(value, str) or len(value) > 64:
            raise CredentialAuditError("audit_clock_invalid", "credential audit clock is unavailable")
        return value

    def _record(self, *, operation: str, instance_id: str, binding_id: str, actor: str,
                decision: str, reason_code: str, state: str | None = None,
                policy_digest: str | None = None, egress_digest: str | None = None,
                broker_digest: str | None = None, binding_version: int | None = None,
                correlation_id: str | None = None, outcome: str | None = None) -> LifecycleRecord:
        if operation not in _OPERATIONS or decision not in _DECISIONS:
            raise ValueError("audit operation or decision is invalid")
        record: dict[str, Any] = {
            "at": self._timestamp(),
            "operation": operation,
            "instance_id": _safe_text(instance_id, _IDENTITY, "audit instance identity is invalid"),
            "binding_id": _safe_text(binding_id, _IDENTITY, "audit binding identity is invalid"),
            "actor": _safe_text(actor, _IDENTITY, "audit actor is invalid"),
            "decision": decision,
            "reason_code": _safe_text(reason_code, _CODE, "audit reason code is invalid"),
        }
        if state is not None:
            record["state"] = _safe_text(state, _CODE, "audit state is invalid")
        for name, value in (("policy_digest", policy_digest), ("egress_digest", egress_digest),
                            ("broker_digest", broker_digest)):
            if value is not None and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
                raise ValueError(f"audit {name} is invalid")
            if value is not None:
                record[name] = value
        if binding_version is not None:
            if isinstance(binding_version, bool) or not isinstance(binding_version, int) or binding_version < 1:
                raise ValueError("audit binding version is invalid")
            record["binding_version"] = binding_version
        if correlation_id is not None:
            record["correlation_id"] = _safe_text(correlation_id, _IDENTITY, "audit correlation ID is invalid")
        if outcome is not None:
            record["outcome"] = _safe_text(outcome, _CODE, "audit outcome is invalid")
        return LifecycleRecord(_safe_nested(record))

    def _append(self, record: LifecycleRecord) -> None:
        try:
            self.sink(record.to_dict())
        except Exception:
            raise CredentialAuditError("audit_append_failed", "credential audit append failed") from None
        with self._lock:
            self._records.append(record)

    def record(self, **fields: Any) -> dict[str, Any]:
        record = self._record(**fields)
        self._append(record)
        return {"ok": True, "state": "recorded", "mutated": True}

    def execute(self, *, operation: str, instance_id: str, binding_id: str,
                actor: str, effect, reason_code: str = "admitted", **fields: Any) -> dict[str, Any]:
        """Audit admission, execute once, and never replay after append failure."""

        allowed_fields = {
            "state", "policy_digest", "egress_digest", "broker_digest",
            "binding_version", "correlation_id",
        }
        if any(not isinstance(name, str) or name not in allowed_fields for name in fields):
            return {"ok": False, "state": "blocked", "reason": {"code": "audit_fields_invalid"},
                    "no_replay": True, "mutated": False}

        base = {
            "operation": operation, "instance_id": instance_id,
            "binding_id": binding_id, "actor": actor,
        }
        base.update(fields)
        try:
            self._append(self._record(**base, decision="allow", reason_code=reason_code))
        except CredentialAuditError:
            return {"ok": False, "state": "blocked", "reason": {"code": "audit_unavailable"},
                    "no_replay": True, "mutated": False}
        try:
            outcome = effect()
            if not isinstance(outcome, dict):
                raise ValueError
            effect_ok = bool(outcome.get("ok"))
            mutated = bool(outcome.get("mutated"))
        except Exception:
            effect_ok, mutated = False, True
            outcome = {}
        decision = "complete" if effect_ok else "indeterminate"
        result_state = "complete" if effect_ok else "indeterminate"
        try:
            self._append(self._record(
                **base, decision=decision,
                reason_code="effect_complete" if effect_ok else "effect_unknown",
                outcome=result_state,
            ))
        except CredentialAuditError:
            return {"ok": False, "state": "indeterminate", "reason": {"code": "audit_unavailable"},
                    "no_replay": True, "effect_observed": mutated or effect_ok, "mutated": True}
        return {"ok": effect_ok, "state": result_state,
                "reason": {"code": "complete" if effect_ok else "effect_failed"},
                "no_replay": not effect_ok, "mutated": mutated}

    @staticmethod
    def replay(result: Any) -> dict[str, Any]:
        """Explicitly refuse replay of an effect with uncertain audit state."""

        if isinstance(result, dict) and result.get("state") == "indeterminate":
            return {"ok": False, "state": "indeterminate", "no_replay": True}
        return {"ok": False, "state": "replay_denied", "no_replay": True}


__all__ = ["CredentialAuditError", "CredentialAuditLog", "LifecycleRecord"]
