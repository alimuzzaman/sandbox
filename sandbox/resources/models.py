from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
import threading
from typing import Any, Iterable, Mapping


TARGET_KINDS = frozenset({"local", "remote"})
CLASSIFICATIONS = frozenset({
    "active", "retained", "disposable_cache", "stale_candidate",
    "unverified", "unmanaged",
})
SIZE_STATES = frozenset({"measured", "not_measured", "timed_out", "unavailable"})
RECLAIM_TIERS = ("safe", "tmp", "all")
PLAN_SCOPES = frozenset({"cache", "stale", *RECLAIM_TIERS})
PLAN_STATES = frozenset({
    "planned", "in_progress", "completed", "indeterminate", "expired",
})
RUN_STATES = frozenset({"completed", "partial", "indeterminate", "refused"})
ITEM_STATES = frozenset({"removed", "skipped", "failed", "timed_out", "already_absent"})
NETWORK_LIFECYCLES = frozenset({"active", "idle", "orphaned", "indeterminate"})
NETWORK_ALLOCATION_STATES = frozenset({"allocated", "available", "exhausted", "unknown"})
NETWORK_OWNER_KINDS = frozenset({"workspace", "sandbox", "foreign", "unknown", "unmanaged"})
NETWORK_REFERENCE_KINDS = frozenset({"leases", "containers", "jobs", "mounts"})
_SECRET = re.compile(
    r"(?i)(token|password|passphrase|authorization|cookie|credential|secret)"
    r"\s*[=:]\s*\S+"
)
_SECRET_KEY = re.compile(
    r"(?i)(token|password|passphrase|authorization|cookie|credential|secret)"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SECRET_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def _non_negative(value: int | None, name: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def normalize_active_references(value: Mapping[str, Any] | Iterable[tuple[str, Any]] | None) -> tuple[tuple[str, int | None], ...]:
    """Normalize lifecycle references without treating missing evidence as zero."""
    if value is None:
        return ()
    items = value.items() if isinstance(value, Mapping) else value
    normalized: dict[str, int | None] = {}
    try:
        for key, count in items:
            if not isinstance(key, str) or not key:
                raise ValueError("reference names must be non-empty strings")
            if count is not None:
                _non_negative(count, f"active_references.{key}")
            normalized[key] = count
    except (TypeError, ValueError):
        raise ValueError("active_references must be a mapping of counts") from None
    return tuple(sorted(normalized.items()))


def _references_dict(value: Iterable[tuple[str, int | None]]) -> dict[str, int | None]:
    return {key: count for key, count in value}


@dataclass(frozen=True)
class NetworkLifecycle:
    """Canonical, read-only network ownership/lifecycle evidence.

    The model is shared by local and named-remote resource adapters.  It is
    deliberately incapable of enabling network cleanup: ``cleanup_eligible``
    is always false and release decisions remain diagnostic until an explicit
    lifecycle owner authorizes a separate operation.
    """

    network_id: str
    owner_kind: str
    owner_id: str | None
    lifecycle: str
    active_references: tuple[tuple[str, int | None], ...] = ()
    allocation_state: str = "unknown"
    allocation_pool: str | None = None
    last_observed: str | None = None
    evidence: tuple[str, ...] = ()
    cleanup_eligible: bool = False
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.network_id, str) or not self.network_id:
            raise ValueError("network_id is required")
        if self.owner_kind not in NETWORK_OWNER_KINDS:
            raise ValueError("invalid network owner kind")
        if self.owner_kind in {"workspace", "sandbox"} and (
            not isinstance(self.owner_id, str) or not self.owner_id
        ):
            raise ValueError("owned networks require an owner identity")
        if self.lifecycle not in NETWORK_LIFECYCLES:
            raise ValueError("invalid network lifecycle")
        if self.allocation_state not in NETWORK_ALLOCATION_STATES:
            raise ValueError("invalid network allocation state")
        if self.allocation_pool is not None and (
            not isinstance(self.allocation_pool, str) or not self.allocation_pool
        ):
            raise ValueError("allocation_pool must be a non-empty string or null")
        refs = normalize_active_references(self.active_references)
        object.__setattr__(self, "active_references", refs)
        if not isinstance(self.cleanup_eligible, bool):
            raise ValueError("cleanup_eligible must be a boolean")
        if self.cleanup_eligible:
            raise ValueError("network cleanup is diagnostic-only")
        if self.last_observed is not None:
            _parse_timestamp(self.last_observed)
        if not all(isinstance(item, str) and item for item in self.evidence):
            raise ValueError("network evidence must contain non-empty strings")

    @property
    def owner_identity(self) -> tuple[str, str | None]:
        return self.owner_kind, self.owner_id

    @property
    def has_active_references(self) -> bool:
        # Unknown reference values (None) are active for release safety.
        return any(value is None or value > 0 for _key, value in self.active_references)

    @property
    def is_releasable(self) -> bool:
        return (
            self.owner_kind in {"workspace", "sandbox"}
            and self.owner_id is not None
            and self.lifecycle in {"idle", "orphaned"}
            and self.allocation_state == "allocated"
            and not self.has_active_references
        )

    def release_decision(self, *, enabled: bool = False) -> dict:
        """Return a bounded release decision without touching a network."""
        if self.has_active_references:
            return {
                "status": "refused",
                "reason": "active_references",
                "owner": {"kind": self.owner_kind, "id": self.owner_id},
            }
        if not self.is_releasable:
            return {
                "status": "refused",
                "reason": "network_lifecycle_not_releasable",
                "owner": {"kind": self.owner_kind, "id": self.owner_id},
            }
        if not enabled:
            return {
                "status": "refused",
                "reason": "network_cleanup_disabled",
                "owner": {"kind": self.owner_kind, "id": self.owner_id},
            }
        return {
            "status": "released",
            "reason": "released",
            "owner": {"kind": self.owner_kind, "id": self.owner_id},
        }

    def to_dict(self) -> dict:
        return redact({
            "network_id": self.network_id,
            "display_name": self.display_name or self.network_id,
            "owner": {"kind": self.owner_kind, "id": self.owner_id},
            "lifecycle": self.lifecycle,
            "active_references": _references_dict(self.active_references),
            "allocation": {
                "state": self.allocation_state,
                "pool": self.allocation_pool,
            },
            "capacity_accounted": False,
            "cleanup_eligible": False,
            "evidence": [{"kind": item, "quality": "bounded"} for item in self.evidence],
            "last_observed": self.last_observed,
        })

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NetworkLifecycle":
        if not isinstance(value, Mapping):
            raise ValueError("network lifecycle must be an object")
        owner = value.get("owner") or {}
        allocation = value.get("allocation") or {}
        evidence = value.get("evidence") or ()
        evidence_values = tuple(
            item.get("kind") if isinstance(item, Mapping) else item
            for item in evidence
        )
        return cls(
            network_id=value.get("network_id"),
            display_name=value.get("display_name"),
            owner_kind=owner.get("kind"),
            owner_id=owner.get("id"),
            lifecycle=value.get("lifecycle"),
            active_references=value.get("active_references") or (),
            allocation_state=allocation.get("state", "unknown"),
            allocation_pool=allocation.get("pool"),
            last_observed=value.get("last_observed"),
            evidence=evidence_values,
            cleanup_eligible=False,
        )

    @classmethod
    def from_observation(cls, item: "ResourceObservation") -> "NetworkLifecycle":
        if item.kind != "network":
            raise ValueError("network lifecycle requires a network observation")
        return cls(
            network_id=item.locator,
            display_name=item.display_name,
            owner_kind=item.owner_kind,
            owner_id=item.owner_id,
            lifecycle=item.lifecycle or "indeterminate",
            active_references=item.active_references,
            allocation_state=item.allocation_state or "unknown",
            allocation_pool=item.allocation_pool,
            last_observed=item.last_observed,
            evidence=item.evidence,
        )


class NetworkLifecycleRegistry:
    """Small in-memory lifecycle projection used by deterministic adapters/tests."""

    def __init__(self) -> None:
        self._records: dict[str, NetworkLifecycle] = {}

    @staticmethod
    def _network_id(owner_id: str) -> str:
        return "network-" + hashlib.sha256(owner_id.encode()).hexdigest()[:20]

    @staticmethod
    def _refs(value: Mapping[str, Any] | Iterable[tuple[str, Any]] | None) -> tuple[tuple[str, int | None], ...]:
        return normalize_active_references(value)

    def create(
        self,
        owner_id: str,
        *,
        owner_kind: str = "workspace",
        network_id: str | None = None,
        active_references: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
        allocation_pool: str | None = None,
    ) -> NetworkLifecycle:
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError("owner identity is required")
        existing = self._records.get(owner_id)
        if existing is not None:
            if network_id is not None and network_id != existing.network_id:
                raise ValueError("network_allocation_conflict")
            refs = existing.active_references if active_references is None else self._refs(active_references)
            updated = replace(
                existing,
                lifecycle="active",
                active_references=refs,
                allocation_state="allocated",
            )
            self._records[owner_id] = updated
            return updated
        chosen = network_id or self._network_id(owner_id)
        if any(item.network_id == chosen for item in self._records.values()):
            raise ValueError("network_allocation_conflict")
        refs = self._refs(active_references)
        record = NetworkLifecycle(
            network_id=chosen,
            owner_kind=owner_kind,
            owner_id=owner_id,
            lifecycle="active",
            active_references=refs,
            allocation_state="allocated",
            allocation_pool=allocation_pool,
            evidence=("lifecycle_owner", "allocation_owned"),
        )
        self._records[owner_id] = record
        return record

    def get(self, owner_id: str) -> NetworkLifecycle | None:
        return self._records.get(owner_id)

    def reconcile(
        self,
        owner_id: str,
        *,
        lifecycle: str | None = None,
        active_references: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
    ) -> NetworkLifecycle:
        current = self._records.get(owner_id)
        if current is None:
            return self.create(owner_id, active_references=active_references)
        updates = {}
        if lifecycle is not None:
            if lifecycle not in NETWORK_LIFECYCLES:
                raise ValueError("invalid network lifecycle")
            updates["lifecycle"] = lifecycle
        if active_references is not None:
            updates["active_references"] = self._refs(active_references)
        if updates:
            current = replace(current, **updates)
            self._records[owner_id] = current
        return current

    def stop(self, owner_id: str) -> NetworkLifecycle:
        return self.reconcile(owner_id, lifecycle="idle")

    def destroy(self, owner_id: str) -> NetworkLifecycle:
        return self.reconcile(owner_id, lifecycle="orphaned")

    def recreate(self, owner_id: str, **kwargs: Any) -> NetworkLifecycle:
        # Reuse the owner-bound allocation; never create a duplicate network.
        return self.create(owner_id, **kwargs)

    def release(self, owner_id: str, *, enabled: bool = False) -> dict:
        current = self._records.get(owner_id)
        if current is None:
            return {"status": "already_absent", "reason": "already_absent"}
        decision = current.release_decision(enabled=enabled)
        if decision["status"] == "released":
            self._records[owner_id] = replace(
                current, lifecycle="orphaned", allocation_state="available",
            )
        return decision

    def count(self) -> int:
        return len(self._records)

    def network_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.network_id for item in self._records.values()))

    def snapshots(self) -> tuple[NetworkLifecycle, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: item.network_id))


