"""Immutable ingress identity, listener, selection, and route contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import ipaddress
import json
from types import MappingProxyType
from typing import Any, Mapping


TIERS = frozenset({
    "sandbox_owned", "adoptable", "conditional", "credential_pending",
    "implemented_unproven", "detect_only", "outside_platform", "unidentified",
})


def digest(value: Any) -> str:
    def plain(item):
        if isinstance(item, Mapping):
            return {str(key): plain(val) for key, val in item.items()}
        if isinstance(item, (tuple, list, set, frozenset)):
            return [plain(val) for val in item]
        return item
    encoded = json.dumps(plain(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class ListenerEndpoint:
    address: str
    port: int
    protocol: str = "tcp"
    dual_stack: bool = False
    socket_id: str | None = None
    process: Mapping[str, Any] | None = None
    service: Mapping[str, Any] | None = None
    owner_confidence: str = "unknown"

    def __post_init__(self):
        address = ipaddress.ip_address(self.address)
        object.__setattr__(self, "address", str(address))
        if isinstance(self.port, bool) or not 1 <= int(self.port) <= 65535:
            raise ValueError("listener port is invalid")
        object.__setattr__(self, "port", int(self.port))
        if self.protocol not in {"tcp", "udp"}:
            raise ValueError("listener protocol is invalid")
        if self.dual_stack and not (address.version == 6 and address.is_unspecified):
            raise ValueError("dual_stack applies only to an IPv6 wildcard")
        if self.owner_confidence not in {"proven", "probable", "unknown"}:
            raise ValueError("listener owner confidence is invalid")
        if self.process is not None:
            object.__setattr__(self, "process", MappingProxyType(dict(self.process)))
        if self.service is not None:
            object.__setattr__(self, "service", MappingProxyType(dict(self.service)))

    @property
    def family(self) -> str:
        return "ipv4" if ipaddress.ip_address(self.address).version == 4 else "ipv6"

    @property
    def wildcard(self) -> bool:
        return ipaddress.ip_address(self.address).is_unspecified

    def overlaps(self, other: "ListenerEndpoint") -> bool:
        if self.port != other.port or self.protocol != other.protocol:
            return False
        left, right = ipaddress.ip_address(self.address), ipaddress.ip_address(other.address)
        if left.version == right.version:
            return left == right or left.is_unspecified or right.is_unspecified
        ipv6 = self if left.version == 6 else other
        ipv4 = other if left.version == 6 else self
        return ipv6.wildcard and ipv6.dual_stack and ipaddress.ip_address(ipv4.address).version == 4

    def to_dict(self) -> dict:
        return {
            "family": self.family, "address": self.address, "port": self.port,
            "protocol": self.protocol, "wildcard": self.wildcard,
            "dual_stack": self.dual_stack, "socket_id": self.socket_id,
            "process": dict(self.process) if self.process else None,
            "service": dict(self.service) if self.service else None,
            "owner_confidence": self.owner_confidence,
        }


@dataclass(frozen=True)
class IngressObservation:
    adapter_id: str
    product: str
    endpoints: tuple[ListenerEndpoint, ...]
    support_tier: str
    capabilities: frozenset[str] = frozenset()
    product_identity: Mapping[str, Any] = field(default_factory=dict)
    control: Mapping[str, Any] | None = None
    fingerprint: str = ""

    def __post_init__(self):
        if self.support_tier not in TIERS:
            raise ValueError("ingress support tier is invalid")
        object.__setattr__(self, "endpoints", tuple(self.endpoints))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "product_identity", MappingProxyType(dict(self.product_identity)))
        basis = {"adapter": self.adapter_id, "product": self.product,
                 "endpoints": [item.to_dict() for item in self.endpoints],
                 "tier": self.support_tier, "capabilities": sorted(self.capabilities),
                 "identity": dict(self.product_identity)}
        expected = digest(basis)
        if self.fingerprint and self.fingerprint != expected:
            raise ValueError("ingress observation fingerprint is invalid")
        object.__setattr__(self, "fingerprint", expected)


@dataclass(frozen=True)
class IngressSelection:
    required_protocols: frozenset[str]
    required_capabilities: frozenset[str]
    adapter_id: str | None
    accepted_addresses: tuple[str, ...]
    reason_code: str
    observation_fingerprint: str | None
    pin: str | None = None
    pin_source: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "required_protocols", frozenset(self.required_protocols))
        object.__setattr__(self, "required_capabilities", frozenset(self.required_capabilities))
        object.__setattr__(self, "accepted_addresses", tuple(
            str(ipaddress.ip_address(item)) for item in self.accepted_addresses
        ))


@dataclass(frozen=True)
class RouteRecord:
    route_id: str
    owner: str
    hostname: str
    backend: Mapping[str, Any]
    adapter_id: str
    protocols: frozenset[str]
    capabilities: frozenset[str]
    desired: Mapping[str, Any]
    last_applied: Mapping[str, Any] | None = None
    observed: Mapping[str, Any] | None = None
    lifecycle: str = "planned"

    @classmethod
    def create(cls, *, owner, hostname, backend, adapter_id, protocols,
               capabilities=(), desired=None):
        identity = digest({"owner": owner, "hostname": hostname, "adapter": adapter_id})
        return cls(identity, owner, hostname, MappingProxyType(dict(backend)), adapter_id,
                   frozenset(protocols), frozenset(capabilities),
                   MappingProxyType(dict(desired or {})))

    def with_applied(self, applied):
        value = MappingProxyType(dict(applied))
        return replace(self, last_applied=value, observed=value, lifecycle="applied")

    def with_observed(self, observed):
        value = MappingProxyType(dict(observed))
        lifecycle = "healthy" if self.last_applied is not None and \
            digest(value) == digest(self.last_applied) else "drifted"
        return replace(self, observed=value, lifecycle=lifecycle)


@dataclass(frozen=True)
class RouteTransaction:
    transaction_id: str
    precondition: str
    candidate_digest: str
    current_valid: bool = False
    candidate_valid: bool = False
    activated: bool = False
    rollback_complete: bool | None = None


@dataclass(frozen=True)
class IncumbentConsent:
    product_identity: str
    decision: str
    policy_version: int
    decided_at: str

    def __post_init__(self):
        if self.decision not in {"accepted", "declined"}:
            raise ValueError("ingress consent decision is invalid")


@dataclass(frozen=True)
class CredentialReference:
    product_identity: str
    key: str

    def __post_init__(self):
        if not self.key or any(word in self.key.lower() for word in ("password=", "token=")):
            raise ValueError("credential reference must name a secret, not contain it")


@dataclass(frozen=True)
class CleanupRecovery:
    route_id: str
    adapter_id: str
    expected_digest: str
    observed_digest: str | None
    reason_code: str
    status: str


@dataclass(frozen=True)
class SupportDeclaration:
    adapter_id: str
    products: tuple[str, ...]
    platforms: tuple[str, ...]
    support_tier: str
    capabilities: frozenset[str]
    evidence_id: str | None = None

    def __post_init__(self):
        if self.support_tier not in TIERS:
            raise ValueError("ingress support tier is invalid")

    @property
    def adoptable(self) -> bool:
        return self.support_tier in {"sandbox_owned", "adoptable"} and bool(self.evidence_id)
