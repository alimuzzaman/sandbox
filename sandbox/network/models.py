"""Immutable, serializable domain-resolution contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from sandbox.config.domains import normalize_hostname


HOSTNAME_SOURCES = frozenset({"persisted", "machine_override", "project", "default"})
SUFFIX_CLASSES = frozenset({"test", "legacy_private", "mdns_reserved", "public"})
MIGRATION_STATES = frozenset({"none", "required", "confirmed", "failed"})
RESOLVER_MANAGERS = frozenset({
    "resolved", "networkmanager", "macos", "dnsmasq", "herd", "valet",
    "hosts", "external", "unknown",
})
SUPPORT_TIERS = frozenset({
    "adoptable", "conditional", "implemented_unproven", "detect_only",
    "external", "outside_platform", "unavailable",
})
BINDING_KINDS = frozenset({"exact", "zone", "incumbent", "external"})
BINDING_STATES = frozenset({
    "planned", "applied", "healthy", "drifted", "pending_cleanup", "removed",
})
AUTHORITY_STATES = frozenset({
    "stopped", "starting", "healthy", "unhealthy", "foreign_collision",
})
CONSENT_DECISIONS = frozenset({"accepted", "declined"})
RECOVERY_STATES = frozenset({"pending", "drifted", "unavailable", "resolved"})
RESULT_STATES = frozenset({
    "ready", "fallback", "pending_consent", "pending_privilege", "unsupported",
    "incompatible_identity", "foreign_collision", "drifted", "cleanup_incomplete",
    "invalid",
})
# Closed status contract for selected-ingress diagnostics.  Rich adapter facts
# are projected onto these reason/state tuples before crossing the application
# boundary.  ``application_protocol_error`` is deliberately not a public
# reason code: the probe itself is unavailable for application use, while the
# protocol-error state remains useful to callers.  It therefore shares the
# stable ``ingress_probe_unavailable`` code with the no-probe tuple.
DIAGNOSTIC_TUPLES = {
    "fresh_dns_unavailable": frozenset({("unavailable", "not_attempted")}),
    "answer_mismatch": frozenset({("unavailable", "not_attempted")}),
    "ingress_probe_unavailable": frozenset({
        ("unavailable", "not_attempted"),
        ("reachable", "protocol_error"),
    }),
    "ingress_listener_unreachable": frozenset({("unreachable", "not_attempted")}),
    "ingress_connect_timeout": frozenset({("timed_out", "not_attempted")}),
    "application_response_timeout": frozenset({("reachable", "timed_out")}),
    "application_http_unhealthy": frozenset({("reachable", "http_unhealthy")}),
    "ready": frozenset({("reachable", "ready")}),
}
DIAGNOSTIC_FALLBACK = (
    "unavailable", "not_attempted", "ingress_probe_unavailable",
)
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_SECRET_KEY = re.compile(r"(?i)(token|password|passphrase|authorization|cookie|credential|secret)")
_SECRET_TEXT = re.compile(
    r"(?i)(token|password|passphrase|authorization|cookie|credential|secret)\s*[=:]\s*\S+"
)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


def canonical_digest(value: Any) -> str:
    payload = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_TEXT.sub(lambda match: match.group(1) + "=[redacted]", value)
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [redact(item) for item in value]
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _digest(value: object, name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{name} must be a sha256 digest")
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return MappingProxyType(dict(value))


def project_diagnostic(
    ingress: object, application: object, reason: object,
) -> dict[str, dict[str, str]]:
    """Project selected-ingress status onto its closed public shape.

    Adapter implementations may retain richer internal facts, but malformed
    or contradictory observations fail closed and never expose raw endpoint,
    exception, header, or body data to callers.
    """
    # The wire shape is intentionally narrow.  Extra adapter facts are dropped
    # rather than copied, while missing/ill-typed required fields fail closed.
    # This lets a trusted transport retain useful state alongside private
    # endpoint facts without allowing any of those facts across the boundary.
    def component(value: object, key: str) -> object:
        if not isinstance(value, Mapping) or key not in value:
            return None
        return value.get(key)

    ingress_state = component(ingress, "state")
    application_state = component(application, "state")
    reason_code = component(reason, "code")
    valid_pairs = DIAGNOSTIC_TUPLES.get(reason_code) \
        if isinstance(reason_code, str) else None
    if (not isinstance(ingress_state, str)
            or not isinstance(application_state, str)
            or valid_pairs is None
            or (ingress_state, application_state) not in valid_pairs):
        ingress_state, application_state, reason_code = DIAGNOSTIC_FALLBACK
    return {
        "ingress": {"state": ingress_state},
        "application": {"state": application_state},
        "reason": {"code": reason_code},
    }


def _address(value: object) -> str:
    try:
        return str(ipaddress.ip_address(_text(value, "address")))
    except ValueError as exc:
        raise ValueError("address is invalid") from exc


@dataclass(frozen=True)
class HostnameIntent:
    project_root: str
    label: str
    hostname: str
    source: str
    suffix_class: str
    wildcard_required: bool = False
    migration_state: str = "none"

    def __post_init__(self) -> None:
        root = str(Path(_text(self.project_root, "project root")).expanduser().resolve())
        object.__setattr__(self, "project_root", root)
        if not _LABEL.fullmatch(self.label):
            raise ValueError("label is invalid")
        if normalize_hostname(self.hostname) != self.hostname:
            raise ValueError("hostname must be normalized")
        if self.source not in HOSTNAME_SOURCES:
            raise ValueError("hostname source is invalid")
        if self.suffix_class not in SUFFIX_CLASSES:
            raise ValueError("suffix class is invalid")
        if not isinstance(self.wildcard_required, bool):
            raise ValueError("wildcard_required must be boolean")
        if self.migration_state not in MIGRATION_STATES:
            raise ValueError("migration state is invalid")

    @property
    def owner_id(self) -> str:
        return f"{self.project_root}::{self.label}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root, "label": self.label,
            "hostname": self.hostname, "source": self.source,
            "suffix_class": self.suffix_class,
            "wildcard_required": self.wildcard_required,
            "migration_state": self.migration_state,
        }


@dataclass(frozen=True)
class ResolverObservation:
    owner_id: str
    manager: str
    mode: str
    support_tier: str
    extension: Mapping[str, Any]
    current_answers: tuple[str, ...]
    fingerprint: str
    evidence: tuple[str, ...] = ()
    # Digest of the OWNERSHIP facts only. `fingerprint` also covers current
    # answers and raw evidence text, both of which move on their own: a DNS TTL
    # expiring, an unrelated container adding a veth interface to
    # `resolvectl status`, or this feature's own successful apply. Comparing the
    # full fingerprint to decide "did the resolver owner change" therefore
    # reports a change on an untouched host and breaks repeat-safety.
    ownership_fingerprint: str = ""

    def __post_init__(self) -> None:
        _text(self.owner_id, "resolver owner")
        if self.manager not in RESOLVER_MANAGERS:
            raise ValueError("resolver manager is invalid")
        _text(self.mode, "resolver mode")
        if self.support_tier not in SUPPORT_TIERS:
            raise ValueError("support tier is invalid")
        object.__setattr__(self, "extension", _mapping(self.extension, "extension"))
        object.__setattr__(self, "current_answers", tuple(_address(item) for item in self.current_answers))
        object.__setattr__(self, "fingerprint", _digest(self.fingerprint, "fingerprint"))
        if not self.ownership_fingerprint:
            object.__setattr__(self, "ownership_fingerprint", canonical_digest({
                "owner_id": self.owner_id, "manager": self.manager,
                "mode": self.mode, "support_tier": self.support_tier,
                "extension": dict(self.extension),
            }))
        object.__setattr__(self, "ownership_fingerprint",
                           _digest(self.ownership_fingerprint, "ownership fingerprint"))
        if not all(isinstance(item, str) for item in self.evidence):
            raise ValueError("resolver evidence must contain strings")

    @classmethod
    def create(cls, *, owner_id: str, manager: str, mode: str, support_tier: str,
               extension: Mapping[str, Any] | None = None,
               current_answers: tuple[str, ...] = (), evidence: tuple[str, ...] = ()):
        basis = {
            "owner_id": owner_id, "manager": manager, "mode": mode,
            "support_tier": support_tier, "extension": dict(extension or {}),
            "current_answers": list(current_answers), "evidence": list(evidence),
        }
        return cls(
            owner_id=owner_id, manager=manager, mode=mode,
            support_tier=support_tier, extension=dict(extension or {}),
            current_answers=tuple(current_answers),
            fingerprint=canonical_digest(basis), evidence=tuple(evidence),
            ownership_fingerprint=canonical_digest({
                "owner_id": owner_id, "manager": manager, "mode": mode,
                "support_tier": support_tier, "extension": dict(extension or {}),
            }),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id, "manager": self.manager, "mode": self.mode,
            "support_tier": self.support_tier, "extension": dict(self.extension),
            "current_answers": list(self.current_answers), "fingerprint": self.fingerprint,
            "ownership_fingerprint": self.ownership_fingerprint,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ResolutionBinding:
    binding_id: str
    kind: str
    name: str
    target: str
    adapter_id: str
    owners: tuple[str, ...]
    desired: Mapping[str, Any]
    last_applied: Mapping[str, Any] | None = None
    observed: Mapping[str, Any] | None = None
    lifecycle: str = "planned"

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _digest(self.binding_id, "binding id"))
        if self.kind not in BINDING_KINDS:
            raise ValueError("binding kind is invalid")
        normalized = normalize_hostname(self.name.lstrip("*."))
        if self.kind == "zone" and self.name.startswith("*."):
            normalized = "*." + normalized
        if normalized != self.name:
            raise ValueError("binding name must be normalized")
        object.__setattr__(self, "target", _address(self.target))
        _text(self.adapter_id, "adapter id")
        owners = tuple(sorted(set(self.owners)))
        if not owners or not all(isinstance(item, str) and item for item in owners):
            raise ValueError("binding owners are invalid")
        object.__setattr__(self, "owners", owners)
        object.__setattr__(self, "desired", _mapping(self.desired, "desired state"))
        if self.last_applied is not None:
            object.__setattr__(self, "last_applied", _mapping(self.last_applied, "last applied state"))
        if self.observed is not None:
            object.__setattr__(self, "observed", _mapping(self.observed, "observed state"))
        if self.lifecycle not in BINDING_STATES:
            raise ValueError("binding lifecycle is invalid")

    @classmethod
    def create(cls, *, kind: str, name: str, target: str, adapter_id: str,
               owners: tuple[str, ...], desired: Mapping[str, Any]):
        identity = {"kind": kind, "name": name, "target": target,
                    "adapter_id": adapter_id}
        return cls(canonical_digest(identity), kind, name, target, adapter_id,
                   owners, desired)

    def with_owners(self, owners: tuple[str, ...]):
        return replace(self, owners=owners)

    @property
    def last_applied_digest(self) -> str | None:
        return canonical_digest(self.last_applied) if self.last_applied is not None else None

    @property
    def observed_digest(self) -> str | None:
        return canonical_digest(self.observed) if self.observed is not None else None

    def with_applied(self, value: Mapping[str, Any]):
        return replace(self, last_applied=value, observed=value, lifecycle="applied")

    def with_observed(self, value: Mapping[str, Any]):
        state = "healthy" if self.last_applied is not None and (
            canonical_digest(value) == self.last_applied_digest
        ) else "drifted"
        return replace(self, observed=value, lifecycle=state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id, "kind": self.kind, "name": self.name,
            "target": self.target, "adapter_id": self.adapter_id,
            "owners": list(self.owners), "desired": dict(self.desired),
            "last_applied": dict(self.last_applied) if self.last_applied is not None else None,
            "observed": dict(self.observed) if self.observed is not None else None,
            "lifecycle": self.lifecycle,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        return cls(
            value.get("binding_id"), value.get("kind"), value.get("name"),
            value.get("target"), value.get("adapter_id"),
            tuple(value.get("owners") or ()), value.get("desired") or {},
            value.get("last_applied"), value.get("observed"),
            value.get("lifecycle", "planned"),
        )


@dataclass(frozen=True)
class AnsweringAuthority:
    authority_id: str
    address: str
    port: int
    binary: str
    config_path: str
    bindings: tuple[str, ...]
    pid: int | None
    pid_start: str | None
    health: str
    config_digest: str

    def __post_init__(self) -> None:
        _text(self.authority_id, "authority id")
        address = ipaddress.ip_address(_address(self.address))
        if not address.is_loopback:
            raise ValueError("authority address must be loopback")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
            raise ValueError("authority port must be unprivileged")
        _text(self.binary, "authority binary")
        _text(self.config_path, "authority config path")
        if self.pid is not None and (isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0):
            raise ValueError("authority pid is invalid")
        if self.health not in AUTHORITY_STATES:
            raise ValueError("authority health is invalid")
        object.__setattr__(self, "config_digest", _digest(self.config_digest, "config digest"))


@dataclass(frozen=True)
class ConsentRecord:
    owner_id: str
    decision: str
    decided_at: str
    policy_version: int
    reconsidered_at: str | None = None

    def __post_init__(self) -> None:
        _text(self.owner_id, "consent owner")
        if self.decision not in CONSENT_DECISIONS:
            raise ValueError("consent decision is invalid")
        _text(self.decided_at, "consent timestamp")
        if isinstance(self.policy_version, bool) or not isinstance(self.policy_version, int) or self.policy_version < 1:
            raise ValueError("consent policy version is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id, "decision": self.decision,
            "decided_at": self.decided_at, "policy_version": self.policy_version,
            "reconsidered_at": self.reconsidered_at,
        }


@dataclass(frozen=True)
class CleanupRecovery:
    binding_id: str
    adapter_id: str
    expected_digest: str
    observed_digest: str | None
    reason_code: str
    retry_after: str | None
    status: str

    def __post_init__(self) -> None:
        _text(self.binding_id, "recovery binding")
        _text(self.adapter_id, "recovery adapter")
        object.__setattr__(self, "expected_digest", _digest(self.expected_digest, "expected digest"))
        object.__setattr__(self, "observed_digest", _digest(
            self.observed_digest, "observed digest", optional=True,
        ))
        _text(self.reason_code, "recovery reason")
        if self.status not in RECOVERY_STATES:
            raise ValueError("recovery status is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id, "adapter_id": self.adapter_id,
            "expected_digest": self.expected_digest,
            "observed_digest": self.observed_digest, "reason_code": self.reason_code,
            "retry_after": self.retry_after, "status": self.status,
        }


@dataclass(frozen=True)
class DomainPlan:
    plan_id: str
    observation_fingerprint: str
    hostname: str
    adapter_id: str
    expected_addresses: tuple[str, ...]
    mutations: tuple[Mapping[str, Any], ...]
    rollback: tuple[Mapping[str, Any], ...]
    consent_required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _digest(self.plan_id, "plan id"))
        object.__setattr__(self, "observation_fingerprint", _digest(
            self.observation_fingerprint, "observation fingerprint",
        ))
        if normalize_hostname(self.hostname) != self.hostname:
            raise ValueError("plan hostname must be normalized")
        _text(self.adapter_id, "plan adapter")
        object.__setattr__(self, "expected_addresses", tuple(
            _address(item) for item in self.expected_addresses
        ))
        object.__setattr__(self, "mutations", tuple(
            _mapping(item, "plan mutation") for item in self.mutations
        ))
        object.__setattr__(self, "rollback", tuple(
            _mapping(item, "rollback operation") for item in self.rollback
        ))


@dataclass(frozen=True)
class DomainResult:
    ok: bool
    state: str
    hostname: str | None
    hostname_source: str
    strategy: str | None
    strategy_source: str
    resolver: Mapping[str, Any]
    actual_answers: tuple[str, ...]
    expected_addresses: tuple[str, ...]
    ownership: str
    health: str
    fallback_url: str
    reason: Mapping[str, Any]
    mutated: bool
    # Optional read-only selected-ingress status classes.  They are additive so
    # existing plan/apply/cleanup positional construction remains compatible.
    ingress: Mapping[str, Any] | None = None
    application: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool) or not isinstance(self.mutated, bool):
            raise ValueError("result flags must be boolean")
        if self.state not in RESULT_STATES:
            raise ValueError("domain result state is invalid")
        if self.hostname is not None and normalize_hostname(self.hostname) != self.hostname:
            raise ValueError("result hostname must be normalized")
        object.__setattr__(self, "resolver", _mapping(self.resolver, "resolver result"))
        object.__setattr__(self, "reason", _mapping(self.reason, "result reason"))
        object.__setattr__(self, "actual_answers", tuple(_address(item) for item in self.actual_answers))
        object.__setattr__(self, "expected_addresses", tuple(_address(item) for item in self.expected_addresses))
        if self.ingress is not None or self.application is not None:
            # ``reason`` is a legacy result object and normally carries a
            # human-readable message.  A selected-ingress diagnostic is a
            # separate closed envelope, so only its stable code is allowed to
            # cross this boundary; messages and injected keys are discarded.
            projected = project_diagnostic(
                self.ingress, self.application,
                {"code": self.reason.get("code")},
            )
            object.__setattr__(self, "reason", _mapping(
                {"code": projected["reason"]["code"]}, "diagnostic reason",
            ))
            object.__setattr__(self, "ingress", _mapping(projected["ingress"], "ingress result"))
            object.__setattr__(self, "application", _mapping(projected["application"], "application result"))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.ok, "state": self.state, "hostname": self.hostname,
            "hostname_source": self.hostname_source, "strategy": self.strategy,
            "strategy_source": self.strategy_source, "resolver": dict(self.resolver),
            "actual_answers": list(self.actual_answers),
            "expected_addresses": list(self.expected_addresses),
            "ownership": self.ownership, "health": self.health,
            "fallback_url": self.fallback_url, "reason": dict(self.reason),
            "mutated": self.mutated,
        }
        if self.ingress is not None:
            payload["ingress"] = dict(self.ingress)
        if self.application is not None:
            payload["application"] = dict(self.application)
        return redact(payload)
