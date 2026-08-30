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


def _strict_mapping(value: Any, *, fields: Sequence[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(fields):
        raise ValueError("invalid %s fields" % label)
    return value


def _non_negative(value: Any, label: str, *, optional: bool = False) -> Optional[int]:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("%s must be a non-negative integer" % label)
    return value


def _reason_codes(values: Sequence[str]) -> tuple:
    if not isinstance(values, (list, tuple)) or len(values) > 8:
        raise ValueError("invalid reason codes")
    result = tuple(values)
    if any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item)
           for item in result):
        raise ValueError("invalid reason codes")
    return result


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

    def __post_init__(self):
        parse_utc(self.observed_at)
        if not self.target_identity or self.evidence_state not in {item.value for item in EvidenceState}:
            raise ValueError("invalid host-memory projection")
        for name in ("memory_total_bytes", "memory_available_bytes", "swap_total_bytes",
                     "swap_used_bytes"):
            _non_negative(getattr(self, name), name, optional=True)
        if (self.swap_total_bytes is not None and self.swap_used_bytes is not None
                and self.swap_used_bytes > self.swap_total_bytes):
            raise ValueError("swap used exceeds total")

    def to_dict(self): return bounded(asdict(self), 64 * 1024)


@dataclass(frozen=True)
class RemoteSwapState:
    observed_at: str
    memory: Mapping[str, Optional[int]]
    filesystem: Mapping[str, Optional[int]]
    swap_areas: Sequence[Mapping[str, Any]]
    swappiness: Mapping[str, Any]
    monitor: Mapping[str, Any]
    container_eligibility: Mapping[str, Any]
    reboot_verification: Any
    operation_block: Optional[Mapping[str, Any]]
    ownership: str
    evidence_state: str
    observation_digest: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        required = {"observed_at", "memory", "filesystem", "swap_areas", "swappiness",
                    "monitor", "container_eligibility", "reboot_verification",
                    "operation_block", "ownership", "evidence_state"}
        if not isinstance(value, Mapping) or set(value) - (required | {"observation_digest", "target_identity"}) or not required.issubset(value):
            raise ValueError("invalid remote swap state fields")
        parse_utc(value["observed_at"])
        memory = value["memory"]
        if not isinstance(memory, Mapping):
            raise ValueError("invalid memory evidence")
        total = _non_negative(memory.get("total_bytes"), "memory total", optional=True)
        available = _non_negative(memory.get("available_bytes"), "memory available", optional=True)
        if total is not None and available is not None and available > total:
            raise ValueError("available memory exceeds total")
        filesystem = value["filesystem"]
        if not isinstance(filesystem, Mapping):
            raise ValueError("invalid filesystem evidence")
        fs_total = _non_negative(filesystem.get("total_bytes"), "filesystem total", optional=True)
        fs_free = _non_negative(filesystem.get("free_bytes"), "filesystem free", optional=True)
        if fs_total is not None and fs_free is not None and fs_free > fs_total:
            raise ValueError("filesystem free exceeds total")
        areas = value["swap_areas"]
        if not isinstance(areas, (list, tuple)) or len(areas) > 32:
            raise ValueError("invalid swap areas")
        for area in areas:
            if not isinstance(area, Mapping):
                raise ValueError("invalid swap area")
            area_total = _non_negative(area.get("total_bytes"), "swap total")
            area_used = _non_negative(area.get("used_bytes"), "swap used")
            if area_used > area_total:
                raise ValueError("swap used exceeds total")
        if value["evidence_state"] not in {item.value for item in EvidenceState}:
            raise ValueError("invalid evidence state")
        payload = {key: value[key] for key in required}
        digest = canonical_digest(payload)
        supplied = value.get("observation_digest")
        if supplied is not None and supplied != digest:
            raise ValueError("observation digest mismatch")
        return cls(**payload, observation_digest=digest)

    def to_dict(self):
        return bounded(asdict(self), 256 * 1024)


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
    state: str = "planned"
    schema_version: int = 1

    def __post_init__(self):
        if not HEX64.fullmatch(self.plan_id) or not HEX64.fullmatch(self.observation_digest):
            raise ValueError("invalid plan identity")
        parse_utc(self.created_at); parse_utc(self.expires_at)
        if self.operation not in {"enable", "disable"} or not self.requires_confirmation:
            raise ValueError("invalid lifecycle plan")
        if self.state not in {"planned", "in_progress", "terminal", "reconciliation_required"}:
            raise ValueError("invalid lifecycle plan state")

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
        if self.status not in {"valid", "partial", "failed"}:
            raise ValueError("invalid aggregate sample")
        _reason_codes(self.errors)
        allowed_memory = {"total_bytes", "available_bytes", "free_bytes", "buffers_bytes", "cached_bytes"}
        if set(self.memory) - allowed_memory or set(self.swap) != {"total_bytes", "free_bytes", "used_bytes"}:
            raise ValueError("invalid aggregate counters")
        for name, value in tuple(self.memory.items()) + tuple(self.swap.items()):
            _non_negative(value, name)
        if self.swap["free_bytes"] + self.swap["used_bytes"] != self.swap["total_bytes"]:
            raise ValueError("invalid swap arithmetic")
        if set(self.vm_counters) - {"pswpin", "pswpout", "pgmajfault"}:
            raise ValueError("invalid VM counters")
        for name, value in self.vm_counters.items():
            _non_negative(value, name)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        fields = ("sampled_at", "status", "memory", "swap", "pressure", "vm_counters",
                  "errors", "schema_version")
        _strict_mapping(value, fields=fields, label="aggregate sample")
        return cls(**dict(value))

    def to_dict(self): return bounded(asdict(self), 16 * 1024)


