"""Application-level owned storage authority port and authorization verification."""

from __future__ import annotations

import datetime
import hashlib
import uuid
from typing import Any, BinaryIO, Dict, Optional

from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityPolicy,
    PolicyMode,
)
from sandbox.owned_storage.protocol import compute_request_digest
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import (
    OwnedStorageService,
    OwnedStorageServiceError,
    utc_now_iso,
)


def build_owned_storage_application_service(
    storage_root: Optional[Path | str] = None,
    repository: Optional[StorageAuthorityRepository] = None,
) -> OwnedStorageApplicationService:
    import os
    from pathlib import Path
    if storage_root is None:
        root_env = os.environ.get("SANDBOX_STORAGE_ROOT")
        if root_env:
            storage_root = Path(root_env)
        else:
            home = Path(os.environ.get("SANDBOX_HOME", Path.home() / "sandbox"))
            storage_root = home / "owned_storage"
    else:
        storage_root = Path(storage_root)

    storage_root.mkdir(parents=True, exist_ok=True)
    if repository is None:
        db_path = storage_root / "authority.db"
        repository = StorageAuthorityRepository(db_path)

    authority_service = OwnedStorageService(storage_root, repository)
    return OwnedStorageApplicationService(authority_service, repository)


class OwnedStorageApplicationError(Exception):
    """Application-level owned storage port error."""

    def __init__(self, message: str, code: str = "request_invalid"):
        super().__init__(f"[{code}] {message}")
        self.code = code


