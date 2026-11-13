"""Owner-side adapter for genuine, live ControlGate actors on tailnet shares.

This module retains the existing authentication receipt in process. It never
parses actor data or selects policy from a request. Call the synchronous API in
a service worker; async callers use the worker bridge below.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
from typing import Callable

from sandbox.hosting.shared_credentials.authentication import (
    AuthenticatedControlReceipt, AuthenticationError, require_authenticated,
)
from sandbox.installation.binding import (
    read_installation_binding, validate_current,
)

_SEAL = object()
_REVISION = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_BINDING_FIELDS = (
    "schema_version", "installation_id", "incarnation", "machine_identity",
    "ownership_marker", "layout_kind", "runtime_revision", "policy_revision",
    "policy_digest",
)


class ShareActorError(ValueError):
    """Stable safe refusal; never includes identity or owner-policy details."""

    def __init__(self, code: str = "share_actor_invalid"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class OwnerDelegationPolicy:
    """Trusted owner-factory result, distinct from ControlGate's policy epoch.

    ``policy_digest`` is the digest of the immutable delegation policy supplied
    by the trusted factory. It is intentionally not the installation policy
    digest, which is retained separately in the receiver binding.
    """

    revision: str
    policy_digest: str

    def __post_init__(self):
        if (not isinstance(self.revision, str) or not _REVISION.fullmatch(self.revision)
                or not isinstance(self.policy_digest, str)
                or not _DIGEST.fullmatch(self.policy_digest)):
            raise ShareActorError("share_policy_invalid")


@dataclass(frozen=True, slots=True)
class ReceiverBinding:
    """Immutable exact receiver/service authority retained by an actor lease."""

    schema_version: int
    installation_id: str
    incarnation: str
    machine_identity: str
    ownership_marker: str
    layout_kind: str
    runtime_revision: str
    policy_revision: str
    policy_digest: str


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class ValidatedShareActor:
    """Private live lease. Only this module can produce an admitted instance."""

    _receipt: AuthenticatedControlReceipt = field(repr=False)
    principal_id: str
    gate_policy_epoch: str
    policy_revision: str
    policy_digest: str = field(repr=False)
    receiver_binding: ReceiverBinding
    _seal: object = field(repr=False)

    def __repr__(self):
        return "ValidatedShareActor(<private>)"

    def __reduce__(self):
        raise TypeError("share actor leases are not serializable")


def _worker_required() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise ShareActorError("share_actor_worker_required")


def _policy(policy_loader: Callable[[], OwnerDelegationPolicy]) -> OwnerDelegationPolicy:
    if not callable(policy_loader):
        raise ShareActorError("share_policy_unavailable")
    try:
        value = policy_loader()
    except Exception:
        raise ShareActorError("share_policy_unavailable") from None
    if type(value) is not OwnerDelegationPolicy:
        raise ShareActorError("share_policy_invalid")
    return value


def _read_receiver(home) -> ReceiverBinding:
    try:
        # Read and then validate through the installation module's genuine
        # owner/managed authority path. Neither operation initializes state.
        snapshot = read_installation_binding(home=home)
        current = validate_current(snapshot, home=home)
        if type(current) is not dict or type(snapshot) is not dict:
            raise ValueError("invalid binding")
        values = {name: snapshot[name] for name in _BINDING_FIELDS}
        if any(current.get(name) != value for name, value in values.items()):
            raise ValueError("binding changed")
        if (type(values["schema_version"]) is not int
                or values["layout_kind"] not in {"owner", "managed"}
                or not all(isinstance(values[key], str) and values[key]
                           for key in _BINDING_FIELDS if key != "schema_version")):
            raise ValueError("invalid binding")
        return ReceiverBinding(**values)
    except Exception:
        raise ShareActorError("share_receiver_binding_changed") from None


def _make_context(receipt, principal, policy, receiver):
    context = object.__new__(ValidatedShareActor)
    for name, value in (
        ("_receipt", receipt), ("principal_id", principal),
        ("gate_policy_epoch", receipt.policy_epoch),
        ("policy_revision", policy.revision), ("policy_digest", policy.policy_digest),
        ("receiver_binding", receiver), ("_seal", _SEAL),
    ):
        object.__setattr__(context, name, value)
    return context


def admit_share_actor(receipt: AuthenticatedControlReceipt, *, policy_loader,
                      home=None) -> ValidatedShareActor:
    """Admit the actual request-state receipt against current owner authority."""
    _worker_required()
    if type(receipt) is not AuthenticatedControlReceipt:
        raise ShareActorError("share_actor_invalid")
    try:
        principal = require_authenticated(receipt)
    except AuthenticationError:
        raise ShareActorError("share_actor_invalid") from None
    policy = _policy(policy_loader)
    receiver = _read_receiver(home)
    return _make_context(receipt, principal, policy, receiver)


def revalidate_share_actor(context: ValidatedShareActor, *, policy_loader,
                           home=None) -> ValidatedShareActor:
    """Repeat genuine gate, owner-policy and exact receiver checks before use."""
    _worker_required()
    if (type(context) is not ValidatedShareActor or getattr(context, "_seal", None) is not _SEAL
            or type(getattr(context, "_receipt", None)) is not AuthenticatedControlReceipt):
        raise ShareActorError("share_actor_invalid")
    try:
        principal = require_authenticated(context._receipt)
    except AuthenticationError:
        raise ShareActorError("share_actor_invalid") from None
    if (principal != context.principal_id
            or context._receipt.policy_epoch != context.gate_policy_epoch):
        raise ShareActorError("share_actor_invalid")
    policy = _policy(policy_loader)
    if (policy.revision != context.policy_revision
            or policy.policy_digest != context.policy_digest):
        raise ShareActorError("share_policy_changed")
    receiver = _read_receiver(home)
    if receiver != context.receiver_binding:
        raise ShareActorError("share_receiver_binding_changed")
    return context


async def admit_share_actor_async(receipt: AuthenticatedControlReceipt, *, policy_loader,
                                  home=None) -> ValidatedShareActor:
    """Async transport adapter; all synchronous owner reads run in a worker."""
    return await asyncio.to_thread(admit_share_actor, receipt,
                                   policy_loader=policy_loader, home=home)


async def revalidate_share_actor_async(context: ValidatedShareActor, *, policy_loader,
                                       home=None) -> ValidatedShareActor:
    """Async use-time revalidation without blocking the transport event loop."""
    return await asyncio.to_thread(revalidate_share_actor, context,
                                   policy_loader=policy_loader, home=home)


__all__ = [
    "OwnerDelegationPolicy", "ReceiverBinding", "ShareActorError",
    "ValidatedShareActor", "admit_share_actor", "admit_share_actor_async",
    "revalidate_share_actor", "revalidate_share_actor_async",
]
