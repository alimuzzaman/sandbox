"""In-process ControlGate provenance, separate from credential admission.

Only the gate owns an issuer. Receipts cannot cross a wire or survive restart.
Synchronous validation is for service workers; async handlers must await
``validate_current_async``. Neither validation grants credential consumption.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Callable, Literal


class AuthenticationError(ValueError):
    """Bounded denial without underlying identity-service exception details."""

    def __init__(self, code: str = "control_receipt_invalid"):
        self.code = code
        super().__init__(code)


_SEAL = object()


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False, repr=False, eq=False)
class AuthenticatedControlReceipt:
    via: Literal["token", "tailnet"]
    service_principal: str | None
    login: str | None
    node: str | None
    policy_epoch: str
    _issuer: _ControlReceiptIssuer = field(repr=False)
    _seal: object = field(repr=False)

    def __repr__(self) -> str:
        return "AuthenticatedControlReceipt(<private>)"

    def __reduce__(self):
        raise TypeError("control receipts are not serializable")

    @property
    def principal_id(self) -> str:
        """Collision-free nonsecret ledger partition, never a bearer fingerprint."""
        _validate_issued(self)
        return json.dumps([self.via, self.service_principal, self.login, self.node],
                          ensure_ascii=True, separators=(",", ":"))

    def validate_current(self) -> None:
        """Recheck the owning gate's current policy in a synchronous worker."""
        _validate_issued(self)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AuthenticationError("control_worker_required")
        try:
            allowed = self._issuer._validator(self)
        except Exception:
            allowed = False
        if allowed is not True:
            raise AuthenticationError("control_policy_denied") from None

    async def validate_current_async(self) -> None:
        await asyncio.to_thread(self.validate_current)


class _ControlReceiptIssuer:
    """Private gate port; never expose this instance to transport consumers."""

    def __init__(self, validator: Callable[[AuthenticatedControlReceipt], bool]):
        self._validator = validator

    def _issue(self, *, via: Literal["token", "tailnet"], policy_epoch: str,
               service_principal: str | None = None, login: str | None = None,
               node: str | None = None) -> AuthenticatedControlReceipt:
        receipt = object.__new__(AuthenticatedControlReceipt)
        for name, value in (("via", via), ("policy_epoch", policy_epoch),
                            ("service_principal", service_principal), ("login", login),
                            ("node", node), ("_issuer", self), ("_seal", _SEAL)):
            object.__setattr__(receipt, name, value)
        return receipt


def _validate_issued(receipt: AuthenticatedControlReceipt) -> None:
    if (type(receipt) is not AuthenticatedControlReceipt
            or getattr(receipt, "_seal", None) is not _SEAL
            or type(getattr(receipt, "_issuer", None)) is not _ControlReceiptIssuer):
        raise AuthenticationError()


def require_authenticated(receipt: AuthenticatedControlReceipt) -> str:
    """Validate authentic current admission and return its nonsecret principal."""
    _validate_issued(receipt)
    receipt.validate_current()
    return receipt.principal_id


__all__ = ["AuthenticatedControlReceipt", "AuthenticationError", "require_authenticated"]
