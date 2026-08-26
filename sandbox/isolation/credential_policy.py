"""Intersection policy between an exact credential binding and egress grants.

The existing network policy remains authoritative for connectivity.  This
module can only narrow it: a binding is admitted when the already-reconciled
grant set has the expected digest, ownership, lifetime, and an exact hostname
or public-CIDR path for the binding destination.  It never creates or widens a
grant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
from collections.abc import Iterable
from typing import Any

from .credential_binding import CredentialBinding
from .models import EgressGrant, EgressGrantSet, parse_utc_timestamp, public_ipv4_network


@dataclass(frozen=True, repr=False)
class CredentialEgressDecision:
    """Secret-free result of one binding/grant intersection check."""

    allowed: bool
    code: str
    reason: str
    grant_ids: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            "CredentialEgressDecision("
            f"allowed={self.allowed}, code={self.code!r}, grants={len(self.grant_ids)})"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "grant_ids": list(self.grant_ids),
        }


def _deny(code: str, reason: str) -> CredentialEgressDecision:
    return CredentialEgressDecision(False, code, reason)


def _now(value: datetime | None) -> datetime | None:
    instant = value or datetime.now(timezone.utc)
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        return None
    return instant.astimezone(timezone.utc)


def _addresses(values: Iterable[str] | None) -> tuple[ipaddress.IPv4Address, ...] | None:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        return None
    result = []
    try:
        for value in values:
            address = ipaddress.ip_address(str(value))
            if address.version != 4 or not address.is_global:
                return None
            result.append(address)
    except (TypeError, ValueError):
        return None
    return tuple(dict.fromkeys(result))


def _is_mapping(value: Any) -> bool:
    return hasattr(value, "keys") and hasattr(value, "items")


def _document(grants: Any):
    if isinstance(grants, EgressGrantSet):
        return grants.machine_id, grants.base_policy_digest, grants.digest, tuple(grants.grants)
    if isinstance(grants, (str, bytes)) or _is_mapping(grants):
        return None, None, None, None
    try:
        values = tuple(grants)
    except TypeError:
        return None, None, None, None
    if any(not isinstance(grant, EgressGrant) for grant in values):
        return None, None, None, None
    return None, None, None, values


def evaluate_binding_egress(
    binding: CredentialBinding,
    grants: EgressGrantSet | Iterable[EgressGrant],
    *,
    resolved_addresses: Iterable[str] | None = None,
    now: datetime | None = None,
    grant_digest: str | None = None,
) -> CredentialEgressDecision:
    """Return whether the existing egress capability authorizes ``binding``.

    Passing an :class:`EgressGrantSet` is the normal path and verifies its
    machine, base-policy, and computed digest identities.  A bare grant
    iterable is accepted only when the caller supplies its already-verified
    grant-set digest; without that identity the check fails closed.
    """

    if not isinstance(binding, CredentialBinding):
        return _deny("binding_invalid", "credential binding is invalid")
    instant = _now(now)
    if instant is None:
        return _deny("clock_invalid", "egress policy clock is unavailable")
    if binding.state != "ready":
        return _deny("binding_not_ready", "credential binding is not ready")
    try:
        if binding.is_expired(now=instant):
            return _deny("binding_expired", "credential binding has expired")
    except (TypeError, ValueError):
        return _deny("binding_expired", "credential binding expiry is invalid")

    machine_id, base_digest, document_digest, grant_values = _document(grants)
    if grant_values is None:
        return _deny("grant_set_invalid", "egress grant set is invalid")
    expected_digest = document_digest or grant_digest
    if not isinstance(expected_digest, str) or expected_digest != binding.egress_digest:
        return _deny("grant_digest_mismatch", "egress grant identity does not match binding")
    if machine_id is not None and machine_id != binding.instance_id:
        return _deny("grant_owner_denied", "egress grant set is not owned by this instance")
    if base_digest is not None and base_digest != binding.policy_digest:
        return _deny("policy_digest_mismatch", "egress grant set is not bound to the policy")

    addresses = _addresses(resolved_addresses)
    if addresses is None:
        return _deny("resolved_address_invalid", "resolved egress addresses are invalid")

    eligible = []
    for grant in grant_values:
        if grant.owner != binding.instance_id or grant.revoked:
            continue
        try:
            if parse_utc_timestamp(grant.expires_at) <= instant:
                continue
        except (TypeError, ValueError):
            continue
        if binding.port not in grant.ports:
            continue
        if grant.kind == "hostname_https":
            # EgressGrant normalizes DNS names.  The binding model already
            # canonicalizes its host and only permits HTTPS/443.
            if binding.port == 443 and binding.host in grant.destinations:
                eligible.append(grant.grant_id)
            continue
        if grant.kind != "public_cidr_tcp" or not addresses:
            continue
        try:
            networks = tuple(public_ipv4_network(value) for value in grant.destinations)
        except (TypeError, ValueError):
            continue
        if all(any(address in network for network in networks) for address in addresses):
            eligible.append(grant.grant_id)

    if not eligible:
        return _deny("egress_not_authorized", "binding destination is not authorized by egress")
    return CredentialEgressDecision(True, "authorized", "binding destination is covered by egress", tuple(sorted(eligible)))


def binding_egress_allowed(
    binding: CredentialBinding,
    grants: EgressGrantSet | Iterable[EgressGrant],
    *,
    resolved_addresses: Iterable[str] | None = None,
    now: datetime | None = None,
    grant_digest: str | None = None,
) -> bool:
    """Boolean convenience surface for admission callbacks."""

    return evaluate_binding_egress(
        binding, grants, resolved_addresses=resolved_addresses, now=now,
        grant_digest=grant_digest,
    ).allowed


class CredentialEgressPolicy:
    """Reusable instance-scoped evaluator for broker admission."""

    def __init__(self, grants: EgressGrantSet | Iterable[EgressGrant], *, grant_digest: str | None = None,
                 clock=None) -> None:
        self.grants = grants
        self.grant_digest = grant_digest
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, binding: CredentialBinding, *, resolved_addresses: Iterable[str] | None = None):
        return evaluate_binding_egress(
            binding, self.grants, resolved_addresses=resolved_addresses,
            now=self.clock(), grant_digest=self.grant_digest,
        )

    def allowed(self, binding: CredentialBinding, *, resolved_addresses: Iterable[str] | None = None) -> bool:
        return self.check(binding, resolved_addresses=resolved_addresses).allowed


__all__ = [
    "CredentialEgressDecision", "CredentialEgressPolicy", "binding_egress_allowed",
    "evaluate_binding_egress",
]
