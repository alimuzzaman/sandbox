"""Credential-store contract and stable, non-secret credential locators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import Literal, Protocol

from sandbox.secrets.models import (
    CredentialLocator,
    CredentialState,
    InfisicalSourcePolicy,
    SecretBrokerError,
)


CREDENTIAL_SERVICE = "sandbox.secret-manager.v1"
_SAFE_MESSAGES = {
    "credential_backend_unavailable": "approved controller credential store is unavailable",
    "credential_locked": "approved controller credential store is locked",
    "credential_missing": "controller credential is not present in the approved store",
    "credential_exists": "controller credential already exists; use explicit replace intent",
    "credential_store_failed": "approved controller credential store operation failed",
}


class CredentialStoreError(SecretBrokerError):
    """A stable credential-store failure that never carries backend detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code, _SAFE_MESSAGES.get(code, _SAFE_MESSAGES["credential_store_failed"]))


def _policy_field(policy: object, attribute: str, config_key: str) -> object:
    if isinstance(policy, Mapping):
        return policy.get(config_key, policy.get(attribute))
    return getattr(policy, attribute, None)


def locator_for_policy(policy: InfisicalSourcePolicy | Mapping[str, object] | object) -> CredentialLocator:
    """Derive one stable locator from the source/project/client identity only."""
    alias = _policy_field(policy, "alias", "alias")
    project_id = _policy_field(policy, "project_id", "projectId")
    client_id = _policy_field(policy, "client_id", "clientId")
    if any(not isinstance(item, str) or not item for item in (alias, project_id, client_id)):
        raise CredentialStoreError("credential_store_failed")
    payload = json.dumps(
        {"version": 1, "source": alias, "project": project_id, "client": client_id},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("ascii")
    account = "v1-" + hashlib.sha256(payload).hexdigest()
    return CredentialLocator(service=CREDENTIAL_SERVICE, account=account)


@dataclass(frozen=True, repr=False)
class CredentialHandle:
    """Opaque adapter handle; only the provider authentication boundary reads it."""

    store: "CredentialStore"
    locator: CredentialLocator

    def read(self) -> str:
        return self.store._read(self.locator)

    def get_secret(self, deadline=None) -> bytes:
        """Read the local credential at the authentication boundary.

        The store lookup is synchronous and does not unlock or prompt. `deadline`
        is accepted for provider compatibility; OS-store calls currently expose
        no portable deadline parameter.
        """
        checker = getattr(deadline, "check", None)
        if callable(checker):
            checker()
        value = self.read()
        if callable(checker):
            checker()
        return value.encode("utf-8")

    def __repr__(self) -> str:
        return "<CredentialHandle>"


class CredentialStore(Protocol):
    """Approved local store interface; no listing, export, or delete operation."""

    def status(self, policy: InfisicalSourcePolicy | Mapping[str, object] | object) -> CredentialState:
        ...

    def get(self, policy: InfisicalSourcePolicy | Mapping[str, object] | object) -> CredentialHandle:
        ...

    def put(
        self,
        policy: InfisicalSourcePolicy | Mapping[str, object] | object,
        value: str,
        *,
        intent: Literal["create", "replace"],
    ) -> None:
        ...

    def _read(self, locator: CredentialLocator) -> str:
        ...
