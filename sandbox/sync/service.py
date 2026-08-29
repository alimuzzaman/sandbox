"""Application-independent synchronization orchestration."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from typing import Any, Callable

from .capture import CaptureError, capture_manifest
from .coordinator import RelationshipCoordinator
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
                 *, identity_resolver: Callable = _default_identity_resolver,
                 coordinator: RelationshipCoordinator | None = None):
        self.repository = repository
        self.transport_factory = transport_factory
        self.identity_resolver = identity_resolver
        self.coordinator = coordinator or RelationshipCoordinator(repository)

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
        owner = self.repository.find_workspace_owner(remote, workspace_id)
        if owner is not None and owner.project_identity != project_identity:
            raise SyncServiceError(
                "selected workspace is already owned by another project identity",
                "ownership_conflict",
            )
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

    @staticmethod
    def _accepted_transport_result(result: Any, generation, manifest) -> bool:
        return (
            isinstance(result, dict)
            and result.get("status") == "accepted"
            and result.get("accepted_generation") == generation.generation_id
            and result.get("manifest_digest") == manifest.manifest_digest
            and result.get("file_count") == manifest.file_count
            and result.get("byte_count") == manifest.byte_count
        )

    def once(self, project_dir: str | Path, *, remote: str, workspace_id: str,
             request_id: str, explicit_includes: tuple[str, ...] = (),
             checkpoint: bool = False, participant_id: str | None = None) -> dict:
        relationship = self._relationship(project_dir, remote, workspace_id)
        if participant_id is not None:
            self.coordinator.participant(relationship.relationship_id, participant_id)
        # A checkpoint is an explicit request marker. It deliberately does not
        # change the persistent mode and follows the same screened transfer.
        _ = checkpoint
        try:
            manifest = capture_manifest(project_dir, explicit_includes=explicit_includes)
        except CredentialDetected:
            self.repository.record_metrics(
                relationship.relationship_id, outcome="refused",
                file_count=0, byte_count=0,
            )
            return failure_envelope(
                code="credential_detected", status="refused",
                relationship_id=relationship.relationship_id, remote_name=remote,
                request_id=request_id, accepted_generation=relationship.accepted_generation_id,
                pending_generation=relationship.pending_generation_id, retryable=False,
            )
        except CaptureError as exc:
            code = "unstable_capture" if getattr(exc, "code", "") == "unstable_capture" else "remote_unavailable"
            self.repository.record_metrics(
                relationship.relationship_id, outcome="failed",
                file_count=0, byte_count=0,
            )
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
        if generation.lifecycle == "accepted":
            return success_envelope(
                self.repository.get_relationship(relationship.relationship_id) or relationship,
                generation, status="accepted", active_generation=generation.generation_id,
            )
        if replay and generation.lifecycle != "pending":
            return failure_envelope(
                code="transport_unknown", status="unknown",
                relationship_id=relationship.relationship_id, remote_name=remote,
                request_id=request_id,
                accepted_generation=relationship.accepted_generation_id,
                pending_generation=generation.generation_id, retryable=False,
            )
        generation, claimed = self.repository.claim_generation_transfer(
            generation.generation_id)
        if not claimed:
            return failure_envelope(
                code="transport_unknown", status="unknown",
                relationship_id=relationship.relationship_id, remote_name=remote,
                request_id=request_id,
                accepted_generation=relationship.accepted_generation_id,
                pending_generation=generation.generation_id, retryable=False,
            )
        try:
            transport_result = self.transport_factory().transfer(
                project_dir, manifest,
                self.repository.get_relationship(relationship.relationship_id) or relationship,
                generation,
            )
            if not self._accepted_transport_result(transport_result, generation, manifest):
                raise SyncServiceError(
                    "remote acceptance is incomplete", "transport_unknown",
                    retryable=False,
                )
        except Exception as exc:
            # Keep the pending generation visible for a safe replay. The public
            # envelope intentionally uses only the stable error code.
            code = getattr(exc, "code", "remote_unavailable")
            if code not in {"remote_unavailable", "transport_unknown", "unstable_capture"}:
                code = "transport_unknown"
            status = "unknown" if code == "transport_unknown" else "failed"
            self.repository.record_metrics(
                relationship.relationship_id,
                outcome="unknown" if status == "unknown" else "failed",
                file_count=generation.file_count, byte_count=generation.byte_count,
            )
            return failure_envelope(
                code=code, status=status, relationship_id=relationship.relationship_id,
                remote_name=remote, request_id=request_id,
                accepted_generation=relationship.accepted_generation_id,
                pending_generation=generation.generation_id,
                retryable=(False if code == "transport_unknown" else
                           bool(getattr(exc, "retryable", True))),
            )
        accepted = self.repository.transition_generation(
            generation.generation_id, "accepted", accepted_at=utc_now(),
        )
        self.repository.record_metrics(
            relationship.relationship_id, outcome="accepted",
            file_count=accepted.file_count, byte_count=accepted.byte_count,
        )
        current = self.repository.get_relationship(relationship.relationship_id) or relationship
        return success_envelope(current, accepted, status="accepted", active_generation=accepted.generation_id)

    def status(self, project_dir: str | Path, *, remote: str, workspace_id: str) -> dict:
        relationship = self._relationship(project_dir, remote, workspace_id, create=False)
        generation_id = relationship.pending_generation_id or relationship.accepted_generation_id
        generation = self.repository.get_generation(generation_id) if generation_id else None
        status = ("pending" if relationship.pending_generation_id else
                  "accepted" if relationship.accepted_generation_id else
                  "pending" if relationship.lifecycle == "active" else "stopped")
        return success_envelope(
            relationship, generation, status=status,
            active_generation=relationship.accepted_generation_id,
        )

    def start(self, project_dir: str | Path, *, remote: str, workspace_id: str,
              mode: str, participant_id: str | None = None) -> dict:
        if mode not in {"live", "checkpoint"}:
            raise SyncServiceError("synchronization mode is invalid", "ownership_conflict")
        relationship = self._relationship(project_dir, remote, workspace_id)
        updated = self.repository.set_mode(
            relationship.relationship_id, mode, lifecycle="active",
        )
        if participant_id is not None:
            self.coordinator.participant(updated.relationship_id, participant_id)
        generation_id = updated.pending_generation_id or updated.accepted_generation_id
        generation = self.repository.get_generation(generation_id) if generation_id else None
        return success_envelope(updated, generation,
                                status=("pending" if updated.pending_generation_id else
                                        "accepted" if updated.accepted_generation_id else "pending"),
                                active_generation=updated.accepted_generation_id)

    def stop(self, project_dir: str | Path, *, remote: str, workspace_id: str,
             participant_id: str | None = None) -> dict:
        relationship = self._relationship(project_dir, remote, workspace_id)
        updated = self.repository.set_mode(
            relationship.relationship_id, "off", lifecycle="stopped",
        )
        if participant_id is not None:
            self.coordinator.participant(updated.relationship_id, participant_id)
        generation_id = updated.pending_generation_id or updated.accepted_generation_id
        generation = self.repository.get_generation(generation_id) if generation_id else None
        return success_envelope(updated, generation,
                                status=("pending" if updated.pending_generation_id else
                                        "accepted" if updated.accepted_generation_id else "stopped"),
                                active_generation=updated.accepted_generation_id)

    def notify_commit(self, project_dir: str | Path, *, remote: str,
                      workspace_id: str, commit: str,
                      participant_id: str | None = None) -> bool:
        """Queue a live-only commit trigger without blocking Git success."""
        if not isinstance(commit, str) or len(commit) != 40 or any(
                character not in "0123456789abcdef" for character in commit):
            return False
        try:
            relationship = self._relationship(
                project_dir, remote, workspace_id, create=False,
            )
            if relationship.mode != "live" or relationship.lifecycle != "active":
                return False
            if participant_id is not None:
                self.coordinator.participant(relationship.relationship_id, participant_id)
            request_id = f"commit-{commit}"
            return self.coordinator.submit(
                relationship.relationship_id, request_id,
                lambda: self.once(
                    project_dir, remote=remote, workspace_id=workspace_id,
                    request_id=request_id, participant_id=participant_id,
                ),
            )
        except Exception:
            return False

    def reconcile(self, project_dir: str | Path, *, remote: str,
                  workspace_id: str) -> dict:
        """Read one uncertain remote acceptance using its original identity."""
        relationship = self._relationship(
            project_dir, remote, workspace_id, create=False,
        )
        pending_id = relationship.pending_generation_id
        generation = self.repository.get_generation(pending_id) if pending_id else None
        if generation is None or generation.lifecycle != "transferring":
            return self.status(project_dir, remote=remote, workspace_id=workspace_id)
        transport = self.transport_factory()
        reconcile = getattr(transport, "reconcile", None)
        if not callable(reconcile):
            return failure_envelope(
                code="transport_unknown", status="unknown",
                relationship_id=relationship.relationship_id, remote_name=remote,
                request_id=generation.request_id,
                accepted_generation=relationship.accepted_generation_id,
                pending_generation=generation.generation_id, retryable=False,
            )
        try:
            result = reconcile(relationship, generation)
        except Exception:
            result = {"status": "unknown"}
        if (isinstance(result, dict) and result.get("status") == "accepted" and
                result.get("accepted_generation") == generation.generation_id and
                result.get("manifest_digest") == generation.manifest_digest and
                result.get("file_count") == generation.file_count and
                result.get("byte_count") == generation.byte_count):
            accepted = self.repository.transition_generation(
                generation.generation_id, "accepted", accepted_at=utc_now(),
            )
            self.repository.record_metrics(
                relationship.relationship_id, outcome="accepted",
                file_count=accepted.file_count, byte_count=accepted.byte_count,
            )
            current = self.repository.get_relationship(relationship.relationship_id) or relationship
            return success_envelope(
                current, accepted, status="accepted",
                active_generation=accepted.generation_id,
            )
        self.repository.record_metrics(
            relationship.relationship_id, outcome="unknown",
            file_count=generation.file_count, byte_count=generation.byte_count,
        )
        return failure_envelope(
            code="transport_unknown", status="unknown",
            relationship_id=relationship.relationship_id, remote_name=remote,
            request_id=generation.request_id,
            accepted_generation=relationship.accepted_generation_id,
            pending_generation=generation.generation_id, retryable=False,
        )

    def resolve(self, project_dir: str | Path, *, remote: str,
                workspace_id: str, resolution: str, confirm: bool,
                participant_id: str | None = None) -> dict:
        """Clear divergence only after an explicit non-takeover decision."""
        if confirm is not True:
            raise SyncServiceError(
                "divergence resolution requires explicit confirmation",
                "divergence",
            )
        if resolution not in {"keep-local", "stop"}:
            raise SyncServiceError("divergence resolution is invalid", "divergence")
        relationship = self._relationship(project_dir, remote, workspace_id)
        if self.repository.get_divergence(relationship.relationship_id) is None:
            raise SyncServiceError("no divergence is recorded", "divergence")
        if participant_id is not None:
            self.coordinator.participant(relationship.relationship_id, participant_id)
        # Resolution never overwrites or adopts remote source. It clears the
        # local conflict gate and leaves the relationship stopped so a later
        # explicit request re-runs normal ownership/divergence preflight.
        self.repository.clear_divergence(relationship.relationship_id)
        updated = self.repository.set_mode(
            relationship.relationship_id, "off", lifecycle="stopped",
        )
        generation_id = updated.pending_generation_id or updated.accepted_generation_id
        generation = self.repository.get_generation(generation_id) if generation_id else None
        return success_envelope(
            updated, generation,
            status=("pending" if updated.pending_generation_id else
                    "accepted" if updated.accepted_generation_id else "stopped"),
            active_generation=updated.accepted_generation_id,
        )


__all__ = ["SyncService", "SyncServiceError", "relationship_id"]
