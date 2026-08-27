"""Immutable, exact-scope credential binding contracts.

This module contains only desired-state metadata.  It deliberately has no
source reader, process launcher, or HTTP client.  Keeping those concerns out of
the binding model makes it possible to validate and persist a binding without
ever resolving credential bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import ipaddress
import re
from typing import Any, Mapping


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REFERENCE_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REFERENCE_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_OPAQUE_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{2,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_OWNER = re.compile(r"^[A-Za-z0-9/][A-Za-z0-9._:/-]{0,511}$")

ALLOWED_METHODS = frozenset({
    "GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
})
ALLOWED_AUTH_FORMS = frozenset({
    "bearer", "api_key", "authorization_bearer", "x_api_key",
})
LIFECYCLE_STATES = frozenset({
    "unconfigured", "credential_pending", "ready", "revoking", "revoked",
    "expired", "blocked",
})

_TRANSITIONS = {
    "unconfigured": frozenset({"credential_pending", "revoked"}),
    "credential_pending": frozenset({"ready", "blocked", "revoked", "expired"}),
    "ready": frozenset({"revoking", "expired", "credential_pending", "blocked"}),
    "revoking": frozenset({"revoked", "blocked"}),
    "revoked": frozenset({"credential_pending"}),
    "expired": frozenset({"credential_pending"}),
    "blocked": frozenset({"credential_pending", "revoked"}),
}

_FIELDS = frozenset({
    "binding_id", "instance_id", "source_reference", "policy_digest",
    "egress_digest", "broker_digest", "scheme", "host", "port", "method",
    "path", "auth_form", "expires_at", "owner", "version", "state",
})
_MUTABLE_FIELDS = _FIELDS - {"binding_id", "instance_id", "version", "state"}


def _safe_text(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"credential {label} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"credential {label} is invalid")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"credential {label} is invalid")
    return value


def canonical_source_reference(value: Any) -> str:
    """Validate an opaque approved source reference.

    New registered-source references use ``source/key``.  A pre-registered
    opaque reference (for example ``ref:test:credential-vault:fixture``) is
    also retained as metadata and is resolved only by a future explicit source
    adapter.  In both forms, path traversal, URL syntax, shell assignment
    syntax, empty segments, and whitespace are refused.
    """

    if not isinstance(value, str) or value != value.strip() or len(value) > 256:
        raise ValueError("credential source reference is invalid")
    if "/" in value:
        pieces = value.split("/")
        if len(pieces) < 2 or len(pieces) > 8 \
                or not _REFERENCE_ALIAS.fullmatch(pieces[0]) \
                or any(not _REFERENCE_KEY.fullmatch(piece) or piece in {".", ".."}
                       for piece in pieces[1:]):
            raise ValueError("credential source reference is invalid")
    elif not _OPAQUE_REFERENCE.fullmatch(value) or value.startswith((".", "-")):
        raise ValueError("credential source reference is invalid")
    return value


def canonical_registered_source_reference(value: Any) -> str:
    """Validate the resolver's concrete two-part ``source/key`` form."""

    canonical = canonical_source_reference(value)
    pieces = canonical.split("/")
    if len(pieces) != 2 or not _REFERENCE_ALIAS.fullmatch(pieces[0]) \
            or not _REFERENCE_KEY.fullmatch(pieces[1]):
        raise ValueError("credential source reference is not registered-source form")
    return canonical


def canonical_binding_id(value: Any) -> str:
    return _safe_text(value, label="binding id", pattern=_OPAQUE_ID)


def canonical_instance_id(value: Any) -> str:
    return _safe_text(value, label="instance id", pattern=_OPAQUE_ID)


def canonical_owner(value: Any) -> str:
    if not isinstance(value, str) or not _OWNER.fullmatch(value):
        raise ValueError("credential owner is invalid")
    if value.startswith("/"):
        path, separator, label = value.partition("::")
        if (not separator or not label or path == "/" or path.endswith("/")
                or "//" in path or any(piece in {"", ".", ".."}
                                       for piece in path.split("/")[1:])):
            raise ValueError("credential owner is invalid")
    return value


def canonical_auth_profile(value: Any) -> str:
    if value not in {"authorization_bearer", "x_api_key"}:
        raise ValueError("credential authentication profile is not approved")
    return value


