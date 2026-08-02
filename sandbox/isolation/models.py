"""Immutable, digest-bound managed isolation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
from types import MappingProxyType
from typing import Any, Mapping


_GRANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_GRANT_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)
_FORBIDDEN_IPV4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
EGRESS_GRANT_AUTHORITY = "staged-v1"


def parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("egress expiry is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("egress expiry is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("egress expiry must include a timezone")
    return parsed.astimezone(timezone.utc)


def public_ipv4_network(value: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("egress destination is invalid") from exc
    if (network.version != 4 or not network.network_address.is_global or
            not network.broadcast_address.is_global or
            any(network.overlaps(blocked) for blocked in _FORBIDDEN_IPV4)):
        raise ValueError("egress destination must be an exact public IPv4 CIDR")
    return network


def canonical_digest(value: Any) -> str:
    def plain(item):
        if isinstance(item, Mapping):
            return {str(key): plain(val) for key, val in item.items()}
        if isinstance(item, (list, tuple, set, frozenset)):
            return [plain(value) for value in item]
        return item
    encoded = json.dumps(plain(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _mapping(value, label):
    if not isinstance(value, Mapping): raise ValueError(f"{label} must be an object")
    if any(str(key).lower() in {"password", "token", "secret"} for key in value):
        raise ValueError(f"{label} must contain references, not secret values")
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class EgressGrant:
    grant_id: str
    owner: str
    kind: str
    destinations: tuple[str, ...]
    ports: tuple[int, ...]
    expires_at: str
    rule_identity: str | None = None
    counters: Mapping[str, int] = field(default_factory=dict)
    revoked: bool = False

    def __post_init__(self):
        if not isinstance(self.grant_id, str) or not _GRANT_ID.fullmatch(self.grant_id):
            raise ValueError("egress grant id is invalid")
        if not isinstance(self.owner, str) or not _GRANT_OWNER.fullmatch(self.owner):
            raise ValueError("egress grant owner is invalid")
        if self.kind not in {"public_cidr_tcp", "hostname_https"}:
            raise ValueError("egress grant kind is invalid")
        if not isinstance(self.destinations, (tuple, list)) or not self.destinations:
            raise ValueError("egress destinations are required")
        for value in self.destinations:
            if self.kind == "hostname_https":
                if not isinstance(value, str) or not _HOSTNAME.fullmatch(value):
                    raise ValueError("egress hostname is invalid")
            else:
                public_ipv4_network(value)
        if (not isinstance(self.ports, (tuple, list)) or not self.ports or
                any(isinstance(port, bool) or not isinstance(port, int)
                    for port in self.ports)):
            raise ValueError("egress ports are invalid")
        ports = tuple(self.ports)
        if any(not 1 <= port <= 65535 for port in ports):
            raise ValueError("egress ports are invalid")
        if len(set(ports)) != len(ports):
            raise ValueError("egress ports must be unique")
        if self.kind == "hostname_https" and ports != (443,):
            raise ValueError("hostname HTTPS grants allow only port 443")
        parse_utc_timestamp(self.expires_at)
        if self.rule_identity is not None and (
                not isinstance(self.rule_identity, str) or
                not _GRANT_ID.fullmatch(self.rule_identity)):
            raise ValueError("egress rule identity is invalid")
        if not isinstance(self.revoked, bool):
            raise ValueError("egress revocation state is invalid")
        destinations = tuple(value.lower().rstrip(".") if self.kind == "hostname_https"
                             else str(public_ipv4_network(value))
                             for value in self.destinations)
        if len(set(destinations)) != len(destinations):
            raise ValueError("egress destinations must be unique")
        if (not isinstance(self.counters, Mapping) or
                any(not isinstance(key, str) or isinstance(value, bool) or
                    not isinstance(value, int) or value < 0
                    for key, value in self.counters.items())):
            raise ValueError("egress counters are invalid")
        object.__setattr__(self, "destinations", destinations)
        object.__setattr__(self, "ports", ports)
        object.__setattr__(self, "counters", MappingProxyType(dict(self.counters)))


@dataclass(frozen=True)
class EgressGrantSet:
    """The separately reconciled, scoped egress capability for one guest.

    The immutable machine policy deliberately does not carry grants: adding or
    revoking an external capability must not replace the image, AppArmor
    profile, nspawn unit, or policy record that make up the isolation boundary.
    This small document is instead CAS-reconciled by the native helper against
    the stable base-policy digest.
    """

    machine_id: str
    base_policy_digest: str
    grants: tuple[EgressGrant, ...] = ()
    version: int = 1
    grant_authority: str = EGRESS_GRANT_AUTHORITY
    digest: str = ""

    def __post_init__(self):
        if not isinstance(self.machine_id, str) or not self.machine_id.startswith("sb-"):
            raise ValueError("egress grant-set identity is invalid")
        if not isinstance(self.base_policy_digest, str) or not re.fullmatch(
                r"[0-9a-f]{64}", self.base_policy_digest):
            raise ValueError("egress grant-set base policy digest is invalid")
        if self.version != 1 or self.grant_authority != EGRESS_GRANT_AUTHORITY:
            raise ValueError("egress grant-set authority is invalid")
        if not isinstance(self.grants, (tuple, list)):
            raise ValueError("egress grant-set grants are invalid")
        grants = tuple(self.grants)
        if any(not isinstance(grant, EgressGrant) for grant in grants):
            raise ValueError("egress grant-set grants are invalid")
        if any(grant.owner != self.machine_id for grant in grants):
            raise ValueError("egress grant-set owner is invalid")
        ids = tuple(grant.grant_id for grant in grants)
        if len(set(ids)) != len(ids):
            raise ValueError("egress grant-set ids must be unique")
        # A revoked grant remains observable in the requested document, but is
        # never a capability.  Canonical ordering prevents equivalent config
        # spellings from producing a needless reconcile.
        grants = tuple(sorted(grants, key=lambda grant: grant.grant_id))
        object.__setattr__(self, "grants", grants)
        expected = canonical_digest(self.to_dict(include_digest=False))
        if self.digest and self.digest != expected:
            raise ValueError("egress grant-set digest is invalid")
        object.__setattr__(self, "digest", expected)

    def to_dict(self, *, include_digest=True):
        value = {
            "version": self.version, "machine_id": self.machine_id,
            "base_policy_digest": self.base_policy_digest,
            "grant_authority": self.grant_authority,
            "grants": [{
                "grant_id": grant.grant_id, "owner": grant.owner,
                "kind": grant.kind, "destinations": list(grant.destinations),
                "ports": list(grant.ports), "expires_at": grant.expires_at,
                "revoked": grant.revoked,
            } for grant in self.grants],
        }
        if include_digest:
            value["grant_digest"] = self.digest
        return value

    @classmethod
    def from_dict(cls, value):
        keys = {"version", "machine_id", "base_policy_digest", "grant_authority",
                "grants", "grant_digest"}
        if not isinstance(value, Mapping) or set(value) != keys:
            raise ValueError("egress grant-set document is invalid")
        raw_grants = value["grants"]
        if not isinstance(raw_grants, list):
            raise ValueError("egress grant-set document is invalid")
        grants = []
        for raw in raw_grants:
            expected = {"grant_id", "owner", "kind", "destinations", "ports",
                        "expires_at", "revoked"}
            if not isinstance(raw, Mapping) or set(raw) != expected:
                raise ValueError("egress grant-set document is invalid")
            grants.append(EgressGrant(
                raw["grant_id"], raw["owner"], raw["kind"],
                tuple(raw["destinations"]), tuple(raw["ports"]), raw["expires_at"],
                revoked=raw["revoked"],
            ))
        return cls(value["machine_id"], value["base_policy_digest"], tuple(grants),
                   version=value["version"], grant_authority=value["grant_authority"],
                   digest=value["grant_digest"])


@dataclass(frozen=True)
class ManagedIsolationPolicy:
    policy_version: int
    machine_id: str
    uid_map: Mapping[str, int]
    root_image: Mapping[str, Any]
    read_only_mounts: tuple[Mapping[str, str], ...]
    writable_mounts: tuple[Mapping[str, str], ...]
    network: Mapping[str, Any]
    syscalls: Mapping[str, Any]
    devices: frozenset[str]
    resources: Mapping[str, Any]
    credentials: tuple[str, ...]
    digest: str = ""

    def __post_init__(self):
        if self.policy_version < 1 or not self.machine_id.startswith("sb-"):
            raise ValueError("managed isolation identity is invalid")
        object.__setattr__(self, "uid_map", _mapping(self.uid_map, "uid map"))
        object.__setattr__(self, "root_image", _mapping(self.root_image, "root image"))
        object.__setattr__(self, "read_only_mounts", tuple(_mapping(v, "read-only mount") for v in self.read_only_mounts))
        object.__setattr__(self, "writable_mounts", tuple(_mapping(v, "writable mount") for v in self.writable_mounts))
        object.__setattr__(self, "network", _mapping(self.network, "network policy"))
        if "grants" in self.network:
            raise ValueError("managed isolation policy must not embed egress grants")
        object.__setattr__(self, "syscalls", _mapping(self.syscalls, "syscall policy"))
        object.__setattr__(self, "devices", frozenset(self.devices))
        object.__setattr__(self, "resources", _mapping(self.resources, "resource policy"))
        object.__setattr__(self, "credentials", tuple(self.credentials))
        basis = self.to_dict(include_digest=False)
        expected = canonical_digest(basis)
        if self.digest and self.digest != expected:
            raise ValueError("managed isolation policy digest is invalid")
        object.__setattr__(self, "digest", expected)

    def to_dict(self, *, include_digest=True):
        value = {"policy_version": self.policy_version, "machine_id": self.machine_id,
                 "uid_map": dict(self.uid_map), "root_image": dict(self.root_image),
                 "read_only_mounts": [dict(v) for v in self.read_only_mounts],
                 "writable_mounts": [dict(v) for v in self.writable_mounts],
                 "network": dict(self.network), "syscalls": dict(self.syscalls),
                 "devices": sorted(self.devices), "resources": dict(self.resources),
                 "credentials": list(self.credentials)}
        if include_digest: value["digest"] = self.digest
        return value


@dataclass(frozen=True)
class NativeCleanupRecovery:
    owner: str
    object_type: str
    identity: str
    expected_digest: str
    observed_digest: str | None
    reason_code: str
    retry_state: str
