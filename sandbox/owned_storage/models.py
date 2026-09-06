"""Owned storage authority core data models and enums."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class AdoptionBindingPhase(str, Enum):
    PREPARED = "prepared"
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class AuthorityAdoptionBinding:
    authority_binding_id: str
    binding_generation: int
    remote_identity: str
    project_identity: str
    platform_mode: str
    fixture_identity: str
    review_decision_id: str
    promotion_id: str
    evidence_candidate_id: str
    evidence_digest: str
    source_revision: str
    service_revision: str
    controller_revision: str
    contract_revision: str
    lifecycle_request_id: str
    request_digest: str
    lifecycle_generation: int
    binding_digest: str
    expires_at: str
    revocation_generation: Optional[int] = None
    phase: AdoptionBindingPhase = AdoptionBindingPhase.PREPARED


class QualificationState(str, Enum):
    SEALED = "sealed"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class QualificationAdmission:
    admission_id: str
    remote_identity: str
    project_identity: str
    fixture_identity: str
    source_revision: str
    service_revision: str
    controller_identity_digest: str
    evidence_candidate_id: str
    allowed_operations: List[str]
    operation_budget: int
    issued_at: str
    expires_at: str
    state: QualificationState
    cleanup_evidence_digest: Optional[str] = None


class PolicyMode(str, Enum):
    LEGACY = "legacy"
    FUTURE = "future"


@dataclass(frozen=True)
class AuthorityPolicy:
    policy_id: str
    remote_identity: str
    project_identity: str
    mode: PolicyMode
    effective_generation: int
    changed_by: str
    request_id: str
    request_digest: str
    admission_basis: Optional[Dict[str, Any]]
    changed_at: str


class OperationType(str, Enum):
    POLICY = "policy"
    QUALIFICATION = "qualification"
    PUBLISH = "publish"
    MATERIALIZE = "materialize"
    REFERENCE_OPEN = "reference_open"
    REFERENCE_CLOSE = "reference_close"
    PREVIEW = "preview"
    CLEANUP = "cleanup"
    RECONCILE = "reconcile"


class OperationPhase(str, Enum):
    RESERVED = "reserved"
    RECEIVING = "receiving"
    VERIFIED = "verified"
    EFFECT_INTENT = "effect_intent"
    EFFECT_APPLIED = "effect_applied"
    TERMINAL = "terminal"


class OperationOutcome(str, Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    RETAINED = "retained"
    REFUSED = "refused"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class CanonicalOperationRequest:
    operation_id: str
    operation_type: OperationType
    request_id: str
    request_digest: str
    authorization_id: str
    controller_epoch: str
    sequence: int
    caller_identity_digest: str
    remote_identity: str
    project_identity: str
    relationship_id: Optional[str]
    workspace_id: Optional[str]
    job_id: Optional[str]
    target_object_id: Optional[str]
    canonical_evidence_digest: str
    qualification_admission_id: Optional[str]
    evidence_candidate_id: Optional[str]
    promotion_id: Optional[str]
    authority_binding_id: Optional[str]
    phase: OperationPhase
    outcome: Optional[OperationOutcome]
    reason_code: Optional[str]
    created_at: str
    updated_at: str


class ObjectKind(str, Enum):
    SYNC_GENERATION = "sync_generation"
    CI_MATERIALIZATION = "ci_materialization"
    RETAINED_ARTIFACT = "retained_artifact"


class ObjectLifecycle(str, Enum):
    STAGING = "staging"
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETAINED = "retained"
    ELIGIBLE = "eligible"
    QUARANTINING = "quarantining"
    QUARANTINED = "quarantined"
    REMOVED = "removed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class AuthorityOwnedObject:
    object_id: str
    object_kind: ObjectKind
    remote_identity: str
    project_identity: str
    relationship_id: Optional[str]
    workspace_id: Optional[str]
    job_id: Optional[str]
    parent_object_id: Optional[str]
    created_by_operation_id: str
    lifecycle: ObjectLifecycle
    policy_id: Optional[str]
    policy_generation: Optional[int]
    qualification_admission_id: Optional[str]
    evidence_candidate_id: Optional[str]
    promotion_id: Optional[str]
    evidence_id: Optional[str]
    authority_binding_id: Optional[str]
    retention_policy_digest: str
    content_evidence: Dict[str, Any]
    filesystem_identity: Dict[str, Any]
    known_bytes: Optional[int]
    created_at: str
    accepted_at: Optional[str] = None
    removed_at: Optional[str] = None


@dataclass(frozen=True)
class GenerationBinding:
    remote_identity: str
    project_identity: str
    relationship_id: str
    workspace_id: str
    request_id: str
    generation_id: str
    manifest_digest: str
    archive_manifest_digest: str
    file_count: int
    byte_count: int
    accepted_at: str


@dataclass(frozen=True)
class RelationshipCurrentSelection:
    relationship_id: str
    object_id: str
    generation_id: str
    selection_generation: int
    operation_id: str
    changed_at: str


@dataclass(frozen=True)
class MaterializationBinding:
    project_identity: str
    job_id: str
    workspace_id: str
    source_generation_object_id: Optional[str]
    source_identity_digest: str
    materialization_id: str
    workspace_mode: str
    cleanup_policy: str
    root_identity_digest: str
    writable_interior_identity: Dict[str, Any]
    created_at: str


class LeaseState(str, Enum):
    RESERVED = "reserved"
    ACTIVE = "active"
    CLOSING = "closing"
    CLOSED = "closed"
    REVOKED = "revoked"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class MaterializationLease:
    lease_id: str
    object_id: str
    job_id: str
    workspace_id: str
    lifecycle_generation: int
    mount_identity_digest: str
    state: LeaseState
    opened_at: str
    heartbeat_at: str
    expires_at: str
    closed_at: Optional[str] = None


class RetentionKind(str, Enum):
    CURRENT = "current"
    PENDING = "pending"
    RETAIN = "retain"
    RELEASE = "release"
    WINDOW = "window"


@dataclass(frozen=True)
class RetentionPolicyProjection:
    policy_digest: str
    policy_kind: RetentionKind
    release_condition: Optional[str]
    retain_until: Optional[str]
    source_policy_generation: int
    observed_at: str


@dataclass(frozen=True)
class ReferenceSnapshot:
    snapshot_id: str
    object_id: str
    current_selection_generation: Optional[int]
    workspace_index_generation: Optional[int]
    active_reference_counts: Dict[str, int]
    complete: bool
    digest: str
    observed_at: str
    expires_at: str


class CandidateDecision(str, Enum):
    ELIGIBLE = "eligible"
    PROTECTED = "protected"


@dataclass(frozen=True)
class PreviewCandidate:
    object_id: str
    object_kind: ObjectKind
    lifecycle: ObjectLifecycle
    decision: CandidateDecision
    reason_code: str
    estimated_bytes: Optional[int]
    object_evidence_digest: str
    reference_snapshot_digest: str


@dataclass(frozen=True)
class ReclamationPreview:
    preview_id: str
    remote_identity: str
    project_identity: str
    inventory_generation: int
    policy_generation: int
    candidate_digest: str
    candidates: List[PreviewCandidate]
    estimated_reclaimable_bytes: int
    complete: bool
    created_at: str
    expires_at: str


class CleanupPhase(str, Enum):
    INTENT = "intent"
    QUARANTINED = "quarantined"
    REMOVING = "removing"
    FINAL_REMOVE_INTENT = "final_remove_intent"
    REMOVED = "removed"
    TERMINAL = "terminal"


class CleanupOutcome(str, Enum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    RETAINED = "retained"
    REFUSED = "refused"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class CleanupIntent:
    cleanup_id: str
    operation_id: str
    preview_id: str
    object_id: str
    expected_object_evidence_digest: str
    expected_reference_digest: str
    final_entry_evidence_digest: Optional[str]
    phase: CleanupPhase
    outcome: Optional[CleanupOutcome]
    reason_code: Optional[str]
    estimated_bytes: Optional[int]
    observed_reclaimed_bytes: Optional[int]
    job_result_digest_before: Optional[str]
    job_result_digest_after: Optional[str]
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


@dataclass(frozen=True)
class LegacyProjection:
    legacy_identity: str
    project_identity: Optional[str]
    relationship_id: Optional[str]
    workspace_id: Optional[str]
    kind: str
    lifecycle: str
    authority_status: str = "legacy_not_owned"
    eligibility: str = "not_authority_candidate"


# Stable reason codes
REASON_CODES: Set[str] = {
    "authority_unavailable",
    "authority_unsupported",
    "authority_unproven",
    "authority_drifted",
    "authority_revision_mismatch",
    "adoption_binding_missing",
    "adoption_binding_prepared",
    "adoption_binding_mismatch",
    "adoption_binding_revoked",
    "promotion_missing",
    "promotion_mismatch",
    "caller_unauthorized",
    "caller_revoked",
    "cross_project_refused",
    "request_invalid",
    "request_id_conflict",
    "policy_not_future",
    "policy_stale",
    "object_unknown",
    "object_not_owned",
    "object_identity_drift",
    "object_replaced",
    "generation_binding_mismatch",
    "generation_already_exists",
    "unstable_capture",
    "storage_exhausted",
    "reference_active",
    "reference_unknown",
    "workspace_active",
    "workspace_lease_active",
    "workspace_index_incomplete",
    "retention_missing",
    "retention_active",
    "preview_incomplete",
    "preview_expired",
    "preview_stale",
    "object_not_previewed",
    "cleanup_already_completed",
    "cleanup_failed",
    "cleanup_indeterminate",
    "transport_unknown",
    "deadline_exceeded",
    "internal_indeterminate",
}
