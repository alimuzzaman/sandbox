"""Explicit OS credential-store adapter selection."""

from __future__ import annotations

import sys


CREDENTIAL_STORE_MANIFEST = {
    "darwin": "sandbox.secrets.credentials.macos.MacOSKeychainCredentialStore",
    "linux": "sandbox.secrets.credentials.linux.LinuxSecretServiceCredentialStore",
}


def create_credential_store(platform: str | None = None, *, bootstrap_dependencies: bool = True):
    """Instantiate only the platform's approved adapter, without discovery."""
    selected = platform or sys.platform
    if selected == "darwin":
        from .macos import MacOSKeychainCredentialStore
        return MacOSKeychainCredentialStore(bootstrap_dependencies=bootstrap_dependencies)
    if selected.startswith("linux"):
        from .linux import LinuxSecretServiceCredentialStore
        return LinuxSecretServiceCredentialStore(bootstrap_dependencies=bootstrap_dependencies)
    raise RuntimeError("approved controller credential store is unavailable")
