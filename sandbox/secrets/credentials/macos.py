"""Explicit macOS Keychain adapter with no keyring backend discovery."""

from __future__ import annotations

import sys
from typing import Literal

from sandbox.secrets.models import CredentialLocator, CredentialState, InfisicalSourcePolicy

from .base import (
    CredentialHandle,
    CredentialStoreError,
    locator_for_policy,
)


class MacOSKeychainCredentialStore:
    """Use keyring's Keychain backend by direct construction, never get_keyring()."""

    def _backend(self):
        if sys.platform != "darwin":
            raise CredentialStoreError("credential_backend_unavailable")
        try:
            from keyring.backends.macOS import Keyring
            return Keyring()
        except ImportError:
            from sandbox.core._config import bootstrap_cli_dependencies
            if bootstrap_cli_dependencies():
                try:
                    from keyring.backends.macOS import Keyring
                    return Keyring()
                except Exception:
                    pass
            raise CredentialStoreError("credential_backend_unavailable") from None
        except Exception:
            raise CredentialStoreError("credential_backend_unavailable") from None

    @staticmethod
    def _validate_value(value: str) -> None:
        if not isinstance(value, str) or not value:
            raise CredentialStoreError("credential_store_failed")

    def status(self, policy: InfisicalSourcePolicy | object) -> CredentialState:
        try:
            locator_for_policy(policy)
            self._backend()
        except CredentialStoreError as exc:
            return (CredentialState.UNAVAILABLE if exc.code == "credential_backend_unavailable"
                    else CredentialState.FAILED)
        except Exception:
            return CredentialState.FAILED
        # Keychain exposes no value-free existence lookup.  Status must never
        # retrieve the bootstrap credential merely to classify its presence.
        return CredentialState.UNKNOWN

    def get(self, policy: InfisicalSourcePolicy | object) -> CredentialHandle:
        return CredentialHandle(self, locator_for_policy(policy))

    def put(
        self,
        policy: InfisicalSourcePolicy | object,
        value: str,
        *,
        intent: Literal["create", "replace"],
    ) -> None:
        self._validate_value(value)
        if intent not in {"create", "replace"}:
            raise CredentialStoreError("credential_store_failed")
        locator = locator_for_policy(policy)
        backend = self._backend()
        try:
            existing = backend.get_password(locator.service, locator.account)
            if intent == "create" and existing is not None:
                raise CredentialStoreError("credential_exists")
            if intent == "replace" and existing is None:
                raise CredentialStoreError("credential_missing")
            backend.set_password(locator.service, locator.account, value)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError("credential_store_failed") from None

    def _read(self, locator: CredentialLocator) -> str:
        try:
            value = self._backend().get_password(locator.service, locator.account)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialStoreError("credential_store_failed") from None
        if value is None:
            raise CredentialStoreError("credential_missing")
        if not isinstance(value, str):
            raise CredentialStoreError("credential_store_failed")
        return value
