"""Fail-closed managed-native isolation mechanisms and contracts."""

from .models import EgressGrant, EgressGrantSet, ManagedIsolationPolicy, NativeCleanupRecovery
from .manifest import MANAGED_ISOLATION_CAPABILITIES, MANAGED_ISOLATION_MATRICES
from .egress_broker import EgressBroker
from .credential_binding import CredentialBinding, CredentialBindingVersionConflict
from .credential_resolver import BrokerLease, SecretReference, SecretReferenceResolver
from .capability_report import (
    BindingState, CapabilityPrerequisite, CapabilityReport, CapabilityReportError,
    EffectiveObservation,
)

__all__ = ["EgressGrant", "EgressGrantSet", "ManagedIsolationPolicy", "NativeCleanupRecovery",
           "MANAGED_ISOLATION_MATRICES", "MANAGED_ISOLATION_CAPABILITIES",
           "EgressBroker", "CredentialBinding", "CredentialBindingVersionConflict",
           "BrokerLease", "SecretReference", "SecretReferenceResolver", "BindingState",
           "CapabilityPrerequisite", "CapabilityReport", "CapabilityReportError",
           "EffectiveObservation"]
