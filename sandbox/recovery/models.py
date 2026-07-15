from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class RecoveryProfile:
    profile_id: str
    scope: str
    source_type: str
    allowed_roots: tuple[str, ...]
    sources: tuple[str, ...]
    capture_mode: str
    consistency: str
    excludes: tuple[str, ...]
    sensitivity: str
    restore_target: str
    verification: str
    retention_class: str
    dependencies: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1
    enabled: bool = True
    schedule_class: str = "manual"

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class ArtifactPlan:
    profile_id: str
    artifact_id: str
    source_type: str
    allowed_roots: tuple[str, ...]
    sources: tuple[str, ...]
    capture_mode: str
    consistency: str
    excludes: tuple[str, ...]
    restore_target: str
    verification: str
    dependencies: tuple[str, ...]
    rationale: str
    status: str = "planned"
    warnings: tuple[str, ...] = ()


_ARTIFACT_TRANSITIONS = {
    "capturing": {"validated", "failed"},
    "validated": {"packaged", "failed"},
    "packaged": {"failed"},
    "failed": set(),
}


@dataclass(frozen=True)
class ArtifactRecord:
    profile_id: str
    artifact_id: str
    source_type: str
    state: str = "capturing"

    def transition(self, state: str) -> "ArtifactRecord":
        if state not in _ARTIFACT_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"invalid artifact transition: {self.state} -> {state}")
        return ArtifactRecord(self.profile_id, self.artifact_id, self.source_type, state)


_SET_TRANSITIONS = {
    "staging": {"captured", "incomplete"},
    "captured": {"encrypted", "incomplete"},
    "encrypted": {"remotely_verified", "incomplete"},
    "remotely_verified": {"complete", "incomplete"},
    "complete": set(),
    "incomplete": set(),
}


@dataclass(frozen=True)
class RecoverySet:
    set_id: str
    status: str = "staging"

    @property
    def restorable(self) -> bool:
        return self.status == "complete"

    def transition(self, status: str) -> "RecoverySet":
        if status not in _SET_TRANSITIONS.get(self.status, set()):
            raise ValueError(f"invalid recovery set transition: {self.status} -> {status}")
        return RecoverySet(self.set_id, status)


@dataclass(frozen=True)
class RestorePlan:
    set_id: str
    profiles: tuple[str, ...]
    actions: tuple[str, ...]
    prerequisites: tuple[str, ...]
    checkpoints: tuple[str, ...]
    rollback: tuple[str, ...]
    warnings: tuple[str, ...]
    requires_confirmation: bool = True


@dataclass(frozen=True)
class SchedulePolicy:
    policy_id: str
    profiles: tuple[str, ...]
    calendar: str
    enabled: bool = False
    randomized_delay: str = "15m"
    timeout: str = "6h"
    remote: str | None = None


@dataclass(frozen=True)
class RetentionPlan:
    destination_prefix: str
    protected_sets: tuple[str, ...]
    candidates: tuple[str, ...]
    requires_confirmation: bool = True


@dataclass(frozen=True)
class RecoveryPlan:
    schema_version: int
    profiles: tuple[str, ...]
    artifacts: tuple[ArtifactPlan, ...]
    excluded: tuple[Mapping[str, str], ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)
