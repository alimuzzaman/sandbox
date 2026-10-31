"""Explicit operating-system credential-store adapters for secret sources."""

from .base import CredentialHandle, CredentialStore, CredentialStoreError, locator_for_policy

__all__ = [
    "CredentialHandle", "CredentialStore", "CredentialStoreError", "locator_for_policy",
]
