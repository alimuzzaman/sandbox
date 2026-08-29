"""Fail-closed managed-native isolation mechanisms and contracts."""

from .models import EgressGrant, EgressGrantSet, ManagedIsolationPolicy, NativeCleanupRecovery
from .manifest import MANAGED_ISOLATION_CAPABILITIES, MANAGED_ISOLATION_MATRICES
from .egress_broker import EgressBroker
from .credential_binding import CredentialBinding, CredentialBindingVersionConflict
from .credential_resolver import BrokerLease, SecretReference, SecretReferenceResolver
from .credential_request_broker import (
    BrokerRequest, BrokerResponse, CredentialBrokerError, CredentialRequestBroker,
)
from .credential_upstream import CredentialUpstreamError, VerifiedHttpsUpstream
from .credential_policy import (
    CredentialEgressDecision, CredentialEgressPolicy, binding_egress_allowed,
    evaluate_binding_egress,
)
from .credential_supervisor import (
    BrokerLeaseTransfer, CredentialBrokerSupervisor, CredentialSupervisorError,
)
from .credential_health import CredentialHealthMonitor
from .credential_audit import CredentialAuditError, CredentialAuditLog, LifecycleRecord
from .capability_report import (
    BindingState, CapabilityPrerequisite, CapabilityReport, CapabilityReportError,
    EffectiveObservation,
)

__all__ = ["EgressGrant", "EgressGrantSet", "ManagedIsolationPolicy", "NativeCleanupRecovery",
           "MANAGED_ISOLATION_MATRICES", "MANAGED_ISOLATION_CAPABILITIES",
           "EgressBroker", "CredentialBinding", "CredentialBindingVersionConflict",
           "BrokerLease", "SecretReference", "SecretReferenceResolver", "BindingState",
           "CapabilityPrerequisite", "CapabilityReport", "CapabilityReportError",
           "EffectiveObservation", "BrokerRequest", "BrokerResponse",
           "CredentialBrokerError", "CredentialRequestBroker", "CredentialUpstreamError",
           "VerifiedHttpsUpstream", "CredentialEgressDecision", "CredentialEgressPolicy",
           "binding_egress_allowed", "evaluate_binding_egress", "BrokerLeaseTransfer",
           "CredentialBrokerSupervisor", "CredentialSupervisorError", "CredentialHealthMonitor",
           "CredentialAuditError", "CredentialAuditLog", "LifecycleRecord"]
