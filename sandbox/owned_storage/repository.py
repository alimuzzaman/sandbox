"""Private SQLite repository for owned storage authority."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sandbox.owned_storage.models import (
    AdoptionBindingPhase,
    AuthorityAdoptionBinding,
    AuthorityOwnedObject,
    AuthorityPolicy,
    CandidateDecision,
    CanonicalOperationRequest,
    CleanupIntent,
    CleanupOutcome,
    CleanupPhase,
    LeaseState,
    MaterializationLease,
    ObjectKind,
    ObjectLifecycle,
    OperationOutcome,
    OperationPhase,
    OperationType,
    PolicyMode,
    PreviewCandidate,
    ReclamationPreview,
    RelationshipCurrentSelection,
)


class StorageRepositoryError(Exception):
    """Base error for storage authority repository."""


class StorageRepositoryConflictError(StorageRepositoryError):
    """Raised when replaying a request with conflicting parameters."""


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 30000;

CREATE TABLE IF NOT EXISTS canonical_operations (
    operation_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    authorization_id TEXT NOT NULL,
    controller_epoch TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    caller_identity_digest TEXT NOT NULL,
    remote_identity TEXT NOT NULL,
    project_identity TEXT NOT NULL,
    relationship_id TEXT,
    workspace_id TEXT,
    job_id TEXT,
    target_object_id TEXT,
    canonical_evidence_digest TEXT NOT NULL,
    qualification_admission_id TEXT,
    evidence_candidate_id TEXT,
    promotion_id TEXT,
    authority_binding_id TEXT,
    phase TEXT NOT NULL,
    outcome TEXT,
    reason_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(remote_identity, project_identity, operation_type, request_id)
);

CREATE TABLE IF NOT EXISTS authority_objects (
    object_id TEXT PRIMARY KEY,
    object_kind TEXT NOT NULL,
    remote_identity TEXT NOT NULL,
    project_identity TEXT NOT NULL,
    relationship_id TEXT,
    workspace_id TEXT,
    job_id TEXT,
    parent_object_id TEXT,
    created_by_operation_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    policy_id TEXT,
    policy_generation INTEGER,
    qualification_admission_id TEXT,
    evidence_candidate_id TEXT,
    promotion_id TEXT,
    evidence_id TEXT,
    authority_binding_id TEXT,
    retention_policy_digest TEXT NOT NULL,
    content_evidence_json TEXT NOT NULL,
    filesystem_identity_json TEXT NOT NULL,
    known_bytes INTEGER,
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS adoption_bindings (
    authority_binding_id TEXT PRIMARY KEY,
    binding_generation INTEGER NOT NULL,
    remote_identity TEXT NOT NULL,
    project_identity TEXT NOT NULL,
    platform_mode TEXT NOT NULL,
    fixture_identity TEXT NOT NULL,
    review_decision_id TEXT NOT NULL,
    promotion_id TEXT NOT NULL,
    evidence_candidate_id TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    service_revision TEXT NOT NULL,
    controller_revision TEXT NOT NULL,
    contract_revision TEXT NOT NULL,
    lifecycle_request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    lifecycle_generation INTEGER NOT NULL,
    binding_digest TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revocation_generation INTEGER,
    phase TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationship_current_selections (
    relationship_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    selection_generation INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materialization_leases (
    lease_id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    lifecycle_generation INTEGER NOT NULL,
    mount_identity_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE IF NOT EXISTS authority_policies (
    policy_id TEXT PRIMARY KEY,
    remote_identity TEXT NOT NULL,
    project_identity TEXT NOT NULL,
    mode TEXT NOT NULL,
    effective_generation INTEGER NOT NULL,
    changed_by TEXT NOT NULL,
    request_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    admission_basis_json TEXT,
    changed_at TEXT NOT NULL,
    UNIQUE(remote_identity, project_identity)
);

CREATE TABLE IF NOT EXISTS cleanup_intents (
    cleanup_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    preview_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    expected_object_evidence_digest TEXT NOT NULL,
    expected_reference_digest TEXT NOT NULL,
    final_entry_evidence_digest TEXT,
    phase TEXT NOT NULL,
    outcome TEXT,
    reason_code TEXT,
    estimated_bytes INTEGER,
    observed_reclaimed_bytes INTEGER,
    job_result_digest_before TEXT,
    job_result_digest_after TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS reclamation_previews (
    preview_id TEXT PRIMARY KEY,
    remote_identity TEXT NOT NULL,
    project_identity TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL,
    policy_generation INTEGER NOT NULL,
    candidate_digest TEXT NOT NULL,
    estimated_reclaimable_bytes INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preview_candidates (
    preview_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    estimated_bytes INTEGER,
    object_evidence_digest TEXT NOT NULL,
    reference_snapshot_digest TEXT NOT NULL,
    PRIMARY KEY(preview_id, object_id),
    FOREIGN KEY(preview_id) REFERENCES reclamation_previews(preview_id) ON DELETE CASCADE
);
"""


class StorageAuthorityRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA synchronous = FULL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def reserve_operation(
        self, op: CanonicalOperationRequest
    ) -> Tuple[bool, CanonicalOperationRequest]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM canonical_operations
                WHERE remote_identity = ? AND project_identity = ? AND operation_type = ? AND request_id = ?
                """,
                (op.remote_identity, op.project_identity, op.operation_type.value, op.request_id),
            ).fetchone()

            if row is not None:
                existing = self._row_to_operation(row)
                if existing.request_digest != op.request_digest:
                    raise StorageRepositoryConflictError(
                        f"Request ID {op.request_id} replayed with different digest"
                    )
                return False, existing

            conn.execute(
                """
                INSERT INTO canonical_operations (
                    operation_id, operation_type, request_id, request_digest, authorization_id,
                    controller_epoch, sequence, caller_identity_digest, remote_identity, project_identity,
                    relationship_id, workspace_id, job_id, target_object_id, canonical_evidence_digest,
                    qualification_admission_id, evidence_candidate_id, promotion_id, authority_binding_id,
                    phase, outcome, reason_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    op.operation_id,
                    op.operation_type.value,
                    op.request_id,
                    op.request_digest,
                    op.authorization_id,
                    op.controller_epoch,
                    op.sequence,
                    op.caller_identity_digest,
                    op.remote_identity,
                    op.project_identity,
                    op.relationship_id,
                    op.workspace_id,
                    op.job_id,
                    op.target_object_id,
                    op.canonical_evidence_digest,
                    op.qualification_admission_id,
                    op.evidence_candidate_id,
                    op.promotion_id,
                    op.authority_binding_id,
                    op.phase.value,
                    op.outcome.value if op.outcome else None,
                    op.reason_code,
                    op.created_at,
                    op.updated_at,
                ),
            )
            return True, op

    def update_operation_phase(
        self,
        operation_id: str,
        phase: OperationPhase,
        updated_at: str,
        outcome: Optional[OperationOutcome] = None,
        reason_code: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE canonical_operations
                SET phase = ?, outcome = ?, reason_code = ?, updated_at = ?
                WHERE operation_id = ?
                """,
                (
                    phase.value,
                    outcome.value if outcome else None,
                    reason_code,
                    updated_at,
                    operation_id,
                ),
            )

    def get_operation(self, operation_id: str) -> Optional[CanonicalOperationRequest]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM canonical_operations WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            return self._row_to_operation(row) if row else None

    def _row_to_operation(self, row: sqlite3.Row) -> CanonicalOperationRequest:
        return CanonicalOperationRequest(
            operation_id=row["operation_id"],
            operation_type=OperationType(row["operation_type"]),
            request_id=row["request_id"],
            request_digest=row["request_digest"],
            authorization_id=row["authorization_id"],
            controller_epoch=row["controller_epoch"],
            sequence=row["sequence"],
            caller_identity_digest=row["caller_identity_digest"],
            remote_identity=row["remote_identity"],
            project_identity=row["project_identity"],
            relationship_id=row["relationship_id"],
            workspace_id=row["workspace_id"],
            job_id=row["job_id"],
            target_object_id=row["target_object_id"],
            canonical_evidence_digest=row["canonical_evidence_digest"],
            qualification_admission_id=row["qualification_admission_id"],
            evidence_candidate_id=row["evidence_candidate_id"],
            promotion_id=row["promotion_id"],
            authority_binding_id=row["authority_binding_id"],
            phase=OperationPhase(row["phase"]),
            outcome=OperationOutcome(row["outcome"]) if row["outcome"] else None,
            reason_code=row["reason_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def save_object(self, obj: AuthorityOwnedObject) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO authority_objects (
                    object_id, object_kind, remote_identity, project_identity, relationship_id,
                    workspace_id, job_id, parent_object_id, created_by_operation_id, lifecycle,
                    policy_id, policy_generation, qualification_admission_id, evidence_candidate_id,
                    promotion_id, evidence_id, authority_binding_id, retention_policy_digest,
                    content_evidence_json, filesystem_identity_json, known_bytes, created_at,
                    accepted_at, removed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obj.object_id,
                    obj.object_kind.value,
                    obj.remote_identity,
                    obj.project_identity,
                    obj.relationship_id,
                    obj.workspace_id,
                    obj.job_id,
                    obj.parent_object_id,
                    obj.created_by_operation_id,
                    obj.lifecycle.value,
                    obj.policy_id,
                    obj.policy_generation,
                    obj.qualification_admission_id,
                    obj.evidence_candidate_id,
                    obj.promotion_id,
                    obj.evidence_id,
                    obj.authority_binding_id,
                    obj.retention_policy_digest,
                    json.dumps(obj.content_evidence, sort_keys=True),
                    json.dumps(obj.filesystem_identity, sort_keys=True),
                    obj.known_bytes,
                    obj.created_at,
                    obj.accepted_at,
                    obj.removed_at,
                ),
            )

    def get_object(self, object_id: str) -> Optional[AuthorityOwnedObject]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM authority_objects WHERE object_id = ?", (object_id,)
            ).fetchone()
            return self._row_to_object(row) if row else None

    def find_object_by_workspace(self, workspace_id: str) -> Optional[AuthorityOwnedObject]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM authority_objects WHERE workspace_id = ? AND lifecycle != 'removed' ORDER BY created_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            return self._row_to_object(row) if row else None

    def find_object_by_job(self, job_id: str) -> Optional[AuthorityOwnedObject]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM authority_objects WHERE job_id = ? AND lifecycle != 'removed' ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            return self._row_to_object(row) if row else None


    def _row_to_object(self, row: sqlite3.Row) -> AuthorityOwnedObject:
        return AuthorityOwnedObject(
            object_id=row["object_id"],
            object_kind=ObjectKind(row["object_kind"]),
            remote_identity=row["remote_identity"],
            project_identity=row["project_identity"],
            relationship_id=row["relationship_id"],
            workspace_id=row["workspace_id"],
            job_id=row["job_id"],
            parent_object_id=row["parent_object_id"],
            created_by_operation_id=row["created_by_operation_id"],
            lifecycle=ObjectLifecycle(row["lifecycle"]),
            policy_id=row["policy_id"],
            policy_generation=row["policy_generation"],
            qualification_admission_id=row["qualification_admission_id"],
            evidence_candidate_id=row["evidence_candidate_id"],
            promotion_id=row["promotion_id"],
            evidence_id=row["evidence_id"],
            authority_binding_id=row["authority_binding_id"],
            retention_policy_digest=row["retention_policy_digest"],
            content_evidence=json.loads(row["content_evidence_json"]),
            filesystem_identity=json.loads(row["filesystem_identity_json"]),
            known_bytes=row["known_bytes"],
            created_at=row["created_at"],
            accepted_at=row["accepted_at"],
            removed_at=row["removed_at"],
        )

    def set_current_selection(self, sel: RelationshipCurrentSelection) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO relationship_current_selections (
                    relationship_id, object_id, generation_id, selection_generation, operation_id, changed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sel.relationship_id,
                    sel.object_id,
                    sel.generation_id,
                    sel.selection_generation,
                    sel.operation_id,
                    sel.changed_at,
                ),
            )

    def get_current_selection(self, relationship_id: str) -> Optional[RelationshipCurrentSelection]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM relationship_current_selections WHERE relationship_id = ?",
                (relationship_id,),
            ).fetchone()
            if not row:
                return None
            return RelationshipCurrentSelection(
                relationship_id=row["relationship_id"],
                object_id=row["object_id"],
                generation_id=row["generation_id"],
                selection_generation=row["selection_generation"],
                operation_id=row["operation_id"],
                changed_at=row["changed_at"],
            )

    def save_adoption_binding(self, binding: AuthorityAdoptionBinding) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO adoption_bindings (
                    authority_binding_id, binding_generation, remote_identity, project_identity,
                    platform_mode, fixture_identity, review_decision_id, promotion_id,
                    evidence_candidate_id, evidence_digest, source_revision, service_revision,
                    controller_revision, contract_revision, lifecycle_request_id, request_digest,
                    lifecycle_generation, binding_digest, expires_at, revocation_generation, phase
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    binding.authority_binding_id,
                    binding.binding_generation,
                    binding.remote_identity,
                    binding.project_identity,
                    binding.platform_mode,
                    binding.fixture_identity,
                    binding.review_decision_id,
                    binding.promotion_id,
                    binding.evidence_candidate_id,
                    binding.evidence_digest,
                    binding.source_revision,
                    binding.service_revision,
                    binding.controller_revision,
                    binding.contract_revision,
                    binding.lifecycle_request_id,
                    binding.request_digest,
                    binding.lifecycle_generation,
                    binding.binding_digest,
                    binding.expires_at,
                    binding.revocation_generation,
                    binding.phase.value,
                ),
            )

    def get_adoption_binding(self, binding_id: str) -> Optional[AuthorityAdoptionBinding]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM adoption_bindings WHERE authority_binding_id = ?", (binding_id,)
            ).fetchone()
            if not row:
                return None
            return AuthorityAdoptionBinding(
                authority_binding_id=row["authority_binding_id"],
                binding_generation=row["binding_generation"],
                remote_identity=row["remote_identity"],
                project_identity=row["project_identity"],
                platform_mode=row["platform_mode"],
                fixture_identity=row["fixture_identity"],
                review_decision_id=row["review_decision_id"],
                promotion_id=row["promotion_id"],
                evidence_candidate_id=row["evidence_candidate_id"],
                evidence_digest=row["evidence_digest"],
                source_revision=row["source_revision"],
                service_revision=row["service_revision"],
                controller_revision=row["controller_revision"],
                contract_revision=row["contract_revision"],
                lifecycle_request_id=row["lifecycle_request_id"],
                request_digest=row["request_digest"],
                lifecycle_generation=row["lifecycle_generation"],
                binding_digest=row["binding_digest"],
                expires_at=row["expires_at"],
                revocation_generation=row["revocation_generation"],
                phase=AdoptionBindingPhase(row["phase"]),
            )

    def save_policy(self, policy: AuthorityPolicy) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO authority_policies (
                    policy_id, remote_identity, project_identity, mode, effective_generation,
                    changed_by, request_id, request_digest, admission_basis_json, changed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy.policy_id,
                    policy.remote_identity,
                    policy.project_identity,
                    policy.mode.value,
                    policy.effective_generation,
                    policy.changed_by,
                    policy.request_id,
                    policy.request_digest,
                    json.dumps(policy.admission_basis, sort_keys=True) if policy.admission_basis else None,
                    policy.changed_at,
                ),
            )

    def get_policy(self, remote_identity: str, project_identity: str) -> Optional[AuthorityPolicy]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM authority_policies
                WHERE remote_identity = ? AND project_identity = ?
                """,
                (remote_identity, project_identity),
            ).fetchone()
            if not row:
                return None
            return AuthorityPolicy(
                policy_id=row["policy_id"],
                remote_identity=row["remote_identity"],
                project_identity=row["project_identity"],
                mode=PolicyMode(row["mode"]),
                effective_generation=row["effective_generation"],
                changed_by=row["changed_by"],
                request_id=row["request_id"],
                request_digest=row["request_digest"],
                admission_basis=json.loads(row["admission_basis_json"]) if row["admission_basis_json"] else None,
                changed_at=row["changed_at"],
            )

    def save_cleanup_intent(self, intent: CleanupIntent) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cleanup_intents (
                    cleanup_id, operation_id, preview_id, object_id,
                    expected_object_evidence_digest, expected_reference_digest,
                    final_entry_evidence_digest, phase, outcome, reason_code,
                    estimated_bytes, observed_reclaimed_bytes,
                    job_result_digest_before, job_result_digest_after,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.cleanup_id,
                    intent.operation_id,
                    intent.preview_id,
                    intent.object_id,
                    intent.expected_object_evidence_digest,
                    intent.expected_reference_digest,
                    intent.final_entry_evidence_digest,
                    intent.phase.value,
                    intent.outcome.value if intent.outcome else None,
                    intent.reason_code,
                    intent.estimated_bytes,
                    intent.observed_reclaimed_bytes,
                    intent.job_result_digest_before,
                    intent.job_result_digest_after,
                    intent.created_at,
                    intent.updated_at,
                    intent.completed_at,
                ),
            )

    def update_cleanup_intent(
        self,
        cleanup_id: str,
        phase: CleanupPhase,
        outcome: Optional[CleanupOutcome] = None,
        reason_code: Optional[str] = None,
        observed_bytes: Optional[int] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE cleanup_intents
                SET phase = ?, outcome = COALESCE(?, outcome), reason_code = COALESCE(?, reason_code),
                    observed_reclaimed_bytes = COALESCE(?, observed_reclaimed_bytes),
                    completed_at = COALESCE(?, completed_at), updated_at = ?
                WHERE cleanup_id = ?
                """,
                (
                    phase.value,
                    outcome.value if outcome else None,
                    reason_code,
                    observed_bytes,
                    completed_at,
                    completed_at or "now",
                    cleanup_id,
                ),
            )

    def get_cleanup_intent(self, cleanup_id: str) -> Optional[CleanupIntent]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cleanup_intents WHERE cleanup_id = ?", (cleanup_id,)
            ).fetchone()
            if not row:
                return None
            return CleanupIntent(
                cleanup_id=row["cleanup_id"],
                operation_id=row["operation_id"],
                preview_id=row["preview_id"],
                object_id=row["object_id"],
                expected_object_evidence_digest=row["expected_object_evidence_digest"],
                expected_reference_digest=row["expected_reference_digest"],
                final_entry_evidence_digest=row["final_entry_evidence_digest"],
                phase=CleanupPhase(row["phase"]),
                outcome=CleanupOutcome(row["outcome"]) if row["outcome"] else None,
                reason_code=row["reason_code"],
                estimated_bytes=row["estimated_bytes"],
                observed_reclaimed_bytes=row["observed_reclaimed_bytes"],
                job_result_digest_before=row["job_result_digest_before"],
                job_result_digest_after=row["job_result_digest_after"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
            )

    def save_lease(self, lease: MaterializationLease) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO materialization_leases (
                    lease_id, object_id, job_id, workspace_id, lifecycle_generation,
                    mount_identity_digest, state, opened_at, heartbeat_at, expires_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease.lease_id,
                    lease.object_id,
                    lease.job_id,
                    lease.workspace_id,
                    lease.lifecycle_generation,
                    lease.mount_identity_digest,
                    lease.state.value,
                    lease.opened_at,
                    lease.heartbeat_at,
                    lease.expires_at,
                    lease.closed_at,
                ),
            )

    def get_lease(self, lease_id: str) -> Optional[MaterializationLease]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM materialization_leases WHERE lease_id = ?", (lease_id,)
            ).fetchone()
            if not row:
                return None
            return MaterializationLease(
                lease_id=row["lease_id"],
                object_id=row["object_id"],
                job_id=row["job_id"],
                workspace_id=row["workspace_id"],
                lifecycle_generation=row["lifecycle_generation"],
                mount_identity_digest=row["mount_identity_digest"],
                state=LeaseState(row["state"]),
                opened_at=row["opened_at"],
                heartbeat_at=row["heartbeat_at"],
                expires_at=row["expires_at"],
                closed_at=row["closed_at"],
            )
