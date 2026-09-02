"""Path-free policy for generation-pinned job source projections."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .models import (
    DivergenceRecord,
    SourceGeneration,
    SynchronizationRelationship,
    utc_now,
    validate_identifier,
)


class ProjectionRefused(RuntimeError):
    """A bounded source-projection refusal safe for public policy handling."""

    def __init__(self, code: str) -> None:
        self.code = validate_identifier(code, "projection refusal code")
        super().__init__(code)


@dataclass(frozen=True)
class ProjectionDecision:
    relationship_id: str
    generation_id: str
    source_access: str
    read_only: bool
    isolated: bool
    artifact_only_output: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "relationship_id": self.relationship_id,
            "generation_id": self.generation_id,
            "source_access": self.source_access,
            "read_only": self.read_only,
            "isolated": self.isolated,
            "artifact_only_output": self.artifact_only_output,
        }


def authorize_projection(
    relationship: SynchronizationRelationship,
    generation: SourceGeneration,
    *,
    requested_generation_id: str,
    source_access: str,
    divergence: DivergenceRecord | None = None,
) -> ProjectionDecision:
    """Authorize one immutable generation without returning a source path."""
    validate_identifier(requested_generation_id, "requested generation id")
    if relationship.lifecycle == "diverged" or divergence is not None:
        raise ProjectionRefused("divergence")
    newest = relationship.pending_generation_id or relationship.accepted_generation_id
    if newest is None or requested_generation_id != newest:
        raise ProjectionRefused("newest_generation_required")
    if relationship.pending_generation_id is not None:
        raise ProjectionRefused("generation_pending")
    if (
        generation.relationship_id != relationship.relationship_id
        or generation.generation_id != requested_generation_id
        or generation.lifecycle != "accepted"
        or relationship.accepted_generation_id != generation.generation_id
    ):
        raise ProjectionRefused("generation_not_accepted")
    if source_access not in {"managed_read_only", "isolated_copy"}:
        raise ProjectionRefused("source_access_invalid")
    isolated = source_access == "isolated_copy"
    return ProjectionDecision(
        relationship_id=relationship.relationship_id,
        generation_id=generation.generation_id,
        source_access=source_access,
        read_only=not isolated,
        isolated=isolated,
        artifact_only_output=isolated,
    )


def validate_isolated_outputs(paths: Iterable[str]) -> tuple[str, ...]:
    """Keep isolated writes inside the declared artifact/output boundary."""
    result: list[str] = []
    for value in paths:
        if not isinstance(value, str) or not value or len(value.encode()) > 1024:
            raise ProjectionRefused("artifact_path_invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value in {".", "./"}:
            raise ProjectionRefused("artifact_path_invalid")
        normalized = path.as_posix()
        if normalized not in result:
            result.append(normalized)
    if not result:
        raise ProjectionRefused("isolated_output_required")
    return tuple(result)


def detect_divergence(
    relationship: SynchronizationRelationship,
    generation: SourceGeneration,
    *,
    observed_manifest_digest: str,
    affected_count: int,
) -> DivergenceRecord | None:
    """Return bounded evidence when managed source no longer matches its pin."""
    if observed_manifest_digest == generation.manifest_digest:
        return None
    if isinstance(affected_count, bool) or not isinstance(affected_count, int):
        raise ProjectionRefused("divergence_evidence_invalid")
    return DivergenceRecord(
        relationship_id=relationship.relationship_id,
        affected_count=affected_count,
        comparison_generation_id=generation.generation_id,
        detected_at=utc_now(),
        resolution_code="explicit_resolution_required",
    )


class SyncJobGateway:
    """Bind a job submission to one locally accepted generation before launch."""

    def __init__(self, repository, *, materialize: Callable) -> None:
        self.repository = repository
        self.materialize = materialize

    def prepare_submission(self, submission):
        relationship = self.repository.get_relationship(
            submission.sync_relationship_id)
        generation = self.repository.get_generation(
            submission.sync_generation_id)
        if relationship is None or generation is None:
            raise ProjectionRefused("generation_not_found")
        if relationship.project_identity != submission.project_identity:
            raise ProjectionRefused("ownership_conflict")
        decision = authorize_projection(
            relationship, generation,
            requested_generation_id=submission.sync_generation_id,
            source_access=submission.source_access,
            divergence=self.repository.get_divergence(
                relationship.relationship_id),
        )
        if decision.isolated:
            validate_isolated_outputs(submission.artifact_paths)
        prepared = self.materialize(decision, submission)
        if not isinstance(prepared, dict):
            raise ProjectionRefused("projection_unavailable")
        project_root = prepared.get("project_root")
        if (
            not isinstance(project_root, str)
            or not Path(project_root).is_absolute()
            or ".." in Path(project_root).parts
        ):
            raise ProjectionRefused("projection_unavailable")
        source_identity = prepared.get("source_identity")
        if not isinstance(source_identity, str) or not source_identity:
            raise ProjectionRefused("projection_unavailable")
        source = type(submission.source)(
            source_identity, generation.commit, generation.dirty_digest,
        )
        return replace(
            submission,
            project_root=project_root,
            source=source,
            workspace_mode=("isolated" if decision.isolated
                            else submission.workspace_mode),
        )


__all__ = [
    "ProjectionDecision",
    "ProjectionRefused",
    "SyncJobGateway",
    "authorize_projection",
    "detect_divergence",
    "validate_isolated_outputs",
]
