"""Explicit OS credential-store adapter selection."""

from __future__ import annotations

import sys


CREDENTIAL_STORE_MANIFEST = {
    "darwin": "sandbox.secrets.credentials.macos.MacOSKeychainCredentialStore",
    "linux": "sandbox.secrets.credentials.linux.LinuxSecretServiceCredentialStore",
}


def create_credential_store(platform: str | None = None):
    """Instantiate only the platform's approved adapter, without discovery."""
    selected = platform or sys.platform
    if selected == "darwin":
        from .macos import MacOSKeychainCredentialStore
        return MacOSKeychainCredentialStore()
    if selected.startswith("linux"):
        from .linux import LinuxSecretServiceCredentialStore
        return LinuxSecretServiceCredentialStore()
    raise RuntimeError("approved controller credential store is unavailable")
