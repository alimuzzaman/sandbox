"""Small, serialisable value objects for the workspace index.

These models intentionally contain no filesystem or SQLite behaviour.  They
are useful to callers that want to provide typed correlation evidence without
letting a path-derived guess become a project identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


def _dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    label: str
    project_identity: str | None = None
    namespace: str | None = None
    path: str | None = None
    lifecycle: str = "ready"
    status: str = "ready"
    source: str = "index"
    aliases: tuple[str, ...] = ()
    bindings: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))
        object.__setattr__(
            self,
            "bindings",
            tuple(MappingProxyType(dict(item)) for item in self.bindings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "label": self.label,
            "project_identity": self.project_identity,
            "namespace": self.namespace,
            "path": self.path,
            "lifecycle": self.lifecycle,
            "status": self.status,
            "source": self.source,
            "aliases": list(self.aliases),
            "bindings": [dict(item) for item in self.bindings],
            "metadata": _dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # The existing service and CLI use ``label``/``path``; dict-like access
    # keeps this frozen model convenient for those compatibility callers.
    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True)
class ResourceBinding:
    workspace_id: str
    resource_type: str
    resource_id: str
    status: str = "owned"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata or {})))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "status": self.status,
            "metadata": _dict(self.metadata),
        }


@dataclass(frozen=True)
class ProjectEvidence:
    """Exact project identity evidence for one legacy workspace leaf."""

    project_identity: str
    namespace: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_identity": self.project_identity,
            "namespace": self.namespace,
            "label": self.label,
            "kind": "project",
        }


@dataclass(frozen=True)
class JobEvidence:
    """Exact job-derived identity evidence for one legacy workspace leaf."""

    project_identity: str
    namespace: str
    label: str
    job_id: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "project_identity": self.project_identity,
            "namespace": self.namespace,
            "label": self.label,
            "job_id": self.job_id,
            "kind": "job",
        }


# A neutral name is handy for callers which merge project and job evidence.
WorkspaceEvidence = ProjectEvidence


@dataclass(frozen=True)
class LegacyWorkspace:
    namespace: str
    label: str
    path: str
    raw_bytes: bytes = b""
    payload: Mapping[str, Any] = field(default_factory=dict)
    digest: str = ""
    status: str = "unresolved"
    project_identity: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload or {})))

    def to_dict(self, *, include_bytes: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "namespace": self.namespace,
            "label": self.label,
            "path": self.path,
            "digest": self.digest,
            "status": self.status,
            "project_identity": self.project_identity,
            "payload": _dict(self.payload),
            "reason": self.reason,
        }
        if include_bytes:
            result["raw_bytes"] = self.raw_bytes
        return result


@dataclass(frozen=True)
class MigrationItem:
    path: str
    namespace: str
    label: str
    digest: str
    status: str
    project_identity: str | None = None
    reason: str | None = None
    workspace_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "namespace": self.namespace,
            "label": self.label,
            "digest": self.digest,
            "status": self.status,
            "project_identity": self.project_identity,
            "reason": self.reason,
            "workspace_id": self.workspace_id,
        }


@dataclass(frozen=True)
class MigrationPlan:
    plan_id: str
    digest: str
    generation: int
    created_at: str
    expires_at: str
    items: tuple[MigrationItem, ...] = ()
    summary: Mapping[str, int] = field(default_factory=dict)
    project_identity: str | None = None
    expected_namespace: str | None = None
    evidence: tuple[Mapping[str, Any], ...] = ()
    inventory_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", MappingProxyType(dict(self.summary or {})))
        object.__setattr__(
            self,
            "evidence",
            tuple(MappingProxyType(dict(item)) for item in self.evidence),
        )

    @property
    def records(self) -> tuple[MigrationItem, ...]:
        return self.items

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "digest": self.digest,
            "generation": self.generation,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "items": [item.to_dict() for item in self.items],
            "records": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
            "project_identity": self.project_identity,
            "expected_namespace": self.expected_namespace,
            "evidence": [dict(item) for item in self.evidence],
            "inventory_digest": self.inventory_digest,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]