class ResourceCancellationSignal:
    """Thread-safe, first-terminal-state signal owned by one request."""

    __slots__ = ("_lock", "_state")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: str | None = None

    def _finish(self, state: str) -> bool:
        if type(state) is not str or state not in {"cancelled", "disconnected"}:
            raise ValueError("invalid resource cancellation state")
        with self._lock:
            if self._state is not None:
                return False
            self._state = state
            return True

    def cancel(self) -> bool:
        return self._finish("cancelled")

    def disconnect(self) -> bool:
        return self._finish("disconnected")

    def terminal_status(self) -> str | None:
        with self._lock:
            return self._state

    def is_set(self) -> bool:
        return self.terminal_status() is not None


def resource_cancellation_signal(value: object = False) -> ResourceCancellationSignal:
    """Translate the reviewed boolean seam once; reject probe-shaped objects."""
    if type(value) is ResourceCancellationSignal:
        return value
    if type(value) is not bool:
        raise ValueError("cancellation must be a resource cancellation signal")
    signal = ResourceCancellationSignal()
    if value:
        signal.cancel()
    return signal


@dataclass(frozen=True)
class ResourceRequest:
    """Per-call measurement controls; cancellation is never persisted."""

    budget_seconds: float
    cancellation: ResourceCancellationSignal | bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "cancellation", resource_cancellation_signal(self.cancellation),
        )

    def terminal_status(self) -> str | None:
        return self.cancellation.terminal_status()

    def is_cancelled(self) -> bool:
        return self.terminal_status() == "cancelled"

    def is_terminal(self) -> bool:
        return self.terminal_status() is not None


