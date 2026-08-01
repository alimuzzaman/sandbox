"""Fail-closed managed-native isolation mechanisms and contracts."""

from .models import EgressGrant, ManagedIsolationPolicy, NativeCleanupRecovery
from .manifest import MANAGED_ISOLATION_MATRICES

__all__ = ["EgressGrant", "ManagedIsolationPolicy", "NativeCleanupRecovery",
           "MANAGED_ISOLATION_MATRICES"]
