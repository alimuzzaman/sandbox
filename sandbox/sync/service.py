"""Application-independent synchronization orchestration."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from typing import Any, Callable

from .capture import CaptureError, capture_manifest
from .models import (
    SynchronizationRelationship,
    failure_envelope,
    success_envelope,
    utc_now,
)
from .policy import CredentialDetected
from .repository import SyncRepository


class SyncServiceError(RuntimeError):
    def __init__(self, message: str, code: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def relationship_id(project_identity: str, remote_name: str, workspace_id: str) -> str:
    digest = hashlib.sha256(
        f"{project_identity}\0{remote_name}\0{workspace_id}".encode("utf-8")
    ).hexdigest()
    return f"rel_{digest}"


def _default_identity_resolver(project_dir: str | Path, *, remote: str) -> dict:
    # The application composition root owns the registered config loader; the
    # pure config facade intentionally refuses to discover it implicitly.
    from sandbox.application.context import resolve_project_identity
    return resolve_project_identity(project_dir, remote=remote)


class SyncService:
    """Coordinate source capture, local journal state, and one remote transfer."""

    def __init__(self, repository: SyncRepository, transport_factory: Callable[[], Any],
                 *, identity_resolver: Callable = _default_identity_resolver):
        self.repository = repository
        self.transport_factory = transport_factory
        self.identity_resolver = identity_resolver

    def _relationship(self, project_dir: str | Path, remote: str, workspace_id: str,
                      *, mode: str = "off", lifecycle: str = "stopped",
                      create: bool = True) -> SynchronizationRelationship:
        identity = self.identity_resolver(project_dir, remote=remote)
        project_identity = identity.get("identity")
        if not isinstance(project_identity, str) or not project_identity:
            raise SyncServiceError("project identity is unavailable", "ownership_conflict")
        rel_id = relationship_id(project_identity, remote, workspace_id)
        existing = self.repository.find_relationship(project_identity, remote, workspace_id)
        if existing is not None:
            return existing
        created = SynchronizationRelationship(
            relationship_id=rel_id,
            project_identity=project_identity,
            remote_name=remote,
            workspace_id=workspace_id,
            mode=mode,
            lifecycle=lifecycle,
            updated_at=utc_now(),
        )
        if not create:
            return created
        return self.repository.put_relationship(created)

    @staticmethod
    def _request_digest(request_id: str, manifest) -> str:
        return SyncRepository.canonical_request_digest({
            "request_id": request_id,
            "manifest_digest": manifest.manifest_digest,
            "generation_id": manifest.generation_id,
        })

    def once(self, project_dir: str | Path, *, remote: str, workspace_id: str,
             request_id: str, explicit_includes: tuple[str, ...] = ()) -> dict:
        relationship = self._relationship(project_dir, remote, workspace_id)
        try:
            manifest = capture_manifest(project_dir, explicit_includes=explicit_includes)
        except CredentialDetected:
            return failure_envelope(
                code="credential_detected", status="refused",
                relationship_id=relationship.relationship_id, remote_name=remote,
                request_id=request_id, accepted_generation=relationship.accepted_generation_id,
                pending_generation=relationship.pending_generation_id, retryable=False,
            )
        except CaptureError as exc:
            code = "unstable_capture" if getattr(exc, "code", "") == "unstable_capture" else "remote_unavailable"
            return failure_envelope(
                code=code, status="failed", relationship_id=relationship.relationship_id,
                remote_name=remote, request_id=request_id,
                accepted_generation=relationship.accepted_generation_id,
                pending_generation=relationship.pending_generation_id,
                retryable=code == "unstable_capture",
            )
        digest = self._request_digest(request_id, manifest)
        generation, replay = self.repository.reserve_generation(
            relationship_id=relationship.relationship_id,
            request_id=request_id, request_digest=digest,
            manifest_digest=manifest.manifest_digest,
            file_count=manifest.file_count, byte_count=manifest.byte_count,
            commit=manifest.commit, dirty_digest=manifest.dirty_digest,
        )
        if replay and generation.lifecycle == "accepted":
            return success_envelope(
                self.repository.get_relationship(relationship.relationship_id) or relationship,
                generation, status="accepted", active_generation=generation.generation_id,
            )
        if generation.lifecycle == "accepted":
            return success_envelope(
                self.repository.get_relationship(relationship.relationship_id) or relationship,
                generation, status="accepted", active_generation=generation.generation_id,
            )
        self.repository.transition_generation(generation.generation_id, "transferring")
        try:
            self.transport_factory().transfer(
                project_dir, manifest,
                self.repository.get_relationship(relationship.relationship_id) or relationship,
                generation,
            )
        except Exception as exc:
            # Keep the pending generation visible for a safe replay. The public
            # envelope intentionally uses only the stable error code.
            code = getattr(exc, "code", "remote_unavailable")
            if code not in {"remote_unavailable", "transport_unknown", "unstable_capture"}:
                code = "transport_unknown"
            status = "unknown" if code == "transport_unknown" else "failed"
            return failure_envelope(
                code=code, status=status, relationship_id=relationship.relationship_id,
                remote_name=remote, request_id=request_id,
                accepted_generation=relationship.accepted_generation_id,
                pending_generation=generation.generation_id,
                retryable=bool(getattr(exc, "retryable", True)),
            )
        accepted = self.repository.transition_generation(
            generation.generation_id, "accepted", accepted_at=utc_now(),
        )
        current = self.repository.get_relationship(relationship.relationship_id) or relationship
        return success_envelope(current, accepted, status="accepted", active_generation=accepted.generation_id)

    def status(self, project_dir: str | Path, *, remote: str, workspace_id: str) -> dict:
        relationship = self._relationship(project_dir, remote, workspace_id, create=False)
        generation_id = relationship.accepted_generation_id or relationship.pending_generation_id
        generation = self.repository.get_generation(generation_id) if generation_id else None
        status = "accepted" if relationship.accepted_generation_id else (
            "pending" if relationship.pending_generation_id or relationship.lifecycle == "active"
            else "stopped"
        )
        return success_envelope(
            relationship, generation, status=status,
            active_generation=relationship.accepted_generation_id,
        )

    def start(self, project_dir: str | Path, *, remote: str, workspace_id: str,
              mode: str) -> dict:
        if mode not in {"live", "checkpoint"}:
            raise SyncServiceError("synchronization mode is invalid", "ownership_conflict")
        relationship = self._relationship(project_dir, remote, workspace_id)
        updated = self.repository.put_relationship(replace(
            relationship, mode=mode, lifecycle="active", updated_at=utc_now(),
        ))
        generation = self.repository.get_generation(updated.accepted_generation_id) if updated.accepted_generation_id else None
        return success_envelope(updated, generation, status=("accepted" if generation else "pending"),
                                active_generation=updated.accepted_generation_id)

    def stop(self, project_dir: str | Path, *, remote: str, workspace_id: str) -> dict:
        relationship = self._relationship(project_dir, remote, workspace_id)
        updated = self.repository.put_relationship(replace(
            relationship, mode="off", lifecycle="stopped", updated_at=utc_now(),
        ))
        generation = self.repository.get_generation(updated.accepted_generation_id) if updated.accepted_generation_id else None
        return success_envelope(updated, generation, status=("accepted" if generation else "stopped"),
                                active_generation=updated.accepted_generation_id)


__all__ = ["SyncService", "SyncServiceError", "relationship_id"]