@dataclass(frozen=True)
class MonitorHealth:
    service_state: str
    timer_state: str
    interval_seconds: Optional[int]
    latest_sample_at: Optional[str]
    age_seconds: Optional[int]
    freshness: str
    next_sample_at: Optional[str]
    sustained_swap_use: Optional[bool]
    pressure_state: str
    retention: Mapping[str, Any]

    def __post_init__(self):
        states = {"active", "inactive", "missing", "unknown", "drifted"}
        if self.service_state not in states or self.timer_state not in states:
            raise ValueError("invalid monitor service state")
        if self.freshness not in {"fresh", "stale", "missing", "malformed", "unknown"}:
            raise ValueError("invalid monitor freshness")
        if self.pressure_state not in {"normal", "pressured", "unknown"}:
            raise ValueError("invalid pressure state")
        _non_negative(self.interval_seconds, "monitor interval", optional=True)
        _non_negative(self.age_seconds, "sample age", optional=True)
        if self.latest_sample_at is not None: parse_utc(self.latest_sample_at)
        if self.next_sample_at is not None: parse_utc(self.next_sample_at)
        required = {"current_files", "history_files", "total_bytes", "compliant", "truncated"}
        if not isinstance(self.retention, Mapping) or set(self.retention) != required:
            raise ValueError("invalid retention evidence")

    def to_dict(self): return bounded(asdict(self), 16 * 1024)


@dataclass(frozen=True)
class HistoryWindow:
    requested_range: Mapping[str, Optional[str]]
    observed_range: Optional[Mapping[str, str]]
    samples: Sequence[Mapping[str, Any]]
    counts: Mapping[str, int]
    freshness: str
    complete: bool
    truncated: bool

    def __post_init__(self):
        if len(self.samples) > 1000:
            raise ValueError("history sample limit exceeded")
        for row in self.samples: AggregateMemorySample.from_dict(row)
        required_counts = {"returned", "valid", "partial", "failed", "malformed", "missing"}
        if set(self.counts) != required_counts or any(_non_negative(value, name) is None for name, value in self.counts.items()):
            raise ValueError("invalid history counts")
        if self.counts["returned"] != len(self.samples):
            raise ValueError("history count mismatch")

    def to_dict(self): return bounded(asdict(self))


@dataclass(frozen=True)
class ProtectedSwapOperation:
    operation_id: str
    plan_id: str
    phase: str
    prior_state_digest: str
    last_observation_digest: str
    phase_evidence: Sequence[Mapping[str, Any]]
    mutation_started: bool
    rollback: Optional[Mapping[str, Any]]
    outcome: Optional[str]
    unrelated_mutation_blocked: bool
    schema_version: int = 1

    def __post_init__(self):
        if not all(HEX64.fullmatch(value) for value in (self.operation_id, self.plan_id,
                                                        self.prior_state_digest,
                                                        self.last_observation_digest)):
            raise ValueError("invalid protected operation identity")
        phases = {"accepted", "preflight", "staged", "persistent", "active", "monitoring",
                  "verifying", "rolling_back", "terminal"}
        if self.phase not in phases or len(self.phase_evidence) > 64:
            raise ValueError("invalid protected operation phase")
        if self.outcome is not None and self.outcome not in {item.value for item in OperationOutcome}:
            raise ValueError("invalid protected operation outcome")

    def to_dict(self): return bounded(asdict(self), 256 * 1024)


@dataclass(frozen=True)
class OwnershipReceipt:
    target_identity: str
    created_by_operation: str
    last_verified_operation: str
    policy: Mapping[str, Any]
    artifacts: Mapping[str, Mapping[str, Any]]
    swap_area_id: str
    prior_swappiness: Mapping[str, Any]
    verified_at: str
    reboot_verification: Mapping[str, Any]
    lifecycle_state: str
    schema_version: int = 1

    def __post_init__(self):
        if not self.target_identity or not HEX64.fullmatch(self.created_by_operation) or not HEX64.fullmatch(self.last_verified_operation):
            raise ValueError("invalid ownership identity")
        if not HEX24.fullmatch(self.swap_area_id) or self.lifecycle_state not in {"enabled", "disabled"}:
            raise ValueError("invalid ownership state")
        SwapPolicy(**dict(self.policy)); parse_utc(self.verified_at)
        if not isinstance(self.artifacts, Mapping) or len(self.artifacts) > 16:
            raise ValueError("invalid owned artifacts")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]):
        fields = ("target_identity", "created_by_operation", "last_verified_operation",
                  "policy", "artifacts", "swap_area_id", "prior_swappiness", "verified_at",
                  "reboot_verification", "lifecycle_state", "schema_version")
        _strict_mapping(value, fields=fields, label="ownership receipt")
        return cls(**dict(value))

    def to_dict(self): return bounded(asdict(self), 128 * 1024)