def canonical_timestamp(value: Any) -> str:
    """Return an unambiguous UTC timestamp without retaining caller spelling."""

    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError("credential expiry is invalid")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError("credential expiry is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("credential expiry must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    timespec = "microseconds" if parsed.microsecond else "seconds"
    return parsed.isoformat(timespec=timespec).replace("+00:00", "Z")


def canonical_host(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 253:
        raise ValueError("credential host is invalid")
    host = value.lower()
    if host.endswith("."):
        host = host[:-1]
    if not host or "://" in host or any(character in host for character in "/\\@?:#%"):
        raise ValueError("credential host is invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # Normal DNS names are not accepted by ``ip_address``.
        pass
    else:
        raise ValueError("credential host must be a DNS name")
    labels = host.split(".")
    if (len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels)
            or not any(character.isalpha() for character in labels[-1])):
        raise ValueError("credential host is invalid")
    return host


def canonical_path(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip() \
            or len(value) > 4096 or not value.startswith("/"):
        raise ValueError("credential path is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("credential path is invalid")
    if any(character in value for character in "?#\\"):
        raise ValueError("credential path is invalid")
    # Encoded separators and dot segments are ambiguous across HTTP clients.
    if re.search(r"%(?:2f|2F|5c|5C|2e|2E)", value):
        raise ValueError("credential path is invalid")
    if "//" in value or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("credential path is invalid")
    for match in re.finditer(r"%(..)", value):
        if not re.fullmatch(r"[0-9A-Fa-f]{2}", match.group(1)):
            raise ValueError("credential path is invalid")
    return value


class CredentialBindingVersionConflict(ValueError):
    """The caller attempted to update a binding from a stale version."""

    code = "binding_version_conflict"


@dataclass(frozen=True, repr=False)
class CredentialBinding:
    """One instance-scoped, exact outbound credential authorization."""

    binding_id: str
    instance_id: str
    source_reference: str
    policy_digest: str
    egress_digest: str
    broker_digest: str
    scheme: str
    host: str
    port: int
    method: str
    path: str
    auth_form: str
    expires_at: str
    owner: str
    version: int = 1
    state: str = "credential_pending"

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _safe_text(
            self.binding_id, label="binding id", pattern=_OPAQUE_ID,
        ))
        object.__setattr__(self, "instance_id", _safe_text(
            self.instance_id, label="instance id", pattern=_OPAQUE_ID,
        ))
        object.__setattr__(self, "source_reference", canonical_source_reference(
            self.source_reference,
        ))
        for name in ("policy_digest", "egress_digest", "broker_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))

        if not isinstance(self.scheme, str) or self.scheme.lower() != "https":
            raise ValueError("credential scheme must be HTTPS")
        object.__setattr__(self, "scheme", "https")
        object.__setattr__(self, "host", canonical_host(self.host))
        if isinstance(self.port, bool) or not isinstance(self.port, int) or self.port != 443:
            raise ValueError("credential port must be 443")
        method = self.method.upper() if isinstance(self.method, str) else self.method
        if method not in ALLOWED_METHODS:
            raise ValueError("credential method is not approved")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", canonical_path(self.path))
        if self.auth_form not in ALLOWED_AUTH_FORMS:
            raise ValueError("credential authentication form is not approved")
        expires_at = canonical_timestamp(self.expires_at)
        object.__setattr__(self, "expires_at", expires_at)

        object.__setattr__(self, "owner", canonical_owner(self.owner))
        if isinstance(self.version, bool) or not isinstance(self.version, int) \
                or self.version < 1:
            raise ValueError("credential binding version is invalid")
        if self.state not in LIFECYCLE_STATES:
            raise ValueError("credential binding state is invalid")

    def __repr__(self) -> str:
        # Deliberately omit even the source reference from generic debugging
        # output.  ``inspect``/``to_dict`` expose the opaque reference only to
        # an already-authorized status surface.
        return (
            "CredentialBinding("
            f"binding_id={self.binding_id!r}, instance_id={self.instance_id!r}, "
            f"version={self.version}, state={self.state!r})"
        )

    @property
    def auth_profile(self) -> str:
        """Compatibility name used by the broker contract."""

        return {
            "bearer": "authorization_bearer",
            "authorization_bearer": "authorization_bearer",
            "api_key": "x_api_key",
            "x_api_key": "x_api_key",
        }[self.auth_form]

    @property
    def expired(self) -> bool:
        return self.is_expired()

    def is_expired(self, *, now: datetime | None = None) -> bool:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("credential expiry comparison requires a timezone")
        expiry = datetime.fromisoformat(self.expires_at[:-1] + "+00:00")
        return instant.astimezone(timezone.utc) >= expiry

    def admits_use(self, *, now: datetime | None = None) -> bool:
        return self.state == "ready" and not self.is_expired(now=now)

    def scope(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme, "host": self.host, "port": self.port,
            "method": self.method, "path": self.path, "auth_form": self.auth_form,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "instance_id": self.instance_id,
            "source_reference": self.source_reference,
            "policy_digest": self.policy_digest,
            "egress_digest": self.egress_digest,
            "broker_digest": self.broker_digest,
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "method": self.method,
            "path": self.path,
            "auth_form": self.auth_form,
            "expires_at": self.expires_at,
            "owner": self.owner,
            "version": self.version,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CredentialBinding":
        if not isinstance(value, Mapping) or set(value) != _FIELDS:
            raise ValueError("credential binding document is invalid")
        return cls(**{key: value[key] for key in _FIELDS})

    def transition(self, state: str, *, now: datetime | None = None) -> "CredentialBinding":
        if state not in LIFECYCLE_STATES:
            raise ValueError("credential binding state is invalid")
        if state == self.state:
            return self
        if self.state in {"revoked", "expired"} and state == "credential_pending":
            raise ValueError("a new versioned authorization is required")
        if state not in _TRANSITIONS[self.state]:
            raise ValueError("credential binding transition is not allowed")
        if state == "ready" and self.is_expired(now=now):
            raise ValueError("expired credential binding cannot become ready")
        return replace(self, state=state, version=self.version + 1)

    def expire(self, *, now: datetime | None = None) -> "CredentialBinding":
        if self.state == "expired":
            return self
        if self.state not in {"credential_pending", "ready"}:
            raise ValueError("credential binding cannot expire from its current state")
        # ``now`` is a test seam; callers cannot use it to make a ready binding
        # appear valid because transition to ready rechecks the real deadline.
        if now is not None and now.tzinfo is None:
            raise ValueError("credential expiry comparison requires a timezone")
        return replace(self, state="expired", version=self.version + 1)

    def begin_revoke(self) -> "CredentialBinding":
        if self.state == "revoked":
            return self
        if self.state == "revoking":
            return self
        if self.state not in {"ready", "credential_pending", "blocked"}:
            raise ValueError("credential binding cannot be revoked from its current state")
        if self.state == "ready":
            return self.transition("revoking")
        return self.transition("revoked")

    def revoke(self) -> "CredentialBinding":
        """Close admission and return the completed revoked version.

        A ready binding takes the explicit ``ready -> revoking -> revoked``
        path.  The request broker can retain the intermediate object while
        draining active sessions; this convenience method is for durable
        state updates where the drain has already completed.
        """

        current = self.begin_revoke()
        if current.state == "revoking":
            current = current.transition("revoked")
        return current

    def cas_update(self, expected_version: int, **changes: Any) -> "CredentialBinding":
        if isinstance(expected_version, bool) or expected_version != self.version:
            raise CredentialBindingVersionConflict("credential binding version does not match")
        unknown = set(changes) - _MUTABLE_FIELDS
        if unknown:
            raise ValueError("credential binding update contains unknown fields")
        if not changes:
            raise ValueError("credential binding update is empty")
        values = self.to_dict()
        values.update(changes)
        # Every scope/reference mutation invalidates the old lease and starts
        # in the fail-closed pending state.  State cannot be caller-selected.
        values["state"] = "credential_pending"
        values["version"] = self.version + 1
        return type(self).from_dict(values)


__all__ = [
    "ALLOWED_AUTH_FORMS", "ALLOWED_METHODS", "CredentialBinding",
    "CredentialBindingVersionConflict", "LIFECYCLE_STATES",
    "canonical_host", "canonical_path", "canonical_registered_source_reference",
    "canonical_source_reference",
    "canonical_timestamp",
]
