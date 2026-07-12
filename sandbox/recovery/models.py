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
