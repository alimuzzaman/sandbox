"""Owned storage authority service mechanisms and publication engine."""

import datetime
import hashlib
import io
import os
import tarfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional

from sandbox.owned_storage.adapters.linux import LinuxFilesystemAdapter, RenameNoReplaceError
from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityOwnedObject,
    CanonicalOperationRequest,
    CleanupOutcome,
    CleanupPhase,
    GenerationBinding,
    ObjectKind,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    OperationType,
    PolicyMode,
    RelationshipCurrentSelection,
)
from sandbox.owned_storage.repository import (
    StorageAuthorityRepository,
    StorageRepositoryConflictError,
)


class OwnedStorageServiceError(Exception):
    """Storage authority service error."""

    def __init__(self, message: str, code: str = "internal_indeterminate"):
        super().__init__(message)
        self.code = code


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class OwnedStorageService:
    def __init__(self, storage_root: Path, repository: StorageAuthorityRepository):
        self.storage_root = Path(storage_root)
        self.repository = repository
        self.adapter = LinuxFilesystemAdapter(self.storage_root)

        self.staging_dir = self.storage_root / "staging"
        self.objects_dir = self.storage_root / "objects"
        self.quarantine_dir = self.storage_root / "quarantine"

        self.adapter.ensure_directory(self.staging_dir, 0o700)
        self.adapter.ensure_directory(self.objects_dir, 0o700)
        self.adapter.ensure_directory(self.quarantine_dir, 0o700)

    def reconcile_startup(self) -> Dict[str, Any]:
        """Reconcile in-flight or interrupted staging and quarantine operations."""
        reconciled_staging = 0
        reconciled_quarantine = 0
        aborted_ops = 0

        # 1. Staging directory reconciliation
        if self.staging_dir.exists():
            for child in list(self.staging_dir.iterdir()):
                if child.is_dir():
                    op = self.repository.get_operation(child.name)
                    if op is None or op.phase != OperationPhase.TERMINAL:
                        self.adapter.remove_tree_beneath(child)
                        try:
                            child.rmdir()
                        except OSError:
                            pass
                        reconciled_staging += 1
                        if op is not None and op.phase != OperationPhase.TERMINAL:
                            self.repository.update_operation_phase(
                                op.operation_id,
                                OperationPhase.TERMINAL,
                                outcome=OperationOutcome.FAILED,
                                reason_code="interrupted_recovery",
                            )
                            aborted_ops += 1

            self.adapter.fsync_directory(self.staging_dir)

        # 2. Quarantine directory reconciliation
        if self.quarantine_dir.exists():
            for child in list(self.quarantine_dir.iterdir()):
                if child.is_dir():
                    clean_id = child.name
                    intent = self.repository.get_cleanup_intent(clean_id)
                    target = child / "target"

                    if intent is not None and intent.phase in (
                        CleanupPhase.QUARANTINED,
                        CleanupPhase.REMOVING,
                        CleanupPhase.FINAL_REMOVE_INTENT,
                    ):
                        if target.exists():
                            self.adapter.remove_tree_beneath(target)
                            try:
                                target.rmdir()
                            except OSError:
                                pass
                        try:
                            child.rmdir()
                        except OSError:
                            pass

                        reconciled_quarantine += 1
                        now = utc_now_iso()
                        self.repository.update_cleanup_intent(
                            clean_id,
                            CleanupPhase.TERMINAL,
                            outcome=CleanupOutcome.COMPLETED,
                            observed_bytes=intent.estimated_bytes or 0,
                            completed_at=now,
                        )
                        with self.repository.connect() as conn:
                            conn.execute(
                                "UPDATE authority_objects SET lifecycle = ?, removed_at = ? WHERE object_id = ?",
                                (ObjectLifecycle.REMOVED.value, now, intent.object_id),
                            )
                    else:
                        if target.exists():
                            self.adapter.remove_tree_beneath(target)
                            try:
                                target.rmdir()
                            except OSError:
                                pass
                        try:
                            child.rmdir()
                        except OSError:
                            pass
                        reconciled_quarantine += 1

            self.adapter.fsync_directory(self.quarantine_dir)

        # 3. Abort uncommitted in-flight operations recorded in DB
        with self.repository.connect() as conn:
            rows = conn.execute(
                "SELECT operation_id FROM canonical_operations WHERE phase != 'terminal'",
            ).fetchall()
            for r in rows:
                self.repository.update_operation_phase(
                    r["operation_id"],
                    OperationPhase.TERMINAL,
                    outcome=OperationOutcome.FAILED,
                    reason_code="interrupted_recovery",
                )
                aborted_ops += 1


        return {
            "ok": True,
            "reconciled_staging_count": reconciled_staging,
            "reconciled_quarantine_count": reconciled_quarantine,
            "aborted_operations": aborted_ops,
        }


    def publish_generation(
        self,
        *,
        remote_identity: str,
        project_identity: str,
        request_id: str,
        request_digest: str,
        authorization_id: str,
        controller_epoch: str,
        sequence: int,
        caller_identity_digest: str,
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
        qualification_admission_id: Optional[str] = None,
        evidence_candidate_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atomically stage, verify, and publish an immutable sync generation."""
        now = utc_now_iso()

        # Check policy and binding
        if qualification_admission_id is None:
            policy = self.repository.get_policy(remote_identity, project_identity)
            if not policy or policy.mode != PolicyMode.FUTURE:
                raise OwnedStorageServiceError("Policy is not future", "policy_not_future")

            if authority_binding_id:
                binding = self.repository.get_adoption_binding(authority_binding_id)
                if not binding or binding.phase != AdoptionBindingPhase.ACTIVE:
                    raise OwnedStorageServiceError("Adoption binding is not active", "adoption_binding_missing")

        operation_id = f"op_pub_{uuid.uuid4().hex[:16]}"
        op = CanonicalOperationRequest(
            operation_id=operation_id,
            operation_type=OperationType.PUBLISH,
            request_id=request_id,
            request_digest=request_digest,
            authorization_id=authorization_id,
            controller_epoch=controller_epoch,
            sequence=sequence,
            caller_identity_digest=caller_identity_digest,
            remote_identity=remote_identity,
            project_identity=project_identity,
            relationship_id=relationship_id,
            workspace_id=workspace_id,
            job_id=None,
            target_object_id=None,
            canonical_evidence_digest=manifest_digest,
            qualification_admission_id=qualification_admission_id,
            evidence_candidate_id=evidence_candidate_id,
            promotion_id=promotion_id,
            authority_binding_id=authority_binding_id,
            phase=OperationPhase.RESERVED,
            outcome=None,
            reason_code=None,
            created_at=now,
            updated_at=now,
        )

        try:
            created, existing_op = self.repository.reserve_operation(op)
        except StorageRepositoryConflictError as exc:
            raise OwnedStorageServiceError(str(exc), "request_id_conflict") from exc

        if not created:
            # Replay of existing operation
            if existing_op.outcome == OperationOutcome.ACCEPTED:
                # Fetch existing object
                existing_curr = self.repository.get_current_selection(relationship_id)
                obj_id = existing_curr.object_id if existing_curr else f"obj_{generation_id}"
                obj = self.repository.get_object(obj_id)
                return {
                    "ok": True,
                    "protocol": "owned-storage-authority-v1",
                    "operation": "publish",
                    "operation_id": existing_op.operation_id,
                    "request_id": request_id,
                    "status": "accepted",
                    "object": {
                        "id": obj.object_id if obj else obj_id,
                        "kind": "sync_generation",
                        "lifecycle": "accepted",
                        "evidence_digest": manifest_digest,
                        "known_bytes": byte_count,
                    },
                    "replay": True,
                    "complete": True,
                    "reason_code": None,
                    "observed_at": existing_op.updated_at,
                }
            raise OwnedStorageServiceError(
                f"Operation in non-accepted phase: {existing_op.phase}", "internal_indeterminate"
            )

        # Stage and verify payload
        staging_dir = self.staging_dir / operation_id
        self.adapter.ensure_directory(staging_dir, 0o700)

        stream_bytes = stream.read()
        calculated_archive_digest = f"sha256:{hashlib.sha256(stream_bytes).hexdigest()}"
        if archive_manifest_digest and calculated_archive_digest != archive_manifest_digest:
            self.repository.update_operation_phase(
                operation_id, OperationPhase.TERMINAL, utc_now_iso(),
                outcome=OperationOutcome.REFUSED, reason_code="generation_binding_mismatch"
            )
            raise OwnedStorageServiceError(
                f"Archive digest mismatch: expected {archive_manifest_digest}, got {calculated_archive_digest}",
                "generation_binding_mismatch",
            )

        extracted_file_count = 0
        extracted_byte_count = 0

        try:
            if stream_bytes:
                with tarfile.open(fileobj=io.BytesIO(stream_bytes), mode="r:*") as tar:
                    for member in tar.getmembers():
                        if member.isreg():
                            extracted_file_count += 1
                            extracted_byte_count += member.size
                            # Safe extraction beneath staging_dir
                            member_target = (staging_dir / member.name).resolve()
                            if not str(member_target).startswith(str(staging_dir.resolve())):
                                raise OwnedStorageServiceError("Archive member path traversal", "request_invalid")
                            member_target.parent.mkdir(parents=True, exist_ok=True)
                            f = tar.extractfile(member)
                            if f:
                                member_bytes = f.read()
                                self.adapter.write_file_bytes(member_target, member_bytes, 0o600)
        except tarfile.TarError as exc:
            self.repository.update_operation_phase(
                operation_id, OperationPhase.TERMINAL, utc_now_iso(),
                outcome=OperationOutcome.FAILED, reason_code="unstable_capture"
            )
            raise OwnedStorageServiceError(f"Failed to extract tar archive: {exc}", "unstable_capture") from exc

        if file_count != extracted_file_count or byte_count != extracted_byte_count:
            self.repository.update_operation_phase(
                operation_id, OperationPhase.TERMINAL, utc_now_iso(),
                outcome=OperationOutcome.REFUSED, reason_code="generation_binding_mismatch"
            )
            raise OwnedStorageServiceError(
                f"Counts mismatch: declared file_count={file_count}, byte_count={byte_count} vs extracted file_count={extracted_file_count}, byte_count={extracted_byte_count}",
                "generation_binding_mismatch",
            )

        # Flush payload and staging directory
        self.adapter.fsync_directory(staging_dir)

        # Record effect intent
        self.repository.update_operation_phase(operation_id, OperationPhase.EFFECT_INTENT, utc_now_iso())

        # Atomic move to destination
        dest_parent = self.objects_dir / project_identity / relationship_id
        self.adapter.ensure_directory(dest_parent, 0o700)
        dest_dir = dest_parent / generation_id

        try:
            self.adapter.rename_noreplace(staging_dir, dest_dir)
        except RenameNoReplaceError as exc:
            self.repository.update_operation_phase(
                operation_id, OperationPhase.TERMINAL, utc_now_iso(),
                outcome=OperationOutcome.REFUSED, reason_code="generation_already_exists"
            )
            raise OwnedStorageServiceError(f"Generation destination already exists: {dest_dir}", "generation_already_exists") from exc

        # Flush destination parent directory
        self.adapter.fsync_directory(dest_parent)

        # Create AuthorityOwnedObject
        object_id = f"obj_{generation_id}"
        filesystem_id = self.adapter.stat_identity(dest_dir)
        accepted_time = utc_now_iso()

        binding_evidence = GenerationBinding(
            remote_identity=remote_identity,
            project_identity=project_identity,
            relationship_id=relationship_id,
            workspace_id=workspace_id,
            request_id=request_id,
            generation_id=generation_id,
            manifest_digest=manifest_digest,
            archive_manifest_digest=archive_manifest_digest,
            file_count=file_count,
            byte_count=byte_count,
            accepted_at=accepted_time,
        )

        obj = AuthorityOwnedObject(
            object_id=object_id,
            object_kind=ObjectKind.SYNC_GENERATION,
            remote_identity=remote_identity,
            project_identity=project_identity,
            relationship_id=relationship_id,
            workspace_id=workspace_id,
            job_id=None,
            parent_object_id=None,
            created_by_operation_id=operation_id,
            lifecycle=ObjectLifecycle.ACCEPTED,
            policy_id=promotion_id,
            policy_generation=1,
            qualification_admission_id=qualification_admission_id,
            evidence_candidate_id=evidence_candidate_id,
            promotion_id=promotion_id,
            evidence_id=None,
            authority_binding_id=authority_binding_id,
            retention_policy_digest=manifest_digest,
            content_evidence=binding_evidence.__dict__,
            filesystem_identity=filesystem_id,
            known_bytes=byte_count,
            created_at=now,
            accepted_at=accepted_time,
            removed_at=None,
        )
        self.repository.save_object(obj)

        # Advance current selection
        curr = self.repository.get_current_selection(relationship_id)
        next_selection_generation = (curr.selection_generation + 1) if curr else 1
        new_sel = RelationshipCurrentSelection(
            relationship_id=relationship_id,
            object_id=object_id,
            generation_id=generation_id,
            selection_generation=next_selection_generation,
            operation_id=operation_id,
            changed_at=accepted_time,
        )
        self.repository.set_current_selection(new_sel)

        # Commit terminal outcome
        self.repository.update_operation_phase(
            operation_id,
            OperationPhase.TERMINAL,
            accepted_time,
            outcome=OperationOutcome.ACCEPTED,
        )

        return {
            "ok": True,
            "protocol": "owned-storage-authority-v1",
            "operation": "publish",
            "operation_id": operation_id,
            "request_id": request_id,
            "status": "accepted",
            "object": {
                "id": object_id,
                "kind": "sync_generation",
                "lifecycle": "accepted",
                "evidence_digest": manifest_digest,
                "known_bytes": byte_count,
            },
            "replay": False,
            "complete": True,
            "reason_code": None,
            "observed_at": accepted_time,
        }
