"""Narrow public Feature 051 activation contract."""

from .models import (
    ActivationAuthorityBinding,
    ActivationContractError,
    ActivationPolicy,
    ActivationRequest,
    ActivationResult,
    ForwardRollbackSubject,
    RollbackCompatibilityGrant,
    validate_activation_artifacts,
)
from .service import ActivationService
from .v2_models import (
    ActivationRequestV2, GenerationBoundEdgeReceiptV2,
    PrivateComposeInputSnapshotV2, RollbackCompatibilityGrantV2,
    VerifiedActivationGenerationV2, validate_activation_generation,
)
from .v2_service import ActivationServiceV2

__all__ = [
    "ActivationAuthorityBinding",
    "ActivationContractError",
    "ActivationPolicy",
    "ActivationRequest",
    "ActivationResult",
    "ActivationService",
    "ActivationRequestV2",
    "ActivationServiceV2",
    "GenerationBoundEdgeReceiptV2",
    "PrivateComposeInputSnapshotV2",
    "RollbackCompatibilityGrantV2",
    "VerifiedActivationGenerationV2",
    "validate_activation_generation",
    "ForwardRollbackSubject",
    "RollbackCompatibilityGrant",
    "validate_activation_artifacts",
]
