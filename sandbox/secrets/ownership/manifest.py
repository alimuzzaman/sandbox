"""Explicit CLI-only ownership adapter contract, with no adapter discovery."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Protocol

from sandbox.secrets.models import SecretBrokerError

from .evidence import STORAGE_CONTRACT_CONFIRMED
from .models import CreateOutcome, Existence, Grant, RevocationOutcome


OWNERSHIP_MANIFEST: Mapping[str, Mapping] = MappingProxyType({
    "infisical-v4": MappingProxyType({
        "capabilities": ("read", "create", "revoke"),
        "surfaces": ("cli",),
        "contract_confirmed": STORAGE_CONTRACT_CONFIRMED,
    }),
})


class OwnershipSession(Protocol):
    token_expires_at: float

    def __enter__(self) -> "OwnershipSession": ...
    def __exit__(self, *exc) -> None: ...
    def exists(self, names: Sequence[str]) -> dict[str, Existence]: ...
    def policy_requires_approval(self) -> bool | None: ...
    def create(self, name: str, value: bytearray, *, comment: str,
               before_send: Callable[[], None]) -> CreateOutcome: ...
    def readback_equals(self, name: str, value: bytearray) -> bool | None: ...
    def read(self, names: Sequence[str]) -> dict[str, bytearray]: ...
    def revocation(self) -> RevocationOutcome: ...


class OwnershipAdapter(Protocol):
    def validate_create_inputs(self, grant: Grant, values: Mapping[str, bytearray]) -> None: ...
    def open_session(self, grant: Grant, *, deadline: float) -> OwnershipSession: ...


def load_ownership_adapter(name: str = "infisical-v4") -> OwnershipAdapter:
    """Load the explicit adapter; its create grant checks contract readiness.

    Read-only sessions do not depend on create-storage admission. The grant is
    available at open_session, where create is refused before spawning a worker.
    """
    manifest = OWNERSHIP_MANIFEST.get(name) if isinstance(name, str) else None
    if manifest is None:
        raise SecretBrokerError("provider_contract_unconfirmed", "ownership provider contract is not confirmed")
    from .broker import BrokerOwnershipAdapter
    return BrokerOwnershipAdapter()


__all__ = ["OWNERSHIP_MANIFEST", "OwnershipAdapter", "OwnershipSession", "load_ownership_adapter"]
