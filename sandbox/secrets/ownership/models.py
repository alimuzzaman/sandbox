"""Frozen non-secret data shapes for secret ownership profiles (spec 069).

Every name here is part of the frozen contract in
``specs/069-secret-profiles/contracts/python-interfaces.md``. Nothing in this
module carries, hashes or derives a secret value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from sandbox.secrets.models import SecretBrokerError

if TYPE_CHECKING:  # pragma: no cover - typing only; keeps model import light
    from sandbox.secrets.sources import SourceRegistry


Domain = Literal["office", "personal"]
Origin = Literal["source", "destination"]
Capability = Literal["read", "create"]

_DOMAINS = ("office", "personal")
_ORIGINS = ("source", "destination")
_GATEWAYS = ("none", "cloudflare-access")
_OWNERSHIP_KEYS = frozenset(("profiles", "entries"))
_PROFILE_KEYS = frozenset(("domain", "destination"))
_DESTINATION_KEYS = frozenset((
    "endpoint", "projectId", "environment", "path", "readClientId",
    "createClientId", "timeoutSeconds", "gateway",
))
_ENTRY_KEYS = frozenset(("source", "key", "profile", "name", "consumer", "origin"))


class NameState(str, Enum):
    PENDING = "pending"
    ATTEMPTED = "attempted"
    CREATED = "created"
    PRE_EXISTING = "pre_existing"
    CONFLICT = "conflict"
    RECONCILED = "reconciled"
    ACCEPTANCE_UNKNOWN = "acceptance_unknown"
    VERIFIED = "verified"
    FAILED = "failed"
    UNRESOLVED = "unresolved"


class BlockReason(str, Enum):
    UNMAPPED = "unmapped"
    UNDECIDED = "undecided"
    MULTIPLY_ASSIGNED = "multiply_assigned"
    DUPLICATE_DESTINATION = "duplicate_destination"
    OTHER_DOMAIN = "other_domain"
    UNREGISTERED_SOURCE = "unregistered_source"
    SOURCE_UNSUPPORTED = "source_unsupported"


class Existence(str, Enum):
    ABSENT = "absent"
    PRESENT = "present"
    UNCHECKED = "unchecked"
    UNKNOWN = "unknown"


def canonical_json(value: object) -> bytes:
    """Canonical ASCII JSON used for every ownership digest."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")


def _invalid() -> SecretBrokerError:
    return SecretBrokerError(
        "authority_mismatch", "secret ownership metadata is not canonical",
    )


@dataclass(frozen=True)
class Destination:
    endpoint: str
    project_id: str
    environment: str
    path: str
    read_client_id: str | None
    create_client_id: str | None
    timeout_seconds: int
    gateway: Literal["none", "cloudflare-access"] = "none"

    def as_normalized(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "projectId": self.project_id,
            "environment": self.environment,
            "path": self.path,
            "readClientId": self.read_client_id,
            "createClientId": self.create_client_id,
            "timeoutSeconds": self.timeout_seconds,
            "gateway": self.gateway,
        }


@dataclass(frozen=True)
class OwnershipProfile:
    name: str
    domain: Domain
    destination: Destination


@dataclass(frozen=True)
class MapEntry:
    source: str
    key: str
    profile: str
    name: str
    consumer: str
    origin: Origin

    def as_normalized(self) -> dict:
        return {
            "source": self.source, "key": self.key, "profile": self.profile,
            "name": self.name, "consumer": self.consumer, "origin": self.origin,
        }


