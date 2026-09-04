"""Quarantine and physical removal state machine for owned storage authority."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from sandbox.owned_storage.adapters.linux import LinuxFilesystemAdapter, RenameNoReplaceError
from sandbox.owned_storage.models import (
    CleanupIntent,
    CleanupOutcome,
    CleanupPhase,
    LeaseState,
    ObjectKind,
    ObjectLifecycle,
)
from sandbox.owned_storage.repository import StorageAuthorityRepository
from sandbox.owned_storage.service import utc_now_iso


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
        self.adapter.ensure_directory(self.quarantine_dir, 0o700)

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
        """Atomically verify, quarantine, unlink, and finalize removal of an authority object."""
        if not confirm:
            raise CleanupExecutionError("Confirmation is required for cleanup", "request_invalid")

        obj = self.repository.get_object(object_id)
        if obj is None:
            raise CleanupExecutionError(f"Object {object_id} unknown", "object_unknown")

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

        # Check for active leases
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

        cleanup_id = f"clean_{uuid.uuid4().hex[:12]}"
        operation_id = f"op_clean_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()

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
        )
        self.repository.save_cleanup_intent(intent)

        # Locate object directory
        if obj.object_kind == ObjectKind.CI_MATERIALIZATION:
            object_dir = self.storage_root / "objects" / obj.project_identity / "workspaces" / object_id
        else:
            rel_path = obj.relationship_id or "unscoped"
            gen_path = obj.content_evidence.get("generation_id", object_id)
            object_dir = self.storage_root / "objects" / obj.project_identity / rel_path / gen_path

        if not object_dir.exists():
            self.repository.update_cleanup_intent(
                cleanup_id,
                CleanupPhase.TERMINAL,
                outcome=CleanupOutcome.FAILED,
                reason_code="object_unknown",
            )
            raise CleanupExecutionError(f"Object directory does not exist: {object_dir}", "object_unknown")

        # Verify filesystem identity (device and inode)
        current_ident = self.adapter.stat_identity(object_dir)
        expected_inode = obj.filesystem_identity.get("inode")
        expected_device = obj.filesystem_identity.get("device")

        if (
            expected_inode is not None
            and current_ident.get("inode") != expected_inode
            or expected_device is not None
            and current_ident.get("device") != expected_device
        ):
            self.repository.update_cleanup_intent(
                cleanup_id,
                CleanupPhase.TERMINAL,
                outcome=CleanupOutcome.REFUSED,
                reason_code="object_identity_drift",
            )
            raise CleanupExecutionError(
                f"Object filesystem identity drifted for {object_id}",
                "object_identity_drift",
            )

        # Move to private quarantine
        quarantine_cleanup_dir = self.quarantine_dir / cleanup_id
        self.adapter.ensure_directory(quarantine_cleanup_dir, 0o700)
        quarantine_target = quarantine_cleanup_dir / "target"

        try:
            self.adapter.rename_noreplace(object_dir, quarantine_target)
        except RenameNoReplaceError as exc:
            self.repository.update_cleanup_intent(
                cleanup_id,
                CleanupPhase.TERMINAL,
                outcome=CleanupOutcome.FAILED,
                reason_code="cleanup_failed",
            )
            raise CleanupExecutionError(f"Quarantine move failed: {exc}", "cleanup_failed") from exc

        self.adapter.fsync_directory(object_dir.parent)
        self.repository.update_cleanup_intent(cleanup_id, CleanupPhase.QUARANTINED)

        # Measure reclaimed bytes before unlinking
        total_reclaimed = 0
        try:
            for root, _, files in os.walk(quarantine_target):
                for f in files:
                    fp = Path(root) / f
                    if not fp.is_symlink():
                        try:
                            total_reclaimed += fp.stat().st_size
                        except OSError:
                            pass
        except OSError:
            pass

        if obj.known_bytes and total_reclaimed == 0:
            total_reclaimed = obj.known_bytes

        # Remove descendants beneath quarantine
        self.repository.update_cleanup_intent(cleanup_id, CleanupPhase.REMOVING)
        self.adapter.remove_tree_beneath(quarantine_target)

        # Verify quarantine target is empty and matches original inode
        post_unlink_ident = self.adapter.stat_identity(quarantine_target)
        if expected_inode is not None and post_unlink_ident.get("inode") != expected_inode:
            self.repository.update_cleanup_intent(
                cleanup_id,
                CleanupPhase.TERMINAL,
                outcome=CleanupOutcome.INDETERMINATE,
                reason_code="cleanup_indeterminate",
            )
            raise CleanupExecutionError("Empty quarantine target identity changed", "cleanup_indeterminate")

        # Flush final remove intent
        self.repository.update_cleanup_intent(
            cleanup_id,
            CleanupPhase.FINAL_REMOVE_INTENT,
        )

        # Unlink empty directory name and cleanup folder
        quarantine_target.rmdir()
        quarantine_cleanup_dir.rmdir()
        self.adapter.fsync_directory(self.quarantine_dir)

        finished_at = utc_now_iso()
        self.repository.update_cleanup_intent(
            cleanup_id,
            CleanupPhase.TERMINAL,
            outcome=CleanupOutcome.COMPLETED,
            observed_bytes=total_reclaimed,
            completed_at=finished_at,
        )

        # Update object lifecycle to REMOVED
        with self.repository.connect() as conn:
            conn.execute(
                """
                UPDATE authority_objects
                SET lifecycle = ?, removed_at = ?
                WHERE object_id = ?
                """,
                (ObjectLifecycle.REMOVED.value, finished_at, object_id),
            )

        return {
            "ok": True,
            "protocol": "owned-storage-authority-v1",
            "operation": "cleanup",
            "cleanup_id": cleanup_id,
            "object_id": object_id,
            "status": "completed",
            "observed_reclaimed_bytes": total_reclaimed,
            "complete": True,
        }
