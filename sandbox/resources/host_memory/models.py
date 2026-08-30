"""Strict, immutable Feature 046 domain models."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

MAX_RESPONSE_BYTES = 1024 * 1024
HEX24 = re.compile(r"^[0-9a-f]{24}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceState(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    STALE = "stale"
    MALFORMED = "malformed"
    UNSUPPORTED = "unsupported"
    UNMANAGED = "unmanaged"
    PARTIAL = "partial"
    DRIFTED = "drifted"


class OperationOutcome(str, Enum):
    APPLIED = "applied"
    ALREADY_CURRENT = "already_current"
    REFUSED = "refused"
    PARTIAL = "partial"
    FAILED = "failed"
    ROLLBACK_COMPLETE = "rollback_complete"
    ROLLBACK_INCOMPLETE = "rollback_incomplete"


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid UTC timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("invalid UTC timestamp")
    return parsed.astimezone(timezone.utc)


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def bounded(value: Mapping[str, Any], maximum: int = MAX_RESPONSE_BYTES) -> dict:
    payload = dict(value)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > maximum:
        raise ValueError("host-memory evidence exceeds bound")
    forbidden = {"stdout", "stderr", "argv", "environment", "command", "path",
                 "pid", "process", "container_id"}
    def walk(item):
        if isinstance(item, dict):
            if forbidden.intersection(item):
                raise ValueError("host-memory evidence contains forbidden fields")
            for child in item.values(): walk(child)
        elif isinstance(item, list):
            for child in item: walk(child)
    walk(payload)
    return payload


@dataclass(frozen=True)
class RemoteServiceEvidence:
    remote_name: str
    target_identity: str
    ownership_marker: str
    runtime_revision: str
    resource_schema: int = 1
    host_memory_schema: int = 1
    transport: str = "control"

    def __post_init__(self):
        if not self.remote_name or len(self.remote_name) > 128 or not self.target_identity:
            raise ValueError("invalid remote service identity")
        if not HEX24.fullmatch(self.ownership_marker) or not HEX24.fullmatch(self.runtime_revision):
            raise ValueError("invalid remote service marker or revision")
        if self.resource_schema != 1 or self.host_memory_schema != 1 or self.transport != "control":
            raise ValueError("unsupported host-memory protocol")


@dataclass(frozen=True)
class SwapPolicy:
    size_gib: int = 4
    swappiness: int = 15
    sample_interval_seconds: int = 300
    freshness_seconds: int = 660
    warning_swap_used_bytes: int = 512 * 1024 * 1024
    warning_consecutive_samples: int = 3
    history_files: int = 9
    history_bytes: int = 32 * 1024 * 1024
    sample_timeout_seconds: int = 5

    def __post_init__(self):
        if (isinstance(self.size_gib, bool) or not isinstance(self.size_gib, int)
                or not 1 <= self.size_gib <= 8):
            raise ValueError("size_gib must be between 1 and 8")
        fixed = (self.swappiness, self.sample_interval_seconds, self.freshness_seconds,
                 self.warning_consecutive_samples, self.history_files,
                 self.history_bytes, self.sample_timeout_seconds)
        if fixed != (15, 300, 660, 3, 9, 32 * 1024 * 1024, 5):
            raise ValueError("unsupported host-memory policy override")

    def to_dict(self): return asdict(self)


@dataclass(frozen=True)
class HostMemoryStatusProjection:
    target_identity: str
    observed_at: str
    evidence_state: str
    memory_total_bytes: Optional[int]
    memory_available_bytes: Optional[int]
    swap_total_bytes: Optional[int]
    swap_used_bytes: Optional[int]
    ownership: str
    monitor_freshness: str
    sustained_swap_use: Optional[bool]
    pressure_state: str
    operation_block: Optional[str]

    def to_dict(self): return bounded(asdict(self), 64 * 1024)


@dataclass(frozen=True)
class SwapLifecyclePlan:
    plan_id: str
    operation: str
    target: Mapping[str, Any]
    created_at: str
    expires_at: str
    observation: Mapping[str, Any]
    observation_digest: str
    requested_policy: Optional[Mapping[str, Any]]
    effective_policy: Optional[Mapping[str, Any]]
    calculations: Sequence[Mapping[str, Any]]
    intended_changes: Sequence[str]
    rollback_scope: Sequence[str]
    requires_confirmation: bool = True
    schema_version: int = 1

    def __post_init__(self):
        if not HEX64.fullmatch(self.plan_id) or not HEX64.fullmatch(self.observation_digest):
            raise ValueError("invalid plan identity")
        if self.operation not in {"enable", "disable"} or not self.requires_confirmation:
            raise ValueError("invalid lifecycle plan")

    def to_dict(self): return bounded(asdict(self))


@dataclass(frozen=True)
class AggregateMemorySample:
    sampled_at: str
    status: str
    memory: Mapping[str, int]
    swap: Mapping[str, int]
    pressure: Optional[Mapping[str, Any]] = None
    vm_counters: Mapping[str, int] = field(default_factory=dict)
    errors: Sequence[str] = field(default_factory=tuple)
    schema_version: int = 1

    def __post_init__(self):
        parse_utc(self.sampled_at)
        if self.status not in {"valid", "partial", "failed"} or len(self.errors) > 8:
            raise ValueError("invalid aggregate sample")
        if set(self.vm_counters) - {"pswpin", "pswpout", "pgmajfault"}:
            raise ValueError("invalid VM counters")

    def to_dict(self): return bounded(asdict(self), 16 * 1024)