@dataclass(frozen=True)
class StorageTarget:
    kind: str
    name: str
    identity: str

    def __post_init__(self) -> None:
        if self.kind not in TARGET_KINDS:
            raise ValueError("target kind must be local or remote")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("target name is required")
        if not isinstance(self.identity, str) or not self.identity:
            raise ValueError("target identity is required")
        if self.kind == "local" and self.name != "local":
            raise ValueError("local target name must be local")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "name": redact(self.name), "identity": redact(self.identity)}

    @classmethod
    def from_dict(cls, value: dict) -> "StorageTarget":
        if not isinstance(value, dict):
            raise ValueError("target must be an object")
        return cls(value.get("kind"), value.get("name"), value.get("identity"))


@dataclass(frozen=True)
class ResourceObservation:
    resource_id: str
    kind: str
    locator: str
    display_name: str
    owner_kind: str
    owner_id: str | None
    classification: str
    size_state: str
    size_bytes: int | None
    reclaimable_bytes: int
    capacity_accounted: bool = True
    age_seconds: int | None = None
    references: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    # Network lifecycle evidence is additive and intentionally unavailable for
    # non-network resources.  Missing reference counts stay unknown (None),
    # rather than being coerced to zero.
    lifecycle: str | None = None
    active_references: tuple[tuple[str, int | None], ...] = ()
    allocation_state: str | None = None
    allocation_pool: str | None = None
    cleanup_eligible: bool = False
    last_observed: str | None = None

    def __post_init__(self) -> None:
        for name in ("resource_id", "kind", "locator", "display_name", "owner_kind"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} is required")
        if self.owner_id is not None and (not isinstance(self.owner_id, str) or not self.owner_id):
            raise ValueError("owner_id must be a non-empty string or null")
        if self.classification not in CLASSIFICATIONS:
            raise ValueError("invalid resource classification")
        if self.size_state not in SIZE_STATES:
            raise ValueError("invalid size state")
        _non_negative(self.size_bytes, "size_bytes", optional=True)
        _non_negative(self.reclaimable_bytes, "reclaimable_bytes")
        _non_negative(self.age_seconds, "age_seconds", optional=True)
        if not isinstance(self.capacity_accounted, bool):
            raise ValueError("capacity_accounted must be a boolean")
        if self.size_state == "measured" and self.size_bytes is None:
            raise ValueError("measured resources require size_bytes")
        if self.size_state != "measured" and self.size_bytes is not None:
            raise ValueError("unmeasured resources cannot have size_bytes")
        if self.reclaimable_bytes and self.classification not in {
            "disposable_cache", "stale_candidate",
        }:
            raise ValueError("protected resources cannot claim reclaimable bytes")
        if self.reclaimable_bytes and self.size_state != "measured":
            raise ValueError("unmeasured resources cannot claim reclaimable bytes")
        if self.size_bytes is not None and self.reclaimable_bytes > self.size_bytes:
            raise ValueError("reclaimable bytes cannot exceed measured bytes")
        if not all(isinstance(item, str) for values in (
            self.references, self.evidence, self.errors,
        ) for item in values):
            raise ValueError("resource evidence fields must contain strings")
        refs = normalize_active_references(self.active_references)
        object.__setattr__(self, "active_references", refs)
        if self.kind == "network":
            if self.lifecycle is None:
                lifecycle = "indeterminate"
                object.__setattr__(self, "lifecycle", lifecycle)
            elif self.lifecycle not in NETWORK_LIFECYCLES:
                raise ValueError("invalid network lifecycle")
            if self.allocation_state is None:
                object.__setattr__(self, "allocation_state", "unknown")
            elif self.allocation_state not in NETWORK_ALLOCATION_STATES:
                raise ValueError("invalid network allocation state")
            if self.allocation_pool is not None and (
                not isinstance(self.allocation_pool, str) or not self.allocation_pool
            ):
                raise ValueError("allocation_pool must be a non-empty string or null")
            if self.cleanup_eligible:
                raise ValueError("network cleanup is diagnostic-only")
            if self.last_observed is not None:
                _parse_timestamp(self.last_observed)
        elif any(value is not None for value in (
            self.lifecycle, self.allocation_state, self.allocation_pool,
            self.last_observed,
        )) or self.active_references or self.cleanup_eligible:
            raise ValueError("lifecycle evidence is only valid for network resources")
        if not isinstance(self.cleanup_eligible, bool):
            raise ValueError("cleanup_eligible must be a boolean")

    def to_dict(self) -> dict:
        value = {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "display_name": self.display_name,
            "owner": {"kind": self.owner_kind, "id": self.owner_id},
            "classification": self.classification,
            "size_state": self.size_state,
            "size_bytes": self.size_bytes,
            "reclaimable_bytes": self.reclaimable_bytes,
            "capacity_accounted": self.capacity_accounted,
            "age_seconds": self.age_seconds,
            "references": list(self.references),
            "evidence": list(self.evidence),
            "errors": list(self.errors),
        }
        if self.kind == "network":
            value.update({
                "lifecycle": self.lifecycle,
                "active_references": _references_dict(self.active_references),
                "allocation": {
                    "state": self.allocation_state,
                    "pool": self.allocation_pool,
                },
                "cleanup_eligible": False,
                "last_observed": self.last_observed,
            })
        return redact(value)

    def internal_dict(self) -> dict:
        return {**self.to_dict(), "locator": self.locator}


