"""Pure, host-neutral activation state and idle lease coordination.

The coordinator does not call Docker or inspect HTTP.  A gateway/host service
owns those effects and uses this module to guarantee one wake per instance,
track active request/job leases, and choose only instances that have remained
idle for the complete configured interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import threading
import time
from typing import Callable, Mapping
import uuid


class ActivationState(str, Enum):
    ASLEEP = "asleep"
    WAKING = "waking"
    READY = "ready"
    DRAINING = "draining"
    ERROR = "error"
    PINNED = "pinned"


@dataclass(frozen=True)
class ActivationPolicy:
    mode: str = "always_on"
    idle_after_seconds: int = 900
    wake_timeout_seconds: int = 60
    stop_grace_seconds: int = 30
    max_pending_requests: int = 32

    def __post_init__(self) -> None:
        if self.mode not in {"always_on", "idle_stop"}:
            raise ValueError("activation policy mode is invalid")
        for name, value, low, high in (
            ("idle_after_seconds", self.idle_after_seconds, 60, 604800),
            ("wake_timeout_seconds", self.wake_timeout_seconds, 5, 600),
            ("stop_grace_seconds", self.stop_grace_seconds, 1, 120),
            ("max_pending_requests", self.max_pending_requests, 1, 256),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"activation policy {name} is invalid")


@dataclass(frozen=True)
class ActivityLease:
    lease_id: str
    route_id: str
    kind: str
    expires_at: float | None


@dataclass
class _Route:
    policy: ActivationPolicy
    state: ActivationState = ActivationState.ASLEEP
    last_activity: float = field(default_factory=time.monotonic)
    pending: int = 0
    leases: dict[str, ActivityLease] = field(default_factory=dict)
    error: str | None = None


_ROUTE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LEASE_KIND = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class ActivationCoordinator:
    """Thread-safe in-memory lifecycle coordinator.

    State is advisory and reconstructable from the runtime status.  Callers
    must persist no credentials or request bodies here; the route id is an
    opaque registry-owned identity.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._routes: dict[str, _Route] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _route(route_id: object) -> str:
        if not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id):
            raise ValueError("activation route id is invalid")
        return route_id

    @staticmethod
    def _kind(kind: object) -> str:
        if not isinstance(kind, str) or not _LEASE_KIND.fullmatch(kind):
            raise ValueError("activity lease kind is invalid")
        return kind

    def register(self, route_id: str, policy: ActivationPolicy, *, state: ActivationState = ActivationState.ASLEEP) -> None:
        route_id = self._route(route_id)
        if not isinstance(policy, ActivationPolicy) or not isinstance(state, ActivationState):
            raise ValueError("activation route registration is invalid")
        now = self._clock()
        with self._lock:
            existing = self._routes.get(route_id)
            if existing is None:
                self._routes[route_id] = _Route(policy, state=state, last_activity=now)
            else:
                existing.policy = policy
                existing.state = state
                existing.last_activity = now

    def touch(self, route_id: str, *, now: float | None = None) -> None:
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            route.last_activity = self._clock() if now is None else float(now)

    def begin_request(self, route_id: str, *, now: float | None = None) -> bool:
        """Reserve one pending request, returning false at the configured bound."""
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            if route.pending >= route.policy.max_pending_requests:
                return False
            route.pending += 1
            route.last_activity = self._clock() if now is None else float(now)
            if route.state == ActivationState.ASLEEP:
                route.state = ActivationState.WAKING
            return True

    def begin_activation(self, route_id: str, *, now: float | None = None) -> tuple[bool, bool]:
        """Reserve a request and report whether this caller owns the wake.

        The first caller that observes an asleep route becomes the sole
        activator. Later callers are admitted as waiters and must not issue a
        second Docker start.
        """
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            if route.pending >= route.policy.max_pending_requests:
                return False, False
            owner = route.state in {ActivationState.ASLEEP, ActivationState.ERROR}
            route.pending += 1
            route.last_activity = self._clock() if now is None else float(now)
            if owner:
                route.state = ActivationState.WAKING
                route.error = None
            return True, owner

    def end_request(self, route_id: str, *, now: float | None = None) -> None:
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            route.pending = max(0, route.pending - 1)
            route.last_activity = self._clock() if now is None else float(now)

    def mark_ready(self, route_id: str, *, now: float | None = None) -> None:
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            route.state = ActivationState.READY
            route.error = None
            route.last_activity = self._clock() if now is None else float(now)

    def mark_asleep(self, route_id: str, *, now: float | None = None) -> None:
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            route.state = ActivationState.ASLEEP
            route.pending = 0
            route.error = None
            route.last_activity = self._clock() if now is None else float(now)

    def mark_error(self, route_id: str, error: str | None = None, *, now: float | None = None) -> None:
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            route.state = ActivationState.ERROR
            route.error = error if isinstance(error, str) and error else "activation_failed"
            route.last_activity = self._clock() if now is None else float(now)

    def acquire_lease(self, route_id: str, kind: str, *, ttl_seconds: int | None = None,
                      now: float | None = None) -> ActivityLease:
        route_id = self._route(route_id)
        kind = self._kind(kind)
        if ttl_seconds is not None and (
                isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int)
                or not 1 <= ttl_seconds <= 604800):
            raise ValueError("activity lease ttl is invalid")
        current = self._clock() if now is None else float(now)
        lease = ActivityLease(
            uuid.uuid4().hex, route_id, kind,
            None if ttl_seconds is None else current + ttl_seconds,
        )
        with self._lock:
            route = self._routes[route_id]
            route.leases[lease.lease_id] = lease
            route.last_activity = current
            route.state = ActivationState.PINNED
        return lease

    def release_lease(self, lease_id: str, *, now: float | None = None) -> bool:
        if not isinstance(lease_id, str) or not lease_id:
            return False
        current = self._clock() if now is None else float(now)
        with self._lock:
            for route in self._routes.values():
                lease = route.leases.pop(lease_id, None)
                if lease is not None:
                    route.last_activity = current
                    if route.state == ActivationState.PINNED and not route.leases:
                        route.state = ActivationState.READY
                    return True
        return False

    def due_for_suspend(self, *, now: float | None = None) -> tuple[str, ...]:
        current = self._clock() if now is None else float(now)
        due: list[str] = []
        with self._lock:
            for route_id, route in self._routes.items():
                expired = [
                    key for key, lease in route.leases.items()
                    if lease.expires_at is not None and lease.expires_at <= current
                ]
                for key in expired:
                    lease = route.leases.pop(key, None)
                    if lease is not None and lease.expires_at is not None:
                        route.last_activity = max(route.last_activity, lease.expires_at)
                if route.state == ActivationState.PINNED and not route.leases:
                    route.state = ActivationState.READY
                if (route.policy.mode == "idle_stop" and route.state == ActivationState.READY
                        and route.pending == 0 and not route.leases
                        and current - route.last_activity >= route.policy.idle_after_seconds):
                    route.state = ActivationState.DRAINING
                    due.append(route_id)
        return tuple(due)

    def snapshot(self, route_id: str) -> Mapping[str, object]:
        route_id = self._route(route_id)
        with self._lock:
            route = self._routes[route_id]
            return {
                "route_id": route_id,
                "state": route.state.value,
                "pending": route.pending,
                "leases": len(route.leases),
                "last_activity": route.last_activity,
                "error": route.error,
                "policy": {
                    "mode": route.policy.mode,
                    "idle_after_seconds": route.policy.idle_after_seconds,
                    "wake_timeout_seconds": route.policy.wake_timeout_seconds,
                    "stop_grace_seconds": route.policy.stop_grace_seconds,
                    "max_pending_requests": route.policy.max_pending_requests,
                },
            }
