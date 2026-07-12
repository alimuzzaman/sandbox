from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class BackupArtifact:
    artifact_id: str
    source: str
    mode: str
    rationale: str
    restore_target: str | None = None


@dataclass(frozen=True)
class BackupPlan:
    schema_version: int
    backup_id: str
    scope: str
    artifacts: tuple[BackupArtifact, ...]
    excludes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RestorePlan:
    backup_id: str
    actions: tuple[Mapping[str, Any], ...]
    requires_confirmation: bool = True


def plan_restore(manifest: Mapping[str, Any]) -> RestorePlan:
    """Build a non-mutating restore plan from a validated manifest."""
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported backup manifest schema")
    backup_id = str(manifest.get("id") or "")
    if not backup_id:
        raise ValueError("backup manifest id is required")
    actions = []
    for artifact in manifest.get("artifacts") or ():
        if not isinstance(artifact, Mapping) or not artifact.get("id"):
            raise ValueError("invalid backup artifact")
        actions.append({
            "artifact_id": artifact["id"],
            "archive": artifact.get("archive"),
            "target": artifact.get("restore_target"),
            "verify_sha256": artifact.get("sha256"),
        })
    return RestorePlan(backup_id=backup_id, actions=tuple(actions))


def manifest_for(plan: BackupPlan, artifacts: Sequence[Mapping[str, Any]]) -> dict:
    return {
        "schema_version": plan.schema_version,
        "id": plan.backup_id,
        "scope": plan.scope,
        "artifacts": list(artifacts),
        "excluded": list(plan.excludes),
        "metadata": dict(plan.metadata),
    }