@dataclass(frozen=True)
class CleanupCandidate:
    resource_id: str
    kind: str
    locator: str
    locator_digest: str
    expected_owner_kind: str
    expected_owner_id: str | None
    expected_absence: tuple[str, ...]
    expected_size_bytes: int | None
    expected_reclaimable_bytes: int
    evidence_digest: str
    expected_lifecycle: str | None = None
    expected_active_references: tuple[tuple[str, int | None], ...] = ()
    expected_allocation_state: str | None = None
    expected_allocation_pool: str | None = None

    @classmethod
    def from_observation(cls, item: ResourceObservation) -> "CleanupCandidate":
        if item.classification not in {"disposable_cache", "stale_candidate"}:
            raise ValueError("resource is not cleanup eligible")
        canonical = "\n".join((
            item.resource_id, item.kind, item.locator, item.owner_kind,
            item.owner_id or "", *sorted(item.evidence), *sorted(item.references),
            item.lifecycle or "", repr(item.active_references),
            item.allocation_state or "", item.allocation_pool or "",
        ))
        return cls(
            resource_id=item.resource_id,
            kind=item.kind,
            locator=item.locator,
            locator_digest=hashlib.sha256(item.locator.encode()).hexdigest(),
            expected_owner_kind=item.owner_kind,
            expected_owner_id=item.owner_id,
            expected_absence=tuple(sorted(item.references)),
            expected_size_bytes=item.size_bytes,
            expected_reclaimable_bytes=item.reclaimable_bytes,
            evidence_digest=hashlib.sha256(canonical.encode()).hexdigest(),
            expected_lifecycle=item.lifecycle,
            expected_active_references=item.active_references,
            expected_allocation_state=item.allocation_state,
            expected_allocation_pool=item.allocation_pool,
        )

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.resource_id, self.kind, self.locator,
            self.locator_digest, self.expected_owner_kind, self.evidence_digest,
        )):
            raise ValueError("candidate identity is incomplete")
        _non_negative(self.expected_size_bytes, "expected_size_bytes", optional=True)
        _non_negative(
            self.expected_reclaimable_bytes,
            "expected_reclaimable_bytes",
        )
        if (
            self.expected_size_bytes is not None
            and self.expected_reclaimable_bytes > self.expected_size_bytes
        ):
            raise ValueError(
                "expected reclaimable bytes cannot exceed expected size",
            )
        refs = normalize_active_references(self.expected_active_references)
        object.__setattr__(self, "expected_active_references", refs)
        if self.expected_lifecycle is not None and self.expected_lifecycle not in NETWORK_LIFECYCLES:
            raise ValueError("invalid expected lifecycle")
        if self.expected_allocation_state is not None and self.expected_allocation_state not in NETWORK_ALLOCATION_STATES:
            raise ValueError("invalid expected allocation state")
        if self.expected_allocation_pool is not None and (
            not isinstance(self.expected_allocation_pool, str)
            or not self.expected_allocation_pool
        ):
            raise ValueError("expected allocation pool must be a non-empty string or null")

    def to_dict(self, *, public: bool = False) -> dict:
        value = {
            "resource_id": self.resource_id,
            "kind": self.kind,
            "locator_digest": self.locator_digest,
            "expected_owner": {
                "kind": self.expected_owner_kind,
                "id": self.expected_owner_id,
            },
            "expected_absence": list(self.expected_absence),
            "expected_size_bytes": self.expected_size_bytes,
            "expected_reclaimable_bytes": self.expected_reclaimable_bytes,
            "evidence_digest": self.evidence_digest,
        }
        if self.kind == "network":
            value.update({
                "expected_lifecycle": self.expected_lifecycle,
                "expected_active_references": _references_dict(
                    self.expected_active_references,
                ),
                "expected_allocation": {
                    "state": self.expected_allocation_state,
                    "pool": self.expected_allocation_pool,
                },
            })
        if not public:
            value["locator"] = self.locator
        return redact(value) if public else value

    @classmethod
    def from_dict(cls, value: dict) -> "CleanupCandidate":
        owner = value.get("expected_owner") or {}
        return cls(
            resource_id=value.get("resource_id"),
            kind=value.get("kind"),
            locator=value.get("locator"),
            locator_digest=value.get("locator_digest"),
            expected_owner_kind=owner.get("kind"),
            expected_owner_id=owner.get("id"),
            expected_absence=tuple(value.get("expected_absence") or ()),
            expected_size_bytes=value.get("expected_size_bytes"),
            expected_reclaimable_bytes=value.get(
                "expected_reclaimable_bytes",
                value.get("expected_size_bytes") or 0,
            ),
            evidence_digest=value.get("evidence_digest"),
            expected_lifecycle=value.get("expected_lifecycle"),
            expected_active_references=value.get("expected_active_references") or (),
            expected_allocation_state=(value.get("expected_allocation") or {}).get("state"),
            expected_allocation_pool=(value.get("expected_allocation") or {}).get("pool"),
        )


