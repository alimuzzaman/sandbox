"""Secret-free capability and proof reports for managed Credential Vault use."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Mapping

from .credential_binding import (
    ALLOWED_AUTH_FORMS, ALLOWED_METHODS, LIFECYCLE_STATES, canonical_host,
    canonical_path, canonical_timestamp,
)


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = frozenset({"pass", "fail", "unknown"})
_SENSITIVE = frozenset({
    "authorization", "body", "credential", "header", "password", "secret",
    "token", "value",
})

CAPABILITY = "outbound_credential_mediation"
RUNTIME = "managed-native"
SUPPORT_TIERS = frozenset({"proven", "implemented_unproven", "blocked", "unavailable"})


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise ValueError(f"capability {label} is invalid")
    return value


def _reason(value: Any, label: str = "reason") -> str:
    if not isinstance(value, str) or not _REASON.fullmatch(value):
        raise ValueError(f"capability {label} is invalid")
    return value


def _status(value: Any) -> str:
    if value not in _STATUSES:
        raise ValueError("capability observation status is invalid")
    return value


def _digest(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"capability {label} is invalid")
    return value


def _safe_mapping(value: Mapping[str, Any], label: str) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise ValueError(f"capability {label} is invalid")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ValueError(f"capability {label} is invalid")
        lowered = key.lower()
        if any(term in lowered for term in _SENSITIVE):
            raise ValueError(f"capability {label} contains sensitive fields")
        if isinstance(item, Mapping):
            item = _safe_mapping(item, label)
        elif isinstance(item, (list, tuple)):
            nested = []
            for child in item:
                if isinstance(child, Mapping):
                    child = _safe_mapping(child, label)
                elif isinstance(child, str):
                    if len(child) > 512 or any(ord(character) < 32 or ord(character) == 127
                                               for character in child):
                        raise ValueError(f"capability {label} is invalid")
                elif not isinstance(child, (bool, int, float)) and child is not None:
                    raise ValueError(f"capability {label} is invalid")
                nested.append(child)
            item = tuple(nested)
        elif isinstance(item, str):
            if len(item) > 512 or any(ord(character) < 32 or ord(character) == 127
                                       for character in item):
                raise ValueError(f"capability {label} is invalid")
        elif not isinstance(item, (bool, int, float)) and item is not None:
            raise ValueError(f"capability {label} is invalid")
        result[key] = item
    return MappingProxyType(result)


@dataclass(frozen=True, repr=False)
class CapabilityPrerequisite:
    name: str
    status: str
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identity(self.name, "prerequisite name"))
        object.__setattr__(self, "status", _status(self.status))
        object.__setattr__(self, "reason_code", _reason(self.reason_code))

    def __repr__(self) -> str:
        return f"CapabilityPrerequisite(name={self.name!r}, status={self.status!r})"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "reason_code": self.reason_code}


@dataclass(frozen=True, repr=False)
class EffectiveObservation:
    name: str
    status: str
    reason_code: str = "ok"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identity(self.name, "effective observation name"))
        object.__setattr__(self, "status", _status(self.status))
        object.__setattr__(self, "reason_code", _reason(self.reason_code))

    def __repr__(self) -> str:
        return f"EffectiveObservation(name={self.name!r}, status={self.status!r})"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "reason_code": self.reason_code}


@dataclass(frozen=True, repr=False)
class BindingState:
    """Status-only projection of a binding; source references are excluded."""

    binding_id: str
    version: int
    scope: Mapping[str, Any]
    state: str
    expires_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _identity(self.binding_id, "binding id"))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("capability binding version is invalid")
        scope = _safe_mapping(self.scope, "binding scope")
        allowed = {"scheme", "host", "port", "method", "path", "auth_form"}
        if set(scope) != allowed:
            raise ValueError("capability binding scope is invalid")
        if scope["scheme"] != "https" or scope["port"] != 443:
            raise ValueError("capability binding scope is invalid")
        if canonical_host(scope["host"]) != scope["host"]:
            raise ValueError("capability binding scope is invalid")
        if scope["method"] not in ALLOWED_METHODS or scope["path"] != canonical_path(scope["path"]):
            raise ValueError("capability binding scope is invalid")
        if scope["auth_form"] not in ALLOWED_AUTH_FORMS:
            raise ValueError("capability binding scope is invalid")
        object.__setattr__(self, "scope", scope)
        if self.state not in LIFECYCLE_STATES:
            raise ValueError("capability binding state is invalid")
        object.__setattr__(self, "expires_at", canonical_timestamp(self.expires_at))

    def __repr__(self) -> str:
        return f"BindingState(binding_id={self.binding_id!r}, version={self.version}, state={self.state!r})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "version": self.version,
            "scope": dict(self.scope),
            "state": self.state,
            "expires_at": self.expires_at,
        }


class CapabilityReportError(ValueError):
    """A bounded refusal to admit an unproven capability."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, repr=False)
