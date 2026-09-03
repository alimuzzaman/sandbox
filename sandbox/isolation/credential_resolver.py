"""Broker-only resolution of registered secret references.

The resolver intentionally has no plaintext-returning method.  A caller gets a
single-use :class:`BrokerLease`; the secret is supplied only to a trusted
callback and is discarded on return.  Durable bindings and status surfaces
must use :mod:`sandbox.isolation.credential_binding` instead of this module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections.abc import Callable
import re
import threading
import weakref
from typing import Any

from sandbox.secrets.formats import SecretFormatError, parse_secret_document, validate_selector
from sandbox.secrets.models import MAX_VALUE_BYTES, SecretBrokerError
from sandbox.secrets.parser import SecretParseError, parse_document
from sandbox.secrets.policy import validate_key

from .credential_binding import (
    CredentialBinding,
    canonical_registered_source_reference,
    canonical_timestamp,
)


_BINDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_OWNER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,511}$")


def _safe_error(code: str, message: str, *, retryable: bool = False) -> SecretBrokerError:
    return SecretBrokerError(code, message, retryable=retryable)


def _parse_expiry(value: str) -> datetime:
    canonical = canonical_timestamp(value)
    return datetime.fromisoformat(canonical[:-1] + "+00:00")


class SecretReference:
    """A registered source alias and key selector without any source bytes."""

    __slots__ = ("alias", "key", "scope", "canonical")

    def __init__(self, alias: str, key: str, scope: str) -> None:
        if scope not in {"project", "personal"}:
            raise ValueError("credential source scope is invalid")
        canonical = canonical_registered_source_reference(f"{alias}/{key}")
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "canonical", canonical)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("credential source references are immutable")

    def __repr__(self) -> str:
        return f"SecretReference({self.canonical!r}, scope={self.scope!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretReference) and (
            self.alias, self.key, self.scope
        ) == (other.alias, other.key, other.scope)

    def __hash__(self) -> int:
        return hash((self.alias, self.key, self.scope))


class BrokerLease:
    """One-use, process-bound authorization with no serializable value."""

    __slots__ = (
        "_resolver", "_reference", "_binding_id", "_binding_version", "_deadline",
        "_lease_id", "_lock", "_used", "_revoked", "__weakref__",
        "_material", "_snapshot_bound",
    )

    def __init__(
        self,
        resolver: "SecretReferenceResolver",
        reference: SecretReference,
        *,
        binding_id: str,
        binding_version: int,
        deadline: datetime,
        lease_id: str,
        material: bytes | None = None,
        snapshot_bound: bool = False,
    ) -> None:
        if snapshot_bound is not (material is not None):
            raise ValueError("snapshot-bound lease material is invalid")
        self._resolver = resolver
        self._reference = reference
        self._binding_id = binding_id
        self._binding_version = binding_version
        self._deadline = deadline
        self._lease_id = lease_id
        self._lock = threading.Lock()
        self._used = False
        self._revoked = False
        self._material = bytearray(material) if material is not None else None
        self._snapshot_bound = snapshot_bound

    @property
    def binding_id(self) -> str:
        return self._binding_id

    @property
    def binding_version(self) -> int:
        return self._binding_version

    @property
    def lease_id(self) -> str:
        """Opaque diagnostic identity; it is not a credential or source value."""

        return self._lease_id

    @property
    def expires_at(self) -> str:
        timespec = "microseconds" if self._deadline.microsecond else "seconds"
        return self._deadline.isoformat(timespec=timespec).replace("+00:00", "Z")

    def __repr__(self) -> str:
        return (
            "BrokerLease("
            f"binding_id={self._binding_id!r}, version={self._binding_version}, "
            f"lease_id={self._lease_id!r})"
        )

    def invalidate(self) -> None:
        with self._lock:
            self._revoked = True
            if self._material is not None:
                self._material[:] = b"\x00" * len(self._material)
                self._material = None

    revoke = invalidate

    def consume(self, consumer: Callable[[bytes], Any]) -> Any:
        """Run one trusted broker callback with transient credential bytes.

        The lease is consumed before the source is read, so a failed consumer or
        source read cannot be retried with the same authorization.  A callback
        may return a structured broker result, but returning bytes is rejected
        to prevent this seam becoming a plaintext getter.
        """

        if not callable(consumer):
            raise _safe_error("lease_consumer_invalid", "broker lease consumer is invalid")
        detached: bytearray | None = None
        snapshot_bound = False
        with self._lock:
            if self._used:
                raise _safe_error("lease_used", "broker lease has already been consumed")
            if self._revoked:
                raise _safe_error("lease_revoked", "broker lease is revoked")
            if datetime.now(timezone.utc) >= self._deadline:
                self._used = True
                if self._material is not None:
                    self._material[:] = b"\x00" * len(self._material)
                    self._material = None
                raise _safe_error("lease_expired", "broker lease has expired")
            self._used = True
            snapshot_bound = self._snapshot_bound
            if snapshot_bound:
                # Detach the exact revision-bound snapshot while invalidation is
                # excluded by the same lock. A staging lease can never fall back
                # to a later registry read.
                if self._material is None:
                    raise _safe_error("lease_revoked", "broker lease snapshot is unavailable")
                detached = self._material
                self._material = None

        material: bytes | None = None
        transient: bytearray | None = None
        try:
            try:
                if snapshot_bound:
                    material = bytes(detached)
                else:
                    # Legacy generic leases intentionally read only here. They
                    # are distinct from revision-bound staging leases.
                    material = self._resolver._read_reference(self._reference)
            except SecretBrokerError:
                raise
            transient = bytearray(material)
            # The immutable bytes object is passed only to the callback.  The
            # mutable copy is wiped in the finally block below as best-effort
            # process cleanup; the contract makes no universal zeroization claim.
            try:
                result = consumer(bytes(transient))
            except Exception:
                # Do not retain callback exceptions: their attributes or
                # tracebacks may contain the candidate secret.
                raise _safe_error("lease_consumer_failed", "broker lease callback failed") from None
            if isinstance(result, (bytes, bytearray, memoryview)):
                raise _safe_error(
                    "plaintext_denied", "broker lease consumers must return a structured result",
                )
            return result
        finally:
            if transient is not None:
                transient[:] = b"\x00" * len(transient)
            if detached is not None:
                detached[:] = b"\x00" * len(detached)
            material = None


class SecretReferenceResolver:
    """Resolve only registered references for a trusted broker callback."""

    def __init__(
        self,
        registry,
        *,
        allowed_scopes: tuple[str, ...] | set[str] | frozenset[str] = ("project", "personal"),
        owner: str | None = None,
        lease_seconds: int = 30,
        reader: Callable[[SecretReference], bytes] | None = None,
    ) -> None:
        if registry is None or not callable(getattr(registry, "policy", None)) \
                or not callable(getattr(registry, "probe", None)):
            raise ValueError("credential resolver requires a registered source registry")
        if not isinstance(allowed_scopes, (tuple, list, set, frozenset)) \
                or not allowed_scopes or not set(allowed_scopes) <= {"project", "personal"}:
            raise ValueError("credential resolver source scope is invalid")
        if owner is not None and (not isinstance(owner, str) or not _OWNER.fullmatch(owner)):
            raise ValueError("credential resolver owner is invalid")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) \
                or not 1 <= lease_seconds <= 300:
            raise ValueError("credential lease duration is invalid")
        if reader is not None and not callable(reader):
            raise ValueError("credential resolver reader is invalid")
        self.registry = registry
        self.allowed_scopes = frozenset(allowed_scopes)
        self.owner = owner
        self.lease_seconds = lease_seconds
        self._reader = reader
        self._leases: weakref.WeakSet[BrokerLease] = weakref.WeakSet()

    def _parse_reference(self, value: str) -> SecretReference:
        try:
            canonical = canonical_registered_source_reference(value)
        except ValueError:
            raise _safe_error("reference_invalid", "credential source reference is invalid") from None
        alias, key = canonical.split("/", 1)
        try:
            policy = self.registry.policy(alias)
        except SecretBrokerError:
            raise _safe_error("source_unknown", "credential source alias is not registered") from None
        except Exception:
            raise _safe_error("source_unknown", "credential source alias is not registered") from None
        if policy.scope not in self.allowed_scopes:
            raise _safe_error("source_scope_denied", "credential source scope is not authorized")
        if self.owner is not None:
            configured_owner = self._configured_owner(alias)
            if configured_owner != self.owner:
                raise _safe_error("source_owner_denied", "credential source owner is not authorized")
        try:
            if policy.format == "dotenv":
                validate_key(key)
            else:
                validate_selector(key)
        except Exception:
            raise _safe_error("reference_invalid", "credential source key selector is invalid") from None
        return SecretReference(alias, key, policy.scope)

    def _configured_owner(self, alias: str) -> str:
        # SourceRegistry intentionally keeps the path private.  Ownership is a
        # separate non-secret descriptor and is read only when a caller opts in
        # to an owner-constrained resolver.  Older registries have no explicit
        # owner, so their scope is the only safe identity available.
        sources = getattr(self.registry, "_sources", {})
        item = sources.get(alias) if isinstance(sources, dict) else None
        if isinstance(item, dict) and isinstance(item.get("owner"), str):
            return item["owner"]
        return self.registry.policy(alias).scope

    def _read_reference(self, reference: SecretReference) -> bytes:
        """Internal source read used only by :meth:`BrokerLease.consume`."""

        if self._reader is not None:
            try:
                value = self._reader(reference)
            except SecretBrokerError:
                # A registered adapter is not allowed to surface arbitrary
                # broker errors: their text could contain source material.
                raise _safe_error(
                    "source_unavailable", "registered credential source is unavailable",
                ) from None
            except Exception:
                raise _safe_error("source_unavailable", "registered credential source is unavailable") from None
            if not isinstance(value, (bytes, bytearray)) or not value:
                raise _safe_error("source_invalid", "registered credential source returned no usable value")
            if len(value) > MAX_VALUE_BYTES:
                raise _safe_error("source_too_large", "registered credential value exceeds the broker limit")
            return bytes(value)

        try:
            safe = self.registry.read(reference.alias)
            if safe.policy.format == "dotenv":
                document = parse_document(safe.content)
            else:
                document = parse_secret_document(safe.content, safe.policy.format)
            record = document.entries.get(reference.key)
            value = getattr(record, "value", None) if record is not None else None
            if not isinstance(value, str) or not value:
                raise _safe_error("source_key_missing", "registered credential key is unavailable")
            encoded = value.encode("utf-8")
            if len(encoded) > MAX_VALUE_BYTES:
                raise _safe_error("source_too_large", "registered credential value exceeds the broker limit")
            return encoded
        except SecretBrokerError:
            # Keep source-adapter diagnostics bounded even when a lower layer
            # accidentally includes an implementation detail or value.
            raise _safe_error(
                "source_unavailable", "registered credential source is unavailable",
            ) from None
        except (SecretParseError, SecretFormatError, UnicodeError):
            raise _safe_error("source_invalid", "registered credential source could not be read safely") from None
        except Exception:
            raise _safe_error("source_unavailable", "registered credential source is unavailable") from None

    def issue(
        self,
        binding_or_reference: CredentialBinding | str,
        *,
        binding_id: str | None = None,
        binding_version: int | None = None,
        expires_at: str | None = None,
        owner: str | None = None,
        state: str = "ready",
    ) -> BrokerLease:
        """Issue one lease after metadata, state, and source checks.

        Passing a :class:`CredentialBinding` is preferred.  The explicit
        reference form exists for the broker's internal hand-off and still
        requires all binding identity/version/expiry fields; it cannot be used
        as a general secret lookup.
        """

        if isinstance(binding_or_reference, CredentialBinding):
            binding = binding_or_reference
            if binding.state != "ready":
                raise _safe_error("binding_not_ready", "credential binding is not ready")
            if binding.is_expired():
                raise _safe_error("binding_expired", "credential binding has expired")
            if self.owner is not None and binding.owner != self.owner:
                raise _safe_error("binding_owner_denied", "credential binding owner is not authorized")
            reference_value = binding.source_reference
            binding_id = binding.binding_id
            binding_version = binding.version
            expires_at = binding.expires_at
            state = binding.state
        else:
            if binding_id is None or binding_version is None or expires_at is None:
                raise _safe_error("binding_required", "credential lease requires binding metadata")
            if state != "ready":
                raise _safe_error("binding_not_ready", "credential binding is not ready")
            reference_value = binding_or_reference

        if not isinstance(binding_id, str) or not binding_id:
            raise _safe_error("binding_invalid", "credential binding identity is invalid")
        if not _BINDING_ID.fullmatch(binding_id):
            raise _safe_error("binding_invalid", "credential binding identity is invalid")
        if isinstance(binding_version, bool) or not isinstance(binding_version, int) \
                or binding_version < 1:
            raise _safe_error("binding_invalid", "credential binding version is invalid")
        try:
            deadline = _parse_expiry(expires_at)
        except (TypeError, ValueError):
            raise _safe_error("binding_invalid", "credential binding expiry is invalid") from None
        now = datetime.now(timezone.utc)
        if deadline <= now:
            raise _safe_error("binding_expired", "credential binding has expired")
        if owner is not None:
            if not _OWNER.fullmatch(owner):
                raise _safe_error("binding_owner_denied", "credential binding owner is not authorized")
            if self.owner is not None and owner != self.owner:
                raise _safe_error("binding_owner_denied", "credential binding owner is not authorized")
        reference = self._parse_reference(reference_value)
        try:
            observation = self.registry.probe(reference.alias)
        except SecretBrokerError:
            raise _safe_error("source_unavailable", "registered credential source is unavailable") from None
        except Exception:
            raise _safe_error("source_unavailable", "registered credential source is unavailable") from None
        if not isinstance(observation, dict) or observation.get("safety") != "safe" \
                or observation.get("broker_readable") is not True:
            raise _safe_error("source_unavailable", "registered credential source is not broker-readable")
        lease_deadline = min(deadline, now + timedelta(seconds=self.lease_seconds))
        # The lease identifier is intentionally opaque and has no relationship
        # to the source value.  ``secrets`` is not used because this identity is
        # diagnostic only and never authorizes a second lookup.
        import uuid

        lease = BrokerLease(
            self, reference, binding_id=binding_id, binding_version=binding_version,
            deadline=lease_deadline, lease_id=f"lease-{uuid.uuid4().hex}",
        )
        self._leases.add(lease)
        return lease

    lease = issue

    def issue_revision_bound(self, binding: CredentialBinding, *, expected_revision: str,
                             revision_key: bytes) -> BrokerLease:
        """Atomically bind one source snapshot revision to one-use lease bytes.

        The source is read exactly once. Its opaque revision and the selected
        credential are derived from that same immutable ``SafeSource`` snapshot;
        later consume never reopens the source.
        """
        import hmac
        import uuid
        from sandbox.secrets.writer import opaque_revision

        if not isinstance(binding, CredentialBinding) or not isinstance(expected_revision, str) \
                or not isinstance(revision_key, bytes):
            raise _safe_error("binding_invalid", "revision-bound credential lease is invalid")
        if binding.state != "ready":
            raise _safe_error("binding_not_ready", "credential binding is not ready")
        if binding.is_expired():
            raise _safe_error("binding_expired", "credential binding has expired")
        if self.owner is not None and binding.owner != self.owner:
            raise _safe_error("binding_owner_denied", "credential binding owner is not authorized")
        reference = self._parse_reference(binding.source_reference)
        try:
            observation = self.registry.probe(reference.alias)
            if not isinstance(observation, dict) or observation.get("safety") != "safe" \
                    or observation.get("broker_readable") is not True:
                raise _safe_error("source_unavailable", "registered credential source is not broker-readable")
            safe = self.registry.read(reference.alias)
            current_revision = opaque_revision(revision_key, safe.content)
        except SecretBrokerError:
            raise _safe_error("source_unavailable", "registered credential source is unavailable") from None
        if not hmac.compare_digest(current_revision, expected_revision):
            raise _safe_error("revision_conflict", "staging credential source revision changed")
        try:
            if safe.policy.format == "dotenv":
                document = parse_document(safe.content)
            else:
                document = parse_secret_document(safe.content, safe.policy.format)
            record = document.entries.get(reference.key)
            value = getattr(record, "value", None) if record is not None else None
            if not isinstance(value, str) or not value:
                raise _safe_error("source_key_missing", "registered credential key is unavailable")
            material = value.encode("utf-8")
            if len(material) > MAX_VALUE_BYTES:
                raise _safe_error("source_too_large", "registered credential value exceeds the broker limit")
        except SecretBrokerError:
            raise
        except (SecretParseError, SecretFormatError, UnicodeError):
            raise _safe_error("source_invalid", "registered credential source could not be read safely") from None
        now = datetime.now(timezone.utc)
        deadline = min(_parse_expiry(binding.expires_at),
                       now + timedelta(seconds=self.lease_seconds))
        lease = BrokerLease(self, reference, binding_id=binding.binding_id,
            binding_version=binding.version, deadline=deadline,
            lease_id=f"lease-{uuid.uuid4().hex}", material=material,
            snapshot_bound=True)
        self._leases.add(lease)
        return lease

    def observe_reference_revision(self, binding: CredentialBinding, *,
                                   revision_key: bytes) -> str:
        """Prove one registered key and return only its opaque source revision."""
        from sandbox.secrets.writer import opaque_revision
        if not isinstance(binding, CredentialBinding) or not isinstance(revision_key, bytes) \
                or len(revision_key) != 32 or binding.state != "ready" \
                or binding.is_expired() or (self.owner is not None and binding.owner != self.owner):
            raise _safe_error("binding_invalid", "revision-bound credential metadata is invalid")
        reference = self._parse_reference(binding.source_reference)
        try:
            observation = self.registry.probe(reference.alias)
            if not isinstance(observation, dict) or observation.get("safety") != "safe" \
                    or observation.get("broker_readable") is not True:
                raise _safe_error("source_unavailable", "registered credential source is unavailable")
            safe = self.registry.read(reference.alias)
            document = (parse_document(safe.content) if safe.policy.format == "dotenv"
                        else parse_secret_document(safe.content, safe.policy.format))
            record = document.entries.get(reference.key)
            value = getattr(record, "value", None) if record is not None else None
            if not isinstance(value, str) or not value:
                raise _safe_error("source_key_missing", "registered credential key is unavailable")
            return opaque_revision(revision_key, safe.content)
        except SecretBrokerError:
            raise
        except Exception:
            raise _safe_error("source_unavailable", "registered credential source is unavailable") from None

    def invalidate(self, binding_id: str, *, binding_version: int | None = None) -> int:
        """Invalidate outstanding leases for a binding before durable revoke."""

        count = 0
        for lease in tuple(self._leases):
            if lease.binding_id != binding_id:
                continue
            if binding_version is not None and lease.binding_version != binding_version:
                continue
            lease.invalidate()
            count += 1
        return count

    def resolve(self, *_args: Any, **_kwargs: Any) -> None:
        """Explicitly deny the tempting plaintext-returning API."""

        raise _safe_error("plaintext_denied", "credential resolver has no plaintext-return operation")


# The longer name is the contract name; this alias keeps the managed-runtime
# seam convenient for callers that refer to it as a credential resolver.
CredentialResolver = SecretReferenceResolver


__all__ = ["BrokerLease", "CredentialResolver", "SecretReference", "SecretReferenceResolver"]