@dataclass(frozen=True)
class CleanupPlan:
    schema_version: int
    plan_id: str
    target: StorageTarget
    scope: str
    created_at: datetime
    expires_at: datetime
    scan_id: str
    candidates: tuple[CleanupCandidate, ...]
    exclusions: tuple[dict, ...]
    estimated_reclaimable_bytes: int
    state: str = "planned"
    confirmation_required: bool = True
    # Feature-owned, opaque to the plan contract: tiered reclamation stores the
    # reviewed candidate detail (class, tier, reason, mtime) it must replay at
    # execution time without re-deciding policy against a changed host.
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported plan schema")
        if not isinstance(self.plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", self.plan_id):
            raise ValueError("invalid plan id")
        if self.scope not in PLAN_SCOPES:
            raise ValueError("invalid cleanup scope")
        if self.state not in PLAN_STATES:
            raise ValueError("invalid plan state")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("plan timestamps require a timezone")
        if self.expires_at <= self.created_at:
            raise ValueError("plan expiry must follow creation")
        if not isinstance(self.scan_id, str) or not self.scan_id:
            raise ValueError("scan id is required")
        if not self.confirmation_required:
            raise ValueError("resource plans always require confirmation")
        _non_negative(self.estimated_reclaimable_bytes, "estimated_reclaimable_bytes")
        if self.scope == "cache" and any(item.kind in {"volume", "worktree"} for item in self.candidates):
            raise ValueError("cache plans cannot contain persistent resources")

    @classmethod
    def create(
        cls,
        target: StorageTarget,
        scope: str,
        candidates: Iterable[CleanupCandidate],
        exclusions: Iterable[dict],
        *,
        scan_id: str | None = None,
        now: datetime | None = None,
        ttl: timedelta = timedelta(minutes=15),
        metadata: dict | None = None,
    ) -> "CleanupPlan":
        reference = (now or utc_now()).astimezone(timezone.utc)
        chosen = tuple(candidates)
        return cls(
            schema_version=1,
            plan_id=secrets.token_hex(16),
            target=target,
            scope=scope,
            created_at=reference,
            expires_at=reference + ttl,
            scan_id=scan_id or secrets.token_hex(16),
            candidates=chosen,
            exclusions=tuple(exclusions),
            estimated_reclaimable_bytes=sum(
                item.expected_reclaimable_bytes for item in chosen
            ),
            metadata=dict(metadata or {}),
        )

    def to_dict(self, *, public: bool = False) -> dict:
        value = {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "target": self.target.to_dict(),
            "scope": self.scope,
            "created_at": _timestamp(self.created_at),
            "expires_at": _timestamp(self.expires_at),
            "scan_id": self.scan_id,
            "candidates": [item.to_dict(public=public) for item in self.candidates],
            "exclusions": list(self.exclusions),
            "estimated_reclaimable_bytes": self.estimated_reclaimable_bytes,
            "state": self.state,
            "confirmation_required": self.confirmation_required,
        }
        if self.metadata:
            # Candidate locators are filesystem paths; keep them owner-only.
            value["metadata"] = (
                {"candidate_count": len(self.metadata.get("candidates") or ())}
                if public else self.metadata
            )
        return redact(value) if public else value

    @classmethod
    def from_dict(cls, value: dict) -> "CleanupPlan":
        if not isinstance(value, dict):
            raise ValueError("plan must be an object")
        return cls(
            schema_version=value.get("schema_version"),
            plan_id=value.get("plan_id"),
            target=StorageTarget.from_dict(value.get("target")),
            scope=value.get("scope"),
            created_at=_parse_timestamp(value.get("created_at")),
            expires_at=_parse_timestamp(value.get("expires_at")),
            scan_id=value.get("scan_id"),
            candidates=tuple(
                CleanupCandidate.from_dict(item)
                for item in value.get("candidates") or ()
            ),
            exclusions=tuple(value.get("exclusions") or ()),
            estimated_reclaimable_bytes=value.get("estimated_reclaimable_bytes"),
            state=value.get("state", "planned"),
            confirmation_required=value.get("confirmation_required", True),
            metadata=value.get("metadata") or {},
        )


@dataclass(frozen=True)
class StorageScan:
    scan_id: str
    target: StorageTarget
    mode: str
    started_at: datetime
    completed_at: datetime
    budget_seconds: float
    status: str
    capacity: dict | None
    resources: tuple[ResourceObservation, ...]
    attributed_bytes: int
    unknown_bytes: int
    reclaimable_bytes: int
    confidence: str
    category_outcomes: tuple[dict, ...] = ()
    drift: dict | None = None
    deep_attribution: Any | None = None
    capacity_scope_id: str | None = None
    capacity_pressure: dict | None = None
    reclaim: dict | None = None

    def __post_init__(self) -> None:
        if self.capacity is not None:
            required = (
                "total_bytes", "used_bytes", "available_bytes", "reserved_bytes",
            )
            for name in required:
                _non_negative(self.capacity.get(name), f"capacity.{name}")
            if self.capacity["used_bytes"] > self.capacity["total_bytes"]:
                raise ValueError("capacity used bytes cannot exceed total bytes")
            reconciled = (
                self.capacity["used_bytes"]
                + self.capacity["available_bytes"]
                + self.capacity["reserved_bytes"]
            )
            if reconciled != self.capacity["total_bytes"]:
                raise ValueError("capacity values must reconcile exactly")
        for name in (
            "attributed_bytes", "unknown_bytes", "reclaimable_bytes",
        ):
            _non_negative(getattr(self, name), name)
        if self.capacity_scope_id is not None and (
            not isinstance(self.capacity_scope_id, str)
            or not self.capacity_scope_id
        ):
            raise ValueError("capacity_scope_id must be a non-empty string or null")

    def to_dict(self) -> dict:
        def aggregate(group):
            buckets = {}
            for item in self.resources:
                key = group(item)
                bucket = buckets.setdefault(key, {
                    "id": key,
                    "resource_count": 0,
                    "measured_bytes": 0,
                    "reclaimable_bytes": 0,
                    "unknown_size_count": 0,
                })
                bucket["resource_count"] += 1
                if item.size_state == "measured":
                    bucket["measured_bytes"] += item.size_bytes or 0
                else:
                    bucket["unknown_size_count"] += 1
                bucket["reclaimable_bytes"] += item.reclaimable_bytes
            return sorted(
                buckets.values(),
                key=lambda item: (
                    item["measured_bytes"],
                    item["reclaimable_bytes"],
                    item["id"],
                ),
                reverse=True,
            )

        owners = aggregate(
            lambda item: f"{item.owner_kind}:{item.owner_id or 'unknown'}",
        )
        categories = aggregate(lambda item: item.kind)
        value = {
            "scan_id": self.scan_id,
            "target": self.target.to_dict(),
            "mode": self.mode,
            "started_at": _timestamp(self.started_at),
            "completed_at": _timestamp(self.completed_at),
            "budget_seconds": self.budget_seconds,
            "completeness": self.status,
            "capacity": self.capacity,
            "capacity_scope_id": self.capacity_scope_id,
            "capacity_pressure": self.capacity_pressure,
            "summary": {
                "attributed_bytes": self.attributed_bytes,
                "unknown_bytes": self.unknown_bytes,
                "reclaimable_bytes": self.reclaimable_bytes,
                "owners": owners,
                "categories": categories,
            },
            "confidence": self.confidence,
            "resources": [item.to_dict() for item in self.resources],
            "category_outcomes": list(self.category_outcomes),
            "drift": self.drift,
        }
        if self.deep_attribution is not None:
            value["deep_attribution"] = self.deep_attribution.to_dict()
        if self.reclaim is not None:
            value["reclaim"] = self.reclaim
        return redact(value)


@dataclass(frozen=True)
class CleanupItemOutcome:
    resource_id: str
    status: str
    reason: str
    observed_bytes: int | None
    evidence_changed: bool
    revalidated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in ITEM_STATES:
            raise ValueError("invalid cleanup item status")
        _non_negative(self.observed_bytes, "observed_bytes", optional=True)

    def to_dict(self) -> dict:
        return redact({
            "resource_id": self.resource_id,
            "status": self.status,
            "reason": self.reason,
            "observed_bytes": self.observed_bytes,
            "evidence_changed": self.evidence_changed,
            "revalidated_at": _timestamp(self.revalidated_at),
        })


@dataclass(frozen=True)
class CleanupRun:
    run_id: str
    plan_id: str
    target: StorageTarget
    status: str
    started_at: datetime
    completed_at: datetime
    planned_bytes: int
    observed_reclaimed_bytes: int | None
    outcomes: tuple[CleanupItemOutcome, ...]
    capacity_before: dict | None = None
    capacity_after: dict | None = None
    drift: dict | None = None
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in RUN_STATES:
            raise ValueError("invalid cleanup run status")
        _non_negative(self.planned_bytes, "planned_bytes")
        _non_negative(
            self.observed_reclaimed_bytes,
            "observed_reclaimed_bytes",
            optional=True,
        )

    def to_dict(self) -> dict:
        return redact({
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "target": self.target.to_dict(),
            "status": self.status,
            "started_at": _timestamp(self.started_at),
            "completed_at": _timestamp(self.completed_at),
            "planned_bytes": self.planned_bytes,
            "observed_reclaimed_bytes": self.observed_reclaimed_bytes,
            "capacity_before": self.capacity_before,
            "capacity_after": self.capacity_after,
            "drift": self.drift,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "errors": list(self.errors),
        })