class CapabilityReport:
    capability: str = CAPABILITY
    runtime: str = RUNTIME
    platform: str = "ubuntu-24.04"
    support_tier: str = "implemented_unproven"
    adoptable: bool = False
    evidence_id: str | None = None
    prerequisites: tuple[CapabilityPrerequisite, ...] = ()
    effective_isolation: tuple[EffectiveObservation, ...] = ()
    policy_digest: str | None = None
    egress_digest: str | None = None
    broker_digest: str | None = None
    binding_states: tuple[BindingState, ...] = ()
    last_transition_at: str | None = None
    last_transition_reason: str | None = None
    refusal_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.capability != CAPABILITY or self.runtime != RUNTIME:
            raise ValueError("credential capability identity is invalid")
        object.__setattr__(self, "platform", _identity(self.platform, "platform"))
        if self.support_tier not in SUPPORT_TIERS:
            raise ValueError("capability support tier is invalid")
        if not isinstance(self.adoptable, bool):
            raise ValueError("capability adoptable flag is invalid")
        if self.evidence_id is not None:
            object.__setattr__(self, "evidence_id", _identity(self.evidence_id, "evidence id"))
        if self.support_tier == "proven" and not self.evidence_id:
            raise ValueError("proven capability requires evidence identity")

        def normalize(items, cls, label):
            normalized = []
            for item in tuple(items or ()):
                if isinstance(item, cls):
                    normalized.append(item)
                elif isinstance(item, Mapping):
                    normalized.append(cls(**dict(item)))
                else:
                    raise ValueError(f"capability {label} is invalid")
            return tuple(normalized)

        object.__setattr__(self, "prerequisites", normalize(
            self.prerequisites, CapabilityPrerequisite, "prerequisites",
        ))
        object.__setattr__(self, "effective_isolation", normalize(
            self.effective_isolation, EffectiveObservation, "effective observations",
        ))
        object.__setattr__(self, "binding_states", normalize(
            self.binding_states, BindingState, "binding states",
        ))
        for name in ("policy_digest", "egress_digest", "broker_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if self.last_transition_at is not None:
            object.__setattr__(self, "last_transition_at", canonical_timestamp(self.last_transition_at))
        if self.last_transition_reason is not None:
            object.__setattr__(self, "last_transition_reason", _reason(
                self.last_transition_reason, "transition reason",
            ))
        reasons = tuple(_reason(reason, "refusal reason") for reason in tuple(self.refusal_reasons or ()))
        object.__setattr__(self, "refusal_reasons", reasons)

    def __repr__(self) -> str:
        return (
            "CapabilityReport("
            f"capability={self.capability!r}, runtime={self.runtime!r}, "
            f"support_tier={self.support_tier!r}, adoptable={self.adoptable})"
        )

    @property
    def derived_refusals(self) -> tuple[str, ...]:
        reasons = list(self.refusal_reasons)
        if self.support_tier != "proven":
            reasons.append("support_unproven" if self.support_tier == "implemented_unproven"
                           else f"support_{self.support_tier}")
        if not self.adoptable:
            reasons.append("not_adoptable")
        if self.support_tier == "proven" and not self.evidence_id:
            reasons.append("evidence_missing")
        if self.support_tier == "proven":
            if not self.prerequisites:
                reasons.append("prerequisites_missing")
            if not self.effective_isolation:
                reasons.append("effective_isolation_missing")
            for name, value in (
                ("policy_digest", self.policy_digest),
                ("egress_digest", self.egress_digest),
                ("broker_digest", self.broker_digest),
            ):
                if value is None:
                    reasons.append(f"{name}_missing")
        for item in self.prerequisites:
            if item.status != "pass":
                reasons.append(f"prerequisite_{item.status}")
        for item in self.effective_isolation:
            if item.status != "pass":
                reasons.append(f"effective_isolation_{item.status}")
        return tuple(dict.fromkeys(reasons))

    @property
    def admissible(self) -> bool:
        return not self.derived_refusals

    def require_admission(self) -> None:
        reasons = self.derived_refusals
        if reasons:
            code = "capability_unproven" if self.support_tier != "proven" else "capability_blocked"
            raise CapabilityReportError(code, "credential capability is not proven and adoptable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "runtime": self.runtime,
            "platform": self.platform,
            "support_tier": self.support_tier,
            "adoptable": self.adoptable,
            "evidence_id": self.evidence_id,
            "prerequisites": [item.to_dict() for item in self.prerequisites],
            "effective_isolation": [item.to_dict() for item in self.effective_isolation],
            "policy_digest": self.policy_digest,
            "egress_digest": self.egress_digest,
            "broker_digest": self.broker_digest,
            "binding_states": [item.to_dict() for item in self.binding_states],
            "last_transition_at": self.last_transition_at,
            "last_transition_reason": self.last_transition_reason,
            "refusal_reasons": list(self.derived_refusals),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityReport":
        expected = {
            "capability", "runtime", "platform", "support_tier", "adoptable", "evidence_id",
            "prerequisites", "effective_isolation", "policy_digest", "egress_digest",
            "broker_digest", "binding_states", "last_transition_at", "last_transition_reason",
            "refusal_reasons",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise ValueError("capability report document is invalid")
        return cls(**dict(value))


__all__ = [
    "BindingState", "CapabilityPrerequisite", "CapabilityReport", "CapabilityReportError",
    "EffectiveObservation", "CAPABILITY", "RUNTIME", "SUPPORT_TIERS",
]