class OwnedStorageApplicationService:
    def __init__(
        self,
        authority_service: OwnedStorageService,
        repository: StorageAuthorityRepository,
    ):
        self.authority_service = authority_service
        self.repository = repository

    def publish(
        self,
        *,
        remote_identity: str,
        project_identity: str,
        request_id: str,
        relationship_id: str,
        workspace_id: str,
        generation_id: str,
        manifest_digest: str,
        archive_manifest_digest: str,
        file_count: int,
        byte_count: int,
        stream: BinaryIO,
        promotion_id: Optional[str] = None,
        authority_binding_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Verify application authorization and invoke owned storage authority publication."""
        if not project_identity or not isinstance(project_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing project identity", code="cross_project_refused"
            )
        if not remote_identity or not isinstance(remote_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing remote identity", code="request_invalid"
            )

        policy = self.repository.get_policy(remote_identity, project_identity)
        if not policy or policy.mode != PolicyMode.FUTURE:
            raise OwnedStorageApplicationError(
                "Policy is not future for this project and remote", code="policy_not_future"
            )

        # Look up active binding if not explicitly passed
        binding_id = authority_binding_id
        if not binding_id and policy.admission_basis:
            binding_id = policy.admission_basis.get("binding_id")

        binding = None
        if binding_id:
            binding = self.repository.get_adoption_binding(binding_id)
            if not binding or binding.phase != AdoptionBindingPhase.ACTIVE:
                raise OwnedStorageApplicationError(
                    "Adoption binding is not active", code="adoption_binding_missing"
                )

        # Construct canonical authorization fields
        auth_seed = f"{request_id}:{remote_identity}:{project_identity}"
        authorization_id = f"auth_{hashlib.sha256((auth_seed + ':auth').encode('utf-8')).hexdigest()[:12]}"
        controller_epoch = f"epoch_{hashlib.sha256((remote_identity + ':epoch').encode('utf-8')).hexdigest()[:8]}"
        sequence = 1
        caller_identity_digest = f"sha256:{hashlib.sha256(project_identity.encode('utf-8')).hexdigest()}"
        default_expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)).isoformat()
        expires_at = binding.expires_at if (binding and binding.expires_at) else default_expires

        req_dict = {
            "protocol": "owned-storage-authority-v1",
            "operation": "publish",
            "request_id": request_id,
            "remote_identity": remote_identity,
            "project_identity": project_identity,
            "authorization": {
                "authorization_id": authorization_id,
                "controller_epoch": controller_epoch,
                "sequence": sequence,
                "caller_identity_digest": caller_identity_digest,
                "application_policy_digest": policy.request_digest,
                "policy_generation": policy.effective_generation,
                "promotion_id": promotion_id,
                "authority_binding_id": binding_id,
                "binding_generation": 1,
                "expires_at": expires_at,
            },
            "qualification": None,
            "input": {
                "relationship_id": relationship_id,
                "workspace_id": workspace_id,
                "generation_id": generation_id,
                "manifest_digest": manifest_digest,
                "archive_manifest_digest": archive_manifest_digest,
                "file_count": file_count,
                "byte_count": byte_count,
            },
        }
        request_digest = compute_request_digest(req_dict)

        try:
            return self.authority_service.publish_generation(
                remote_identity=remote_identity,
                project_identity=project_identity,
                request_id=request_id,
                request_digest=request_digest,
                authorization_id=authorization_id,
                controller_epoch=controller_epoch,
                sequence=sequence,
                caller_identity_digest=caller_identity_digest,
                relationship_id=relationship_id,
                workspace_id=workspace_id,
                generation_id=generation_id,
                manifest_digest=manifest_digest,
                archive_manifest_digest=archive_manifest_digest,
                file_count=file_count,
                byte_count=byte_count,
                stream=stream,
                promotion_id=promotion_id,
                authority_binding_id=binding_id,
            )
        except OwnedStorageServiceError as exc:
            raise OwnedStorageApplicationError(str(exc), code=exc.code) from exc

    def generate_preview(
        self,
        *,
        remote_identity: str,
        project_identity: str,
        kind: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect authority objects and build an immutable 15-minute reclamation preview."""
        import datetime
        from sandbox.owned_storage.models import (
            CandidateDecision,
            ObjectKind,
            ObjectLifecycle,
            PreviewCandidate,
            ReclamationPreview,
        )

        if not project_identity or not isinstance(project_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing project identity", code="cross_project_refused"
            )
        if not remote_identity or not isinstance(remote_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing remote identity", code="request_invalid"
            )

        policy = self.repository.get_policy(remote_identity, project_identity)
        policy_gen = policy.effective_generation if policy else 1

        all_objects = self.repository.get_all_objects_for_scope(
            remote_identity=remote_identity,
            project_identity=project_identity,
        )

        candidates: List[PreviewCandidate] = []
        for obj in all_objects:
            if kind is not None and obj.object_kind.value != kind:
                continue

            decision = CandidateDecision.PROTECTED
            reason_code = "retention_active"

            if obj.lifecycle == ObjectLifecycle.REMOVED:
                decision = CandidateDecision.PROTECTED
                reason_code = "object_unknown"
            elif obj.object_kind == ObjectKind.SYNC_GENERATION:
                curr = (
                    self.repository.get_current_selection(obj.relationship_id)
                    if obj.relationship_id
                    else None
                )
                if curr is not None and curr.object_id == obj.object_id:
                    decision = CandidateDecision.PROTECTED
                    reason_code = "reference_active"
                else:
                    decision = CandidateDecision.ELIGIBLE
                    reason_code = "superseded_unreferenced"
            elif obj.object_kind == ObjectKind.CI_MATERIALIZATION:
                with self.repository.connect() as conn:
                    active_leases = conn.execute(
                        """
                        SELECT lease_id FROM materialization_leases
                        WHERE object_id = ? AND state IN ('reserved', 'active', 'closing')
                        """,
                        (obj.object_id,),
                    ).fetchall()
                if active_leases:
                    decision = CandidateDecision.PROTECTED
                    reason_code = "workspace_lease_active"
                else:
                    decision = CandidateDecision.ELIGIBLE
                    reason_code = "terminal_unreferenced"
            elif obj.object_kind == ObjectKind.RETAINED_ARTIFACT:
                decision = CandidateDecision.ELIGIBLE
                reason_code = "retained_unreferenced"

            object_ev_digest = obj.retention_policy_digest or "sha256:empty"
            ref_digest = f"sha256:{hashlib.sha256((obj.object_id + decision.value).encode('utf-8')).hexdigest()}"

            candidates.append(
                PreviewCandidate(
                    object_id=obj.object_id,
                    object_kind=obj.object_kind,
                    lifecycle=obj.lifecycle,
                    decision=decision,
                    reason_code=reason_code,
                    estimated_bytes=obj.known_bytes,
                    object_evidence_digest=object_ev_digest,
                    reference_snapshot_digest=ref_digest,
                )
            )

        # Calculate estimated reclaimable bytes (sum of known eligible bytes only)
        estimated_reclaimable = sum(
            c.estimated_bytes
            for c in candidates
            if c.decision == CandidateDecision.ELIGIBLE and c.estimated_bytes is not None
        )

        # Candidate digest
        cand_repr = [
            f"{c.object_id}:{c.decision.value}:{c.estimated_bytes or 0}"
            for c in sorted(candidates, key=lambda x: x.object_id)
        ]
        cand_digest = f"sha256:{hashlib.sha256(','.join(cand_repr).encode('utf-8')).hexdigest()}"

        now_dt = datetime.datetime.now(datetime.timezone.utc)
        created_at = now_dt.isoformat()
        expires_at = (now_dt + datetime.timedelta(minutes=15)).isoformat()

        preview_id = f"prev_{uuid.uuid4().hex[:12]}"
        preview = ReclamationPreview(
            preview_id=preview_id,
            remote_identity=remote_identity,
            project_identity=project_identity,
            inventory_generation=1,
            policy_generation=policy_gen,
            candidate_digest=cand_digest,
            candidates=candidates,
            estimated_reclaimable_bytes=estimated_reclaimable,
            complete=True,
            created_at=created_at,
            expires_at=expires_at,
        )

        self.repository.save_preview(preview)

        # Bounded pagination for candidate response
        bounded_limit = min(max(1, limit), 500)
        start_idx = 0
        if cursor:
            try:
                start_idx = int(cursor)
            except ValueError:
                start_idx = 0

        candidates.sort(key=lambda c: c.object_id)
        page_candidates = candidates[start_idx : start_idx + bounded_limit]
        next_cursor = (
            str(start_idx + bounded_limit)
            if (start_idx + bounded_limit) < len(candidates)
            else None
        )

        return {
            "ok": True,
            "protocol": "owned-storage-authority-v1",
            "operation": "preview",
            "preview_id": preview.preview_id,
            "remote_identity": remote_identity,
            "project_identity": project_identity,
            "inventory_generation": preview.inventory_generation,
            "policy_generation": preview.policy_generation,
            "candidate_digest": preview.candidate_digest,
            "candidates": [
                {
                    "object_id": c.object_id,
                    "object_kind": c.object_kind.value,
                    "lifecycle": c.lifecycle.value,
                    "decision": c.decision.value,
                    "reason_code": c.reason_code,
                    "estimated_bytes": c.estimated_bytes,
                    "object_evidence_digest": c.object_evidence_digest,
                    "reference_snapshot_digest": c.reference_snapshot_digest,
                }
                for c in page_candidates
            ],
            "estimated_reclaimable_bytes": preview.estimated_reclaimable_bytes,
            "complete": preview.complete,
            "created_at": preview.created_at,
            "expires_at": preview.expires_at,
            "cursor": next_cursor,
        }

    def get_status(
        self,
        *,
        remote_identity: str,
        project_identity: str,
        kind: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read-only bounded page of authority objects for project and remote."""
        from sandbox.owned_storage.models import ObjectKind

        if not project_identity or not isinstance(project_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing project identity", code="cross_project_refused"
            )
        if not remote_identity or not isinstance(remote_identity, str):
            raise OwnedStorageApplicationError(
                "Invalid or missing remote identity", code="request_invalid"
            )

        k = ObjectKind(kind) if kind else None
        objs, next_cursor = self.repository.query_objects(
            remote_identity=remote_identity,
            project_identity=project_identity,
            kind=k,
            limit=limit,
            cursor=cursor,
        )

        formatted = [
            {
                "object_id": o.object_id,
                "kind": o.object_kind.value,
                "lifecycle": o.lifecycle.value,
                "known_bytes": o.known_bytes,
                "created_at": o.created_at,
                "accepted_at": o.accepted_at,
                "removed_at": o.removed_at,
            }
            for o in objs
        ]

        return {
            "ok": True,
            "protocol": "owned-storage-authority-v1",
            "operation": "status",
            "remote_identity": remote_identity,
            "project_identity": project_identity,
            "objects": formatted,
            "cursor": next_cursor,
        }

    def reclaim(
        self,
        *,
        remote_identity: str,
        project_identity: str,
        preview_id: str,
        object_id: str,
        request_id: str,
        confirm: bool,
    ) -> Dict[str, Any]:
        """Execute safe reclamation of one exact reviewed preview candidate."""
        import datetime
        from sandbox.owned_storage.cleanup import CleanupExecutionError, OwnedStorageCleanupManager
        from sandbox.owned_storage.models import (
            CandidateDecision,
            ObjectKind,
            ObjectLifecycle,
        )

        if not confirm:
            raise OwnedStorageApplicationError(
                "Explicit confirmation is required for reclaim", code="request_invalid"
            )
        if not preview_id or not object_id or not request_id:
            raise OwnedStorageApplicationError(
                "Missing required parameters for reclaim", code="request_invalid"
            )

        preview = self.repository.get_preview(preview_id)
        if preview is None:
            raise OwnedStorageApplicationError(
                f"Preview {preview_id} not found", code="object_not_previewed"
            )

        if (
            preview.remote_identity != remote_identity
            or preview.project_identity != project_identity
        ):
            raise OwnedStorageApplicationError(
                "Preview scope mismatch", code="cross_project_refused"
            )

        now_dt = datetime.datetime.now(datetime.timezone.utc)
        exp_dt = datetime.datetime.fromisoformat(preview.expires_at)
        if now_dt > exp_dt:
            raise OwnedStorageApplicationError(
                f"Preview {preview_id} has expired", code="preview_expired"
            )

        if not preview.complete:
            raise OwnedStorageApplicationError(
                f"Preview {preview_id} is incomplete and cannot execute",
                code="preview_incomplete",
            )

        candidate = next((c for c in preview.candidates if c.object_id == object_id), None)
        if candidate is None:
            raise OwnedStorageApplicationError(
                f"Object {object_id} was not in preview {preview_id}",
                code="object_not_previewed",
            )

        if candidate.decision != CandidateDecision.ELIGIBLE:
            reason = candidate.reason_code or "retention_active"
            raise OwnedStorageApplicationError(
                f"Object {object_id} is protected: {reason}", code=reason
            )

        obj = self.repository.get_object(object_id)
        if obj is None:
            raise OwnedStorageApplicationError(
                f"Object {object_id} unknown", code="object_unknown"
            )

        if obj.lifecycle == ObjectLifecycle.REMOVED:
            return {
                "ok": True,
                "protocol": "owned-storage-authority-v1",
                "operation": "cleanup",
                "object_id": object_id,
                "status": "already_completed",
                "observed_reclaimed_bytes": 0,
                "complete": True,
            }

        # Fresh final checks
        if obj.object_kind == ObjectKind.SYNC_GENERATION:
            curr = (
                self.repository.get_current_selection(obj.relationship_id)
                if obj.relationship_id
                else None
            )
            if curr is not None and curr.object_id == obj.object_id:
                raise OwnedStorageApplicationError(
                    f"Object {object_id} became current selection",
                    code="reference_active",
                )
        elif obj.object_kind == ObjectKind.CI_MATERIALIZATION:
            with self.repository.connect() as conn:
                active_leases = conn.execute(
                    """
                    SELECT lease_id FROM materialization_leases
                    WHERE object_id = ? AND state IN ('reserved', 'active', 'closing')
                    """,
                    (object_id,),
                ).fetchall()
            if active_leases:
                raise OwnedStorageApplicationError(
                    f"Object {object_id} has active lease",
                    code="workspace_lease_active",
                )

        cleanup_mgr = OwnedStorageCleanupManager(
            self.authority_service.storage_root, self.repository
        )
        try:
            return cleanup_mgr.cleanup_object(
                preview_id=preview_id,
                object_id=object_id,
                request_id=request_id,
                confirm=confirm,
                expected_object_evidence_digest=candidate.object_evidence_digest,
                expected_reference_digest=candidate.reference_snapshot_digest,
            )
        except CleanupExecutionError as exc:
            raise OwnedStorageApplicationError(str(exc), code=exc.code) from exc