def _text(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise _invalid()
    return value


def _exact_keys(value: object, keys: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _invalid()
    return value


def _destination(value: object) -> Destination:
    item = _exact_keys(value, _DESTINATION_KEYS)
    timeout = item["timeoutSeconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        raise _invalid()
    if item["gateway"] not in _GATEWAYS:
        raise _invalid()
    return Destination(
        endpoint=_text(item["endpoint"]),
        project_id=_text(item["projectId"]),
        environment=_text(item["environment"]),
        path=_text(item["path"]),
        read_client_id=_text(item["readClientId"], optional=True),
        create_client_id=_text(item["createClientId"], optional=True),
        timeout_seconds=timeout,
        gateway=item["gateway"],
    )


@dataclass(frozen=True)
class OwnershipConfig:
    profiles: Mapping[str, OwnershipProfile]
    entries: tuple[MapEntry, ...]
    map_sha256: str            # sha256 of canonical normalized ownership block

    @staticmethod
    def from_normalized(block: Mapping) -> "OwnershipConfig":
        """Rebuild from a normalized block; strict shape, metadata only."""
        root = _exact_keys(block, _OWNERSHIP_KEYS)
        raw_profiles = root["profiles"]
        raw_entries = root["entries"]
        if (not isinstance(raw_profiles, Mapping) or not isinstance(raw_entries, (list, tuple))
                or any(not isinstance(name, str) for name in raw_profiles)):
            raise _invalid()
        profiles: dict[str, OwnershipProfile] = {}
        for name in sorted(raw_profiles):
            if not isinstance(name, str) or not name or name == "undecided":
                raise _invalid()
            item = _exact_keys(raw_profiles[name], _PROFILE_KEYS)
            if item["domain"] not in _DOMAINS:
                raise _invalid()
            profiles[name] = OwnershipProfile(
                name=name, domain=item["domain"],
                destination=_destination(item["destination"]),
            )
        entries = []
        for raw in raw_entries:
            item = _exact_keys(raw, _ENTRY_KEYS)
            if item["origin"] not in _ORIGINS:
                raise _invalid()
            profile = _text(item["profile"])
            if profile != "undecided" and profile not in profiles:
                raise _invalid()
            entries.append(MapEntry(
                source=_text(item["source"]), key=_text(item["key"]),
                profile=profile, name=_text(item["name"]),
                consumer=_text(item["consumer"]), origin=item["origin"],
            ))
        partial = OwnershipConfig(
            profiles=MappingProxyType(profiles), entries=tuple(entries), map_sha256="",
        )
        normalized = partial.as_normalized()
        if canonical_json(normalized) != canonical_json(_plain(block)):
            raise _invalid()
        return OwnershipConfig(
            profiles=partial.profiles, entries=partial.entries,
            map_sha256=hashlib.sha256(canonical_json(normalized)).hexdigest(),
        )

    def as_normalized(self) -> dict:
        """Reconstruct the complete canonical normalized ownership block."""
        return {
            "profiles": {
                name: {
                    "domain": profile.domain,
                    "destination": profile.destination.as_normalized(),
                }
                for name, profile in sorted(self.profiles.items())
            },
            "entries": [entry.as_normalized() for entry in self.entries],
        }


def _plain(value: object) -> object:
    """Convert mapping/sequence views to JSON-serializable plain values."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise _invalid()


@dataclass(frozen=True)
class ClassificationReport:
    profile: str
    states: Mapping[str, tuple[str, ...]]        # state/reason value -> sorted names
    blocking: bool

    def counts(self) -> dict[str, int]:
        return {state: len(names) for state, names in sorted(self.states.items())}

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "blocking": self.blocking,
            "counts": self.counts(),
            "names": {state: list(names) for state, names in sorted(self.states.items())},
        }


@dataclass(frozen=True)
class SourceContinuity:
    alias: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int
    nlink: int
    uid: int
    mode: int
    names_sha256: str
    reliable: bool
    unreliable_reason: str | None

    def same_as(self, other: "SourceContinuity") -> bool:
        if not isinstance(other, SourceContinuity):
            return False
        fields = (
            "alias", "dev", "ino", "size", "mtime_ns", "ctime_ns", "nlink",
            "uid", "mode", "names_sha256",
        )
        return all(getattr(self, name) == getattr(other, name) for name in fields)


@dataclass(frozen=True)
class Grant:                     # what a worker is allowed to do; non-secret
    profile: str
    capability: Capability
    destination: Destination
    client_id: str
    authority_digest: str        # "o1-..."
    names: tuple[str, ...]       # exact allowlist for this call
    project_root: str
    ownership: Mapping           # complete canonical non-secret authority


@dataclass(frozen=True)
class StorageAdmission:
    evidence_sha256: str
    destination_origin: str
    project_id: str
    captured_at: float
    expires_at: float
    attestation: Literal["operator_attested"]


@dataclass(frozen=True)
class Approval:
    approval_id: str
    operation: Literal["migrate", "cleanup"]
    operator_uid: int
    operator_login: str
    controller_id: str
    profile: str
    sources: tuple[SourceContinuity, ...]
    map_sha256: str
    destination_sha256: str
    authority_digest: str
    evidence_sha256: str | None
    evidence_expires_at: float | None
    created_at: float
    expires_at: float
    surface: Literal["cli_tty"]


@dataclass(frozen=True)
class CreateOutcome:
    name: str
    state: NameState
    reason: str | None


@dataclass(frozen=True)
class RevocationOutcome:
    status: Literal["revoked", "cleanup_unknown", "not_applicable"]
    token_expires_at: float | None
    next_action: str | None


@dataclass(frozen=True)
class ScopedRunRequest:
    profile: str
    keys: tuple[str, ...]
    argv: tuple[str, ...]
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True)
class OwnershipContext:
    config: OwnershipConfig
    registry: "SourceRegistry"
    project_root: Path
    runtime_root: Path
    controller_id: str
    read_only_broker: object | None
    credential_store: object | None


__all__ = [
    "Approval", "BlockReason", "Capability", "ClassificationReport",
    "CreateOutcome", "Destination", "Domain", "Existence", "Grant",
    "MapEntry", "NameState", "Origin", "OwnershipConfig", "OwnershipContext",
    "OwnershipProfile", "RevocationOutcome", "ScopedRunRequest",
    "SourceContinuity", "StorageAdmission", "canonical_json",
]
