from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Protocol, Sequence


class ArtifactStore(Protocol):
    def put(self, artifact: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def list(self) -> Sequence[Mapping[str, Any]]: ...
    def read(self, artifact_id: str) -> Mapping[str, Any]: ...


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


class HermesBackupService:
    """Non-destructive artifact ownership boundary backed by injected storage."""
    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def create(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(artifact)
        if not value.get("id"):
            raise ValueError("backup artifact id is required")
        self._validate_digest(value)
        return dict(self.store.put(value))

    def list(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self.store.list())

    def verify(self, artifact: Mapping[str, Any]) -> bool:
        artifact_id = str(artifact.get("id") or "")
        if not artifact_id:
            return False
        try:
            stored = self.store.read(artifact_id)
            self._validate_digest(stored)
        except (KeyError, ValueError):
            return False
        return stored.get("sha256", "").lower() == str(artifact.get("sha256", "")).lower()

    @staticmethod
    def _validate_digest(artifact: Mapping[str, Any]) -> None:
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("backup artifact sha256 must be a 64-character hexadecimal digest")


def retention_candidates(artifacts: Sequence[Mapping[str, Any]], *, keep: int) -> tuple[Mapping[str, Any], ...]:
    """Return oldest excess artifacts only; this hook performs no deletion."""
    if keep < 0:
        raise ValueError("retention keep count must not be negative")
    ordered = sorted(artifacts, key=lambda artifact: str(artifact.get("created_at") or ""), reverse=True)
    return tuple(ordered[keep:])


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
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise ValueError("backup artifact sha256 must be a 64-character hexadecimal digest")
        actions.append({
            "artifact_id": artifact["id"],
            "archive": artifact.get("archive"),
            "target": artifact.get("restore_target"),
            "verify_sha256": digest.lower(),
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
