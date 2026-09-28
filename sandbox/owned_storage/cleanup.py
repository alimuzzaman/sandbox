"""Quarantine and physical removal state machine for owned storage authority."""

from __future__ import annotations

import hashlib
import os
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sandbox.owned_storage.adapters.linux import (
    FileSystemAdapterError,
    LinuxFilesystemAdapter,
    OpenBeneathError,
    RenameNoReplaceError,
)
from sandbox.owned_storage.models import (
    CanonicalOperationRequest,
    CleanupIntent,
    CleanupOutcome,
    CleanupPhase,
    ObjectKind,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    OperationType,
)
from sandbox.owned_storage.protocol import compute_request_digest
from sandbox.owned_storage.repository import (
    StorageAuthorityRepository,
    StorageRepositoryConflictError,
    StorageRepositoryError,
)


class CleanupExecutionError(Exception):
    """Cleanup execution error with a stable safe code."""

    def __init__(self, message: str, code: str = "cleanup_failed"):
        super().__init__(f"[{code}] {message}")
        self.code = code


class OwnedStorageCleanupManager:
    """Manages identity-bound safe quarantine and physical removal."""

    def __init__(self, storage_root: Path, repository: StorageAuthorityRepository):
        self.storage_root = Path(storage_root)
        self.repository = repository
        self.adapter = LinuxFilesystemAdapter(self.storage_root)
        self.quarantine_dir = self.storage_root / "quarantine"
        self.adapter.ensure_directory_beneath("quarantine", 0o700)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _cleanup_id(operation_id: str) -> str:
        digest = hashlib.sha256(
            b"owned-storage-cleanup-v1:" + operation_id.encode("utf-8")
        ).hexdigest()
        return f"clean_{digest}"

    @staticmethod
    def _relative_parts(value: str) -> tuple[str, ...]:
        path = Path(value)
        if path.is_absolute() or not path.parts or any(part in (".", "..") for part in path.parts):
            raise CleanupExecutionError("Object path is not a safe relative path", "object_identity_invalid")
        return tuple(path.parts)

    def _object_relative_path(self, obj: Any) -> str:
        if obj.object_kind == ObjectKind.CI_MATERIALIZATION:
            parts = ("objects", obj.project_identity, "workspaces", obj.object_id)
        else:
            relationship = obj.relationship_id or "unscoped"
            generation = obj.content_evidence.get("generation_id", obj.object_id)
            parts = ("objects", obj.project_identity, relationship, str(generation))
        for part in parts:
            if len(self._relative_parts(part)) != 1:
                raise CleanupExecutionError("Object identity contains a path separator", "object_identity_invalid")
        return "/".join(parts)

    def _request_digest(
        self,
        *,
        request_id: str,
        remote_identity: str,
        project_identity: str,
        preview_id: str,
        object_id: str,
        confirm: bool,
        expected_object_evidence_digest: str,
        expected_reference_digest: str,
        job_result_digest_before: Optional[str],
        job_result_digest_after: Optional[str],
    ) -> str:
        request_input = {
            "preview_id": preview_id,
            "object_id": object_id,
            "confirm": confirm,
            "expected_object_evidence_digest": expected_object_evidence_digest,
            "expected_reference_digest": expected_reference_digest,
        }
        if job_result_digest_before is not None:
            request_input["job_result_digest_before"] = job_result_digest_before
        if job_result_digest_after is not None:
            request_input["job_result_digest_after"] = job_result_digest_after
        return compute_request_digest(
            {
                "protocol": "owned-storage-authority-v1",
                "operation": "cleanup",
                "request_id": request_id,
                "remote_identity": remote_identity,
                "project_identity": project_identity,
                "authorization": None,
                "qualification": None,
                "input": request_input,
            }
        )

    def _capture_intent_identities(
        self, source_relative_path: str, obj: Any
    ) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        source_parts = self._relative_parts(source_relative_path)
        if len(source_parts) < 2:
            raise CleanupExecutionError("Object path has no parent directory", "object_identity_invalid")
        source_parent_path = "/".join(source_parts[:-1])

        root_fd = self.adapter.open_root_directory()
        try:
            root_identity = self.adapter.identity_from_stat(os.fstat(root_fd))
        finally:
            os.close(root_fd)

        parent_fd = self.adapter.open_directory_beneath(source_parent_path)
        try:
            source_parent_identity = self.adapter.identity_from_stat(os.fstat(parent_fd))
            source_identity = self.adapter.stat_identity_at(parent_fd, source_parts[-1])
        finally:
            os.close(parent_fd)
        if source_identity is None or not stat.S_ISDIR(source_identity["mode"]):
            raise CleanupExecutionError("Object directory does not exist", "object_unknown")

        expected_inode = obj.filesystem_identity.get("inode")
        expected_device = obj.filesystem_identity.get("device")
        if expected_inode is None or expected_device is None:
            raise CleanupExecutionError(
                "Object has no captured filesystem identity", "object_identity_missing"
            )
        if source_identity["inode"] != expected_inode or source_identity["device"] != expected_device:
            raise CleanupExecutionError(
                "Object filesystem identity drifted", "object_identity_drift"
            )

        quarantine_fd = self.adapter.open_directory_beneath("quarantine")
        try:
            quarantine_identity = self.adapter.identity_from_stat(os.fstat(quarantine_fd))
        finally:
            os.close(quarantine_fd)
        return root_identity, source_parent_identity, source_identity, quarantine_identity

    def cleanup_object(
        self,
        *,
        preview_id: str,
        object_id: str,
        request_id: str,
        confirm: bool,
        expected_object_evidence_digest: str,
        expected_reference_digest: str,
        job_result_digest_before: Optional[str] = None,
        job_result_digest_after: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reserve one immutable intent, then resume only from its captured identity."""
        if not confirm:
            raise CleanupExecutionError("Confirmation is required for cleanup", "request_invalid")

        for prior_op in self.repository.get_operations_by_request_id(
            OperationType.CLEANUP, request_id
        ):
            if prior_op.target_object_id != object_id:
                raise CleanupExecutionError(
                    f"Request ID {request_id} was already used for another cleanup object",
                    "request_id_conflict",
                )

        obj = self.repository.get_object(object_id)
        if obj is None:
            raise CleanupExecutionError(f"Object {object_id} unknown", "object_unknown")

        request_digest = self._request_digest(
            request_id=request_id,
            remote_identity=obj.remote_identity,
            project_identity=obj.project_identity,
            preview_id=preview_id,
            object_id=object_id,
            confirm=confirm,
            expected_object_evidence_digest=expected_object_evidence_digest,
            expected_reference_digest=expected_reference_digest,
            job_result_digest_before=job_result_digest_before,
            job_result_digest_after=job_result_digest_after,
        )

        existing_op = self.repository.get_operation_by_request(
            OperationType.CLEANUP,
            request_id,
            obj.remote_identity,
            obj.project_identity,
        )
        if existing_op is not None:
            if existing_op.request_digest != request_digest or existing_op.target_object_id != object_id:
                raise CleanupExecutionError(
                    f"Request ID {request_id} replayed with conflicting cleanup evidence",
                    "request_id_conflict",
                )
            intent = self.repository.get_cleanup_intent_for_operation(existing_op.operation_id)
            if intent is None:
                raise CleanupExecutionError(
                    "Historical cleanup operation has no matching intent; recovery authority is unavailable",
                    "cleanup_intent_missing",
                )
            self._verify_intent_replay(intent, existing_op, request_digest, preview_id, object_id,
                                       expected_object_evidence_digest, expected_reference_digest,
                                       job_result_digest_before, job_result_digest_after)
            if intent.phase == CleanupPhase.TERMINAL:
                if intent.outcome == CleanupOutcome.COMPLETED:
                    if (
                        existing_op.phase != OperationPhase.TERMINAL
                        or existing_op.outcome != OperationOutcome.COMPLETED
                    ):
                        raise CleanupExecutionError(
                            "Cleanup intent and canonical operation have conflicting outcomes",
                            "cleanup_journal_conflict",
                        )
                    return self._result(intent, object_id, "already_completed", intent.observed_reclaimed_bytes)
                raise CleanupExecutionError(
                    intent.reason_code or "Cleanup is terminal without completion",
                    intent.reason_code or "cleanup_terminal",
                )
            if existing_op.phase == OperationPhase.TERMINAL:
                raise CleanupExecutionError(
                    "Cleanup operation is terminal without a recorded completion",
                    "cleanup_terminal",
                )
            if not self._has_recovery_identity(intent):
                self._diagnose(intent, "historical_identity_missing")
                raise CleanupExecutionError(
                    "Historical cleanup intent lacks captured inode evidence and is retained",
                    "historical_identity_missing",
                )
            return self._resume_cleanup(intent, existing_op)

        if obj.lifecycle == ObjectLifecycle.REMOVED:
            return self._result(None, object_id, "already_completed", 0)

        self._ensure_no_active_leases(object_id)
        source_relative_path = self._object_relative_path(obj)
        operation_id = f"op_clean_{uuid.uuid4().hex}"
        cleanup_id = self._cleanup_id(operation_id)
        now = self._now()
        try:
            root_identity, source_parent_identity, source_identity, quarantine_identity = (
                self._capture_intent_identities(source_relative_path, obj)
            )
        except OpenBeneathError as exc:
            raise CleanupExecutionError("Object path failed no-follow identity validation", "object_identity_drift") from exc
        except OSError as exc:
            raise CleanupExecutionError("Object path could not be identity-checked", "object_identity_drift") from exc

        op = CanonicalOperationRequest(
            operation_id=operation_id,
            operation_type=OperationType.CLEANUP,
            request_id=request_id,
            request_digest=request_digest,
            authorization_id=f"auth_{uuid.uuid4().hex[:8]}",
            controller_epoch=str(int(time.time())),
            sequence=1,
            caller_identity_digest="sha256:caller",
            remote_identity=obj.remote_identity,
            project_identity=obj.project_identity,
            relationship_id=obj.relationship_id,
            workspace_id=obj.workspace_id,
            job_id=obj.job_id,
            target_object_id=object_id,
            canonical_evidence_digest=expected_object_evidence_digest,
            qualification_admission_id=None,
            evidence_candidate_id=None,
            promotion_id=None,
            authority_binding_id=None,
            phase=OperationPhase.RESERVED,
            outcome=None,
            reason_code=None,
            created_at=now,
            updated_at=now,
        )
        intent = CleanupIntent(
            cleanup_id=cleanup_id,
            operation_id=operation_id,
            preview_id=preview_id,
            object_id=object_id,
            expected_object_evidence_digest=expected_object_evidence_digest,
            expected_reference_digest=expected_reference_digest,
            final_entry_evidence_digest=None,
            phase=CleanupPhase.INTENT,
            outcome=None,
            reason_code=None,
            estimated_bytes=obj.known_bytes,
            observed_reclaimed_bytes=None,
            job_result_digest_before=job_result_digest_before,
            job_result_digest_after=job_result_digest_after,
            created_at=now,
            updated_at=now,
            completed_at=None,
            request_digest=request_digest,
            source_relative_path=source_relative_path,
            quarantine_relative_path=f"quarantine/{cleanup_id}",
            storage_root_identity=root_identity,
            source_parent_identity=source_parent_identity,
            source_identity=source_identity,
            quarantine_container_identity=quarantine_identity,
        )
        try:
            _, reserved_op, reserved_intent = self.repository.reserve_cleanup_operation(op, intent)
        except StorageRepositoryConflictError as exc:
            raise CleanupExecutionError(str(exc), "request_id_conflict") from exc
        except StorageRepositoryError as exc:
            raise CleanupExecutionError("Cleanup intent could not be reserved atomically", "cleanup_journal_error") from exc

        if reserved_intent is None:
            raise CleanupExecutionError(
                "Existing cleanup operation has no recorded intent; recovery authority is unavailable",
                "cleanup_intent_missing",
            )
        self._verify_intent_replay(
            reserved_intent,
            reserved_op,
            request_digest,
            preview_id,
            object_id,
            expected_object_evidence_digest,
            expected_reference_digest,
            job_result_digest_before,
            job_result_digest_after,
        )
        if not self._has_recovery_identity(reserved_intent):
            self._diagnose(reserved_intent, "historical_identity_missing")
            raise CleanupExecutionError("Cleanup intent lacks complete identity evidence", "historical_identity_missing")
        return self._resume_cleanup(reserved_intent, reserved_op)

    def _verify_intent_replay(
        self,
        intent: CleanupIntent,
        op: CanonicalOperationRequest,
        request_digest: str,
        preview_id: str,
        object_id: str,
        expected_object_evidence_digest: str,
        expected_reference_digest: str,
        job_result_digest_before: Optional[str],
        job_result_digest_after: Optional[str],
    ) -> None:
        if (
            intent.operation_id != op.operation_id
            or intent.object_id != object_id
            or intent.preview_id != preview_id
            or intent.expected_object_evidence_digest != expected_object_evidence_digest
            or intent.expected_reference_digest != expected_reference_digest
            or intent.job_result_digest_before != job_result_digest_before
            or intent.job_result_digest_after != job_result_digest_after
        ):
            raise CleanupExecutionError("Cleanup intent evidence differs from replay", "request_id_conflict")
        if op.request_digest != request_digest:
            raise CleanupExecutionError("Cleanup replay digest changed", "request_id_conflict")
        if intent.request_digest is not None and intent.request_digest != request_digest:
            raise CleanupExecutionError("Cleanup intent digest differs from canonical operation", "cleanup_journal_conflict")

    @staticmethod
    def _has_recovery_identity(intent: CleanupIntent) -> bool:
        return all(
            (
                intent.request_digest,
                intent.source_relative_path,
                intent.quarantine_relative_path,
                intent.storage_root_identity,
                intent.source_parent_identity,
                intent.source_identity,
                intent.quarantine_container_identity,
            )
        )

    def _diagnose(self, intent: CleanupIntent, reason_code: str) -> None:
        try:
            self.repository.record_cleanup_diagnostic(intent.cleanup_id, reason_code)
        except StorageRepositoryError as exc:
            raise CleanupExecutionError("Cleanup diagnostic could not be persisted", "cleanup_journal_error") from exc

    def _block(self, intent: CleanupIntent, reason_code: str, message: str) -> None:
        self._diagnose(intent, reason_code)
        raise CleanupExecutionError(message, reason_code)

    def _ensure_no_active_leases(self, object_id: str) -> None:
        with self.repository.connect() as conn:
            active_leases = conn.execute(
                """
                SELECT lease_id FROM materialization_leases
                WHERE object_id = ? AND state IN ('reserved', 'active', 'closing')
                """,
                (object_id,),
            ).fetchall()
        if active_leases:
            raise CleanupExecutionError(
                f"Object {object_id} has {len(active_leases)} active lease(s)",
                "workspace_lease_active",
            )

    def _resume_cleanup(
        self, intent: CleanupIntent, op: CanonicalOperationRequest
    ) -> Dict[str, Any]:
        lock_keys = [f"cleanup:{intent.cleanup_id}", f"object:{intent.object_id}"]
        if op.relationship_id:
            lock_keys.append(f"selection:{op.relationship_id}")
        try:
            with self.repository.cleanup_serialization_lock(*lock_keys):
                latest_intent = self.repository.get_cleanup_intent(intent.cleanup_id)
                latest_op = self.repository.get_operation(intent.operation_id)
                if (
                    latest_intent is None
                    or latest_op is None
                    or latest_intent.operation_id != intent.operation_id
                    or latest_intent.object_id != intent.object_id
                    or latest_op.operation_type != OperationType.CLEANUP
                    or latest_op.target_object_id != intent.object_id
                    or latest_op.request_digest != latest_intent.request_digest
                ):
                    self._block(
                        intent,
                        "cleanup_journal_conflict",
                        "Cleanup intent and canonical operation no longer match",
                    )
                if latest_intent.phase == CleanupPhase.TERMINAL:
                    if (
                        latest_intent.outcome == CleanupOutcome.COMPLETED
                        and latest_op.phase == OperationPhase.TERMINAL
                        and latest_op.outcome == OperationOutcome.COMPLETED
                    ):
                        return self._result(
                            latest_intent,
                            latest_intent.object_id,
                            "already_completed",
                            latest_intent.observed_reclaimed_bytes,
                        )
                    raise CleanupExecutionError(
                        latest_intent.reason_code or "Cleanup is terminal without completion",
                        latest_intent.reason_code or "cleanup_terminal",
                    )
                if latest_op.phase == OperationPhase.TERMINAL:
                    self._block(
                        latest_intent,
                        "cleanup_operation_terminal_incomplete",
                        "Cleanup operation is terminal without a completed intent",
                    )
                self._ensure_no_active_references(latest_intent, latest_op)
                return self._resume_cleanup_locked(latest_intent, latest_op)
        except StorageRepositoryError as exc:
            raise CleanupExecutionError(
                "Cleanup serialization or journal state is unavailable",
                "cleanup_journal_error",
            ) from exc

    def _ensure_no_active_references(
        self, intent: CleanupIntent, op: CanonicalOperationRequest
    ) -> None:
        obj = self.repository.get_object(intent.object_id)
        if obj is None or obj.object_id != op.target_object_id:
            self._block(intent, "object_unknown", "Cleanup object is no longer canonical")
        if obj.object_kind == ObjectKind.SYNC_GENERATION:
            if obj.relationship_id != op.relationship_id:
                self._block(
                    intent,
                    "cleanup_journal_conflict",
                    "Cleanup relationship differs from its canonical object",
                )
            current = (
                self.repository.get_current_selection(obj.relationship_id)
                if obj.relationship_id
                else None
            )
            if current is not None and current.object_id == obj.object_id:
                self._block(
                    intent,
                    "reference_active",
                    "Cleanup object became the current generation selection",
                )
        elif obj.object_kind == ObjectKind.CI_MATERIALIZATION:
            self._ensure_no_active_leases(obj.object_id)

    def _resume_cleanup_locked(
        self, intent: CleanupIntent, op: CanonicalOperationRequest
    ) -> Dict[str, Any]:
        if not self._has_recovery_identity(intent):
            self._block(
                intent,
                "historical_identity_missing",
                "Cleanup intent lacks captured inode evidence and is retained",
            )
        expected_quarantine_path = f"quarantine/{intent.cleanup_id}"
        if intent.quarantine_relative_path != expected_quarantine_path:
            self._block(intent, "cleanup_path_mismatch", "Cleanup quarantine path differs from its stable ID")
        if intent.phase not in (
            CleanupPhase.INTENT,
            CleanupPhase.QUARANTINED,
            CleanupPhase.REMOVING,
            CleanupPhase.FINAL_REMOVE_INTENT,
        ):
            self._block(intent, "cleanup_phase_unsupported", "Cleanup phase cannot authorize recovery")
        source_parent_fd: Optional[int] = None
        quarantine_fd: Optional[int] = None
        try:
            source_parts = self._relative_parts(intent.source_relative_path or "")
            if len(source_parts) < 2:
                self._block(intent, "cleanup_path_mismatch", "Cleanup source path has no parent")
            source_name = source_parts[-1]
            source_parent_path = "/".join(source_parts[:-1])

            root_fd = self.adapter.open_root_directory()
            root_identity = self.adapter.identity_from_stat(os.fstat(root_fd))
            os.close(root_fd)
            if not self.adapter.identity_matches(intent.storage_root_identity, root_identity):
                self._block(intent, "cleanup_root_identity_changed", "Storage root identity changed")

            source_parent_fd = self.adapter.open_directory_beneath(
                source_parent_path, intent.source_parent_identity
            )
            quarantine_fd = self.adapter.open_directory_beneath(
                "quarantine", intent.quarantine_container_identity
            )
        except CleanupExecutionError:
            if source_parent_fd is not None:
                os.close(source_parent_fd)
            if quarantine_fd is not None:
                os.close(quarantine_fd)
            raise
        except (OSError, OpenBeneathError) as exc:
            if source_parent_fd is not None:
                os.close(source_parent_fd)
            if quarantine_fd is not None:
                os.close(quarantine_fd)
            self._block(intent, "cleanup_parent_identity_changed", "Cleanup parent identity could not be verified")

        try:
            phase = intent.phase
            if not self.adapter.identity_matches(
                intent.quarantine_container_identity,
                self.adapter.identity_from_stat(os.fstat(quarantine_fd)),
            ):
                self._block(intent, "cleanup_quarantine_identity_changed", "Quarantine container identity changed")

            source_identity = self.adapter.stat_identity_at(source_parent_fd, source_name)
            target_name = intent.cleanup_id
            target_identity = self.adapter.stat_identity_at(quarantine_fd, target_name)
            source_exists = source_identity is not None
            target_exists = target_identity is not None

            if source_exists and target_exists:
                self._block(intent, "cleanup_both_locations_present", "Source and quarantine target both exist")
            if not source_exists and not target_exists:
                if intent.phase != CleanupPhase.FINAL_REMOVE_INTENT:
                    self._block(intent, "cleanup_both_locations_missing", "Source and quarantine target are both absent")
                self.adapter.fsync_directory_fd(quarantine_fd)
                observed = intent.observed_reclaimed_bytes
                self.repository.finalize_cleanup(
                    cleanup_id=intent.cleanup_id,
                    operation_id=op.operation_id,
                    object_id=intent.object_id,
                    observed_bytes=observed,
                    completed_at=self._now(),
                )
                return self._result(intent, intent.object_id, "completed", observed)

            if source_exists:
                if intent.phase != CleanupPhase.INTENT:
                    self._block(
                        intent,
                        "cleanup_source_phase_mismatch",
                        "Source remains after cleanup advanced beyond intent",
                    )
                if not self.adapter.identity_matches(intent.source_identity, source_identity or {}):
                    self._block(intent, "object_identity_drift", "Source object identity changed")
                self._ensure_no_active_references(intent, op)
                try:
                    self.adapter.rename_directory_noreplace_at(
                        source_parent_fd,
                        source_name,
                        quarantine_fd,
                        target_name,
                        source_identity=intent.source_identity or {},
                        source_parent_identity=intent.source_parent_identity or {},
                        destination_parent_identity=intent.quarantine_container_identity or {},
                    )
                except RenameNoReplaceError as exc:
                    self._block(intent, "cleanup_target_exists", "Quarantine target already exists")
                except (FileSystemAdapterError, OSError, OpenBeneathError) as exc:
                    self._block(intent, "cleanup_rename_failed", "Identity-bound quarantine rename failed")
                self.adapter.fsync_directory_fd(source_parent_fd)
                self.adapter.fsync_directory_fd(quarantine_fd)
                self.repository.update_cleanup_intent(
                    intent.cleanup_id,
                    expected_phase=CleanupPhase.INTENT,
                    phase=CleanupPhase.QUARANTINED,
                )
                target_identity = self.adapter.stat_identity_at(quarantine_fd, target_name)
                source_exists = False
                target_exists = target_identity is not None
                phase = CleanupPhase.QUARANTINED

            if not target_exists or not self.adapter.identity_matches(
                intent.source_identity, target_identity or {}
            ):
                self._block(intent, "cleanup_target_identity_changed", "Quarantine target identity changed")

            if phase == CleanupPhase.INTENT:
                # A crash may have happened after the rename but before phase persistence.
                self.adapter.fsync_directory_fd(source_parent_fd)
                self.adapter.fsync_directory_fd(quarantine_fd)
                self.repository.update_cleanup_intent(
                    intent.cleanup_id,
                    expected_phase=CleanupPhase.INTENT,
                    phase=CleanupPhase.QUARANTINED,
                )
                phase = CleanupPhase.QUARANTINED

            target_fd = self.adapter.open_directory_at(
                quarantine_fd, target_name, intent.source_identity
            )
            try:
                if phase == CleanupPhase.FINAL_REMOVE_INTENT:
                    if os.listdir(target_fd):
                        self._block(
                            intent,
                            "cleanup_final_target_not_empty",
                            "Final-remove intent points to a nonempty quarantine target",
                        )
                else:
                    if phase == CleanupPhase.QUARANTINED:
                        self.repository.update_cleanup_intent(
                            intent.cleanup_id,
                            expected_phase=CleanupPhase.QUARANTINED,
                            phase=CleanupPhase.REMOVING,
                        )
                        phase = CleanupPhase.REMOVING
                    observed = intent.observed_reclaimed_bytes
                    if observed is None:
                        observed = self.adapter.measure_tree_bytes(target_fd, intent.source_identity or {})
                        self.repository.record_cleanup_observed_bytes(intent.cleanup_id, observed)
                    self._ensure_no_active_references(intent, op)
                    self.adapter.remove_tree_contents_fd(target_fd, intent.source_identity or {})
                    if os.listdir(target_fd):
                        self._block(intent, "cleanup_target_not_empty", "Quarantine target remains nonempty")
                    self.adapter.fsync_directory_fd(target_fd)
                    self.repository.update_cleanup_intent(
                        intent.cleanup_id,
                        expected_phase=CleanupPhase.REMOVING,
                        phase=CleanupPhase.FINAL_REMOVE_INTENT,
                    )
            finally:
                os.close(target_fd)

            self._ensure_no_active_references(intent, op)
            self.adapter.remove_empty_directory_at(
                quarantine_fd,
                target_name,
                intent.source_identity or {},
            )
            finished_at = self._now()
            latest_intent = self.repository.get_cleanup_intent(intent.cleanup_id)
            observed_bytes = (
                latest_intent.observed_reclaimed_bytes if latest_intent is not None else intent.observed_reclaimed_bytes
            )
            self.repository.finalize_cleanup(
                cleanup_id=intent.cleanup_id,
                operation_id=op.operation_id,
                object_id=intent.object_id,
                observed_bytes=observed_bytes,
                completed_at=finished_at,
            )
            return self._result(intent, intent.object_id, "completed", observed_bytes)
        except CleanupExecutionError:
            raise
        except Exception:
            self._block(intent, "cleanup_recovery_failed", "Cleanup recovery stopped before finalization")
        finally:
            if source_parent_fd is not None:
                os.close(source_parent_fd)
            if quarantine_fd is not None:
                os.close(quarantine_fd)

    @staticmethod
    def _result(
        intent: Optional[CleanupIntent],
        object_id: str,
        status: str,
        observed_bytes: Optional[int],
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "ok": True,
            "protocol": "owned-storage-authority-v1",
            "operation": "cleanup",
            "object_id": object_id,
            "status": status,
            "observed_reclaimed_bytes": observed_bytes,
            "complete": True,
        }
        if intent is not None:
            result["cleanup_id"] = intent.cleanup_id
            result["operation_id"] = intent.operation_id
        return result

    def reconcile_startup(self) -> Dict[str, Any]:
        """Recover only journaled cleanup intents with complete captured identity."""
        diagnostics: List[Dict[str, str]] = []
        reconciled = 0
        known_ids = set()
        quarantine_fd = self.adapter.open_directory_beneath("quarantine")
        try:
            quarantine_entries = set(os.listdir(quarantine_fd))
        finally:
            os.close(quarantine_fd)

        for intent in self.repository.list_cleanup_intents(include_terminal=True):
            known_ids.add(intent.cleanup_id)
            if intent.phase == CleanupPhase.TERMINAL:
                if intent.cleanup_id in quarantine_entries:
                    reason = (
                        "historical_identity_missing"
                        if not self._has_recovery_identity(intent)
                        else "terminal_quarantine_entry_retained"
                    )
                    diagnostics.append({"cleanup_id": intent.cleanup_id, "reason_code": reason})
                continue
            if not self._has_recovery_identity(intent):
                self._diagnose(intent, "historical_identity_missing")
                diagnostics.append(
                    {"cleanup_id": intent.cleanup_id, "reason_code": "historical_identity_missing"}
                )
                continue
            op = self.repository.get_operation(intent.operation_id)
            if op is None or op.operation_type != OperationType.CLEANUP or op.request_digest != intent.request_digest:
                self._diagnose(intent, "cleanup_operation_mismatch")
                diagnostics.append(
                    {"cleanup_id": intent.cleanup_id, "reason_code": "cleanup_operation_mismatch"}
                )
                continue
            if op.phase == OperationPhase.TERMINAL:
                self._diagnose(intent, "cleanup_operation_terminal_incomplete")
                diagnostics.append(
                    {"cleanup_id": intent.cleanup_id, "reason_code": "cleanup_operation_terminal_incomplete"}
                )
                continue
            try:
                self._resume_cleanup(intent, op)
                reconciled += 1
            except CleanupExecutionError as exc:
                diagnostics.append({"cleanup_id": intent.cleanup_id, "reason_code": exc.code})

        for name in quarantine_entries:
            if name not in known_ids:
                diagnostics.append({"cleanup_id": name, "reason_code": "unknown_quarantine_entry_retained"})

        return {
            "ok": True,
            "reconciled_quarantine_count": reconciled,
            "cleanup_diagnostics": diagnostics,
        }
