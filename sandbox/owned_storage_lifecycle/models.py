"""Owned storage authority lifecycle models and state machines."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class SupportTier(str, Enum):
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    IMPLEMENTED_UNPROVEN = "implemented_unproven"
    PROVEN = "proven"
    DRIFTED = "drifted"


class AcceptanceState(str, Enum):
    PENDING_ORDINARY = "pending_ordinary"
    COMPLETE = "complete"
    FAILED = "failed"


class ReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ReviewPhase(str, Enum):
    RESERVED = "reserved"
    BINDING_PREPARED = "binding_prepared"
    COMMITTED = "committed"
    TERMINAL = "terminal"


class PromotionPhase(str, Enum):
    VALIDATION_PENDING = "validation_pending"
    SUPPORTED = "supported"
    REVOKED = "revoked"


class AcceptancePhase(str, Enum):
    RESERVED = "reserved"
    OBSERVING = "observing"
    EVIDENCE_CLOSED = "evidence_closed"
    COMMITTED = "committed"
    REVOCATION_PENDING = "revocation_pending"
    TERMINAL = "terminal"


class AcceptanceOutcome(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class AuthorityCapability:
    capability_id: str
    remote_identity: str
    platform_mode: str
    support_tier: SupportTier
    adoptable: bool
    service_revision: str
    owner_identity_digest: Optional[str]
    root_identity_digest: Optional[str]
    primitive_states: Dict[str, str]
    evidence_id: Optional[str]
    ordinary_evidence_id: Optional[str]
    acceptance_state: Optional[AcceptanceState]
    observed_at: str
    expires_at: str
    reason_code: str
    promotion_id: Optional[str] = None
    authority_binding_id: Optional[str] = None
    binding_generation: Optional[int] = None


@dataclass(frozen=True)
class CapabilityReviewRequest:
    review_request_id: str
    request_digest: str
    evidence_candidate_id: str
    candidate_close_generation: int
    evidence_digest: str
    cleanup_evidence_digest: str
    source_revision: str
    service_revision: str
    contract_revision: str
    controller_identity_digest: str
    remote_identity: str
    project_identity: str
    fixture_identity: str
    reviewer_identity_digest: str
    requested_decision: ReviewDecision
    proposed_review_decision_id: str
    proposed_promotion_id: str
    proposed_authority_binding_id: str
    expected_binding_digest: str
    lifecycle_generation: int
    phase: ReviewPhase


@dataclass(frozen=True)
class CapabilityReviewDecision:
    review_decision_id: str
    review_request_id: str
    evidence_candidate_id: str
    candidate_close_generation: int
    reviewer_identity_digest: str
    decision: ReviewDecision
    reason_code: str
    request_digest: str
    lifecycle_generation: int
    decided_at: str
    expires_at: str


@dataclass(frozen=True)
class CapabilityPromotion:
    promotion_id: str
    review_decision_id: str
    evidence_candidate_id: str
    capability_id: str
    remote_identity: str
    project_identity: str
    fixture_identity: str
    source_revision: str
    service_revision: str
    contract_revision: str
    evidence_digest: str
    authority_binding_id: str
    binding_generation: int
    phase: PromotionPhase
    support_tier: SupportTier
    adoptable: bool
    request_id: str
    request_digest: str
    promoted_at: str
    expires_at: str


@dataclass(frozen=True)
class CapabilityAcceptanceRequest:
    acceptance_request_id: str
    request_digest: str
    promotion_id: str
    authority_binding_id: str
    reviewer_identity_digest: str
    starting_lifecycle_generation: int
    observed_evidence_digest: Optional[str]
    phase: AcceptancePhase
    outcome: Optional[AcceptanceOutcome]
    reason_code: Optional[str]


@dataclass(frozen=True)
class CapabilityAcceptance:
    acceptance_id: str
    promotion_id: str
    sync_operation_id: str
    ci_operation_id: str
    cleanup_operation_id: str
    policy_id: str
    evidence_id: str
    authority_binding_id: str
    ordinary_evidence_digest: str
    outcome: AcceptanceOutcome
    reason_code: Optional[str]
    request_id: str
    request_digest: str
    lifecycle_generation: int
    completed_at: str


@dataclass(frozen=True)
class CapabilityRevocation:
    revocation_id: str
    remote_identity: str
    project_identity: str
    promotion_id: str
    authority_binding_id: str
    reviewer_identity_digest: str
    request_id: str
    request_digest: str
    lifecycle_generation: int
    reason_code: str
    revoked_at: str
