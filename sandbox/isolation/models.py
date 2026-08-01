"""Immutable, digest-bound managed isolation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import ipaddress
import json
from types import MappingProxyType
from typing import Any, Mapping


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
        if self.kind not in {"public_cidr_tcp", "hostname_https"}:
            raise ValueError("egress grant kind is invalid")
        for value in self.destinations:
            try: address = ipaddress.ip_network(value, strict=False)
            except ValueError:
                if self.kind != "hostname_https" or "." not in value:
                    raise ValueError("egress destination is invalid")
                continue
            if address.is_private or address.is_loopback or address.is_link_local \
                    or address.is_multicast or address.is_unspecified:
                raise ValueError("egress destination must be public")
        ports = tuple(int(port) for port in self.ports)
        if not ports or any(not 1 <= port <= 65535 for port in ports):
            raise ValueError("egress ports are invalid")
        if self.kind == "hostname_https" and ports != (443,):
            raise ValueError("hostname HTTPS grants allow only port 443")
        object.__setattr__(self, "destinations", tuple(self.destinations))
        object.__setattr__(self, "ports", ports)
        object.__setattr__(self, "counters", MappingProxyType(dict(self.counters)))


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
