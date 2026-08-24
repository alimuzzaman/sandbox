"""Thread-safe, host-neutral request-wake coordination."""

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
    wake_on_request: bool = False
    idle_after_seconds: int = 900
    wake_timeout_seconds: int = 60
    stop_grace_seconds: int = 30
    max_pending_requests: int = 32

    def __post_init__(self) -> None:
        if self.mode not in {"always_on", "idle_stop"} or not isinstance(self.wake_on_request, bool):
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


@dataclass(frozen=True)
class ActivationClaim:
    accepted: bool
    owner: bool
    generation: int


@dataclass(frozen=True)
class DrainClaim:
    route_id: str
    generation: int


@dataclass
class _Route:
    policy: ActivationPolicy
    state: ActivationState = ActivationState.ASLEEP
    last_activity: float = field(default_factory=time.monotonic)
    pending: int = 0
    leases: dict[str, ActivityLease] = field(default_factory=dict)
    error: str | None = None
    generation: int = 0


_ROUTE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LEASE_KIND = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class ActivationCoordinator:
    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._routes: dict[str, _Route] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _route(route_id: object) -> str:
        if not isinstance(route_id, str) or not _ROUTE_ID.fullmatch(route_id):
            raise ValueError("activation route id is invalid")
        return route_id

    def register(self, route_id: str, policy: ActivationPolicy,
                 *, state: ActivationState = ActivationState.ASLEEP) -> None:
        route_id = self._route(route_id)
        if not isinstance(policy, ActivationPolicy) or not isinstance(state, ActivationState):
            raise ValueError("activation route registration is invalid")
        with self._lock:
            existing = self._routes.get(route_id)
            if existing is None:
                self._routes[route_id] = _Route(policy, state=state, last_activity=self._clock())
            else:
                existing.policy = policy

    def touch(self, route_id: str, *, now: float | None = None) -> None:
        with self._lock:
            route = self._routes[self._route(route_id)]
            route.last_activity = self._clock() if now is None else float(now)
            if route.state == ActivationState.DRAINING:
                route.generation += 1
                route.state = ActivationState.READY

    def claim_activation(self, route_id: str, *, now: float | None = None) -> ActivationClaim:
        with self._lock:
            route = self._routes[self._route(route_id)]
            if route.pending >= route.policy.max_pending_requests:
                return ActivationClaim(False, False, route.generation)
            route.pending += 1
            route.last_activity = self._clock() if now is None else float(now)
            if route.state == ActivationState.DRAINING:
                route.generation += 1
                route.state = ActivationState.READY
            owner = route.state in {ActivationState.ASLEEP, ActivationState.ERROR}
            if owner:
                route.generation += 1
                route.state = ActivationState.WAKING
                route.error = None
            return ActivationClaim(True, owner, route.generation)

    def begin_request(self, route_id: str, *, now: float | None = None) -> bool:
        return self.claim_activation(route_id, now=now).accepted

    def begin_activation(self, route_id: str, *, now: float | None = None) -> tuple[bool, bool]:
        claim = self.claim_activation(route_id, now=now)
        return claim.accepted, claim.owner

    def end_request(self, route_id: str, *, now: float | None = None) -> None:
        with self._lock:
            route = self._routes[self._route(route_id)]
            route.pending = max(0, route.pending - 1)
            route.last_activity = self._clock() if now is None else float(now)

    def mark_ready(self, route_id: str, *, generation: int | None = None,
                   now: float | None = None) -> bool:
        with self._lock:
            route = self._routes[self._route(route_id)]
            if generation is not None and generation != route.generation:
                return False
            route.state, route.error = ActivationState.READY, None
            route.last_activity = self._clock() if now is None else float(now)
            return True

    def mark_asleep(self, route_id: str, *, generation: int | None = None,
                    now: float | None = None) -> bool:
        with self._lock:
            route = self._routes[self._route(route_id)]
            if generation is not None and (generation != route.generation or
                                           route.state != ActivationState.DRAINING):
                return False
            route.state, route.error = ActivationState.ASLEEP, None
            route.last_activity = self._clock() if now is None else float(now)
            return True

    def mark_error(self, route_id: str, error: str | None = None,
                   *, generation: int | None = None, now: float | None = None) -> bool:
        with self._lock:
            route = self._routes[self._route(route_id)]
            if generation is not None and generation != route.generation:
                return False
            route.state = ActivationState.ERROR
            route.error = error if isinstance(error, str) and error else "activation_failed"
            route.last_activity = self._clock() if now is None else float(now)
            return True

    def acquire_lease(self, route_id: str, kind: str, *, ttl_seconds: int | None = None,
                      now: float | None = None) -> ActivityLease:
        if not isinstance(kind, str) or not _LEASE_KIND.fullmatch(kind):
            raise ValueError("activity lease kind is invalid")
        if ttl_seconds is not None and (isinstance(ttl_seconds, bool) or
                not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 604800):
            raise ValueError("activity lease ttl is invalid")
        current = self._clock() if now is None else float(now)
        lease = ActivityLease(uuid.uuid4().hex, self._route(route_id), kind,
                              None if ttl_seconds is None else current + ttl_seconds)
        with self._lock:
            route = self._routes[lease.route_id]
            route.leases[lease.lease_id] = lease
            route.last_activity, route.state = current, ActivationState.PINNED
        return lease

    def release_lease(self, lease_id: str, *, now: float | None = None) -> bool:
        if not isinstance(lease_id, str) or not lease_id:
            return False
        current = self._clock() if now is None else float(now)
        with self._lock:
            for route in self._routes.values():
                if route.leases.pop(lease_id, None) is not None:
                    route.last_activity = current
                    if route.state == ActivationState.PINNED and not route.leases:
                        route.state = ActivationState.READY
                    return True
        return False

    def claim_due_for_suspend(self, *, now: float | None = None) -> tuple[DrainClaim, ...]:
        current = self._clock() if now is None else float(now)
        claims: list[DrainClaim] = []
        with self._lock:
            for route_id, route in self._routes.items():
                for key, lease in tuple(route.leases.items()):
                    if lease.expires_at is not None and lease.expires_at <= current:
                        route.leases.pop(key, None)
                        route.last_activity = max(route.last_activity, lease.expires_at)
                if route.state == ActivationState.PINNED and not route.leases:
                    route.state = ActivationState.READY
                if (route.policy.mode == "idle_stop" and route.state == ActivationState.READY
                        and route.pending == 0 and not route.leases
                        and current - route.last_activity >= route.policy.idle_after_seconds):
                    route.generation += 1
                    route.state = ActivationState.DRAINING
                    claims.append(DrainClaim(route_id, route.generation))
        return tuple(claims)

    def due_for_suspend(self, *, now: float | None = None) -> tuple[str, ...]:
        return tuple(claim.route_id for claim in self.claim_due_for_suspend(now=now))

    def run_if_drain_current(self, claim: DrainClaim,
                             operation: Callable[[], bool]) -> tuple[bool, bool]:
        """Run a stop effect only while its exact drain generation is current.

        The coordinator lock intentionally spans the bounded stop callback. A
        new request therefore cannot invalidate a claim between its final
        check and the stop effect; it waits, then observes ASLEEP and wakes it.
        """
        if not isinstance(claim, DrainClaim):
            raise ValueError("drain claim is invalid")
        with self._lock:
            route = self._routes[self._route(claim.route_id)]
            if route.state != ActivationState.DRAINING or route.generation != claim.generation:
                return False, False
            try:
                return True, bool(operation())
            except Exception:
                return True, False

    def snapshot(self, route_id: str) -> Mapping[str, object]:
        with self._lock:
            route_id = self._route(route_id)
            route = self._routes[route_id]
            return {"route_id": route_id, "state": route.state.value,
                    "pending": route.pending, "leases": len(route.leases),
                    "last_activity": route.last_activity, "error": route.error,
                    "generation": route.generation,
                    "policy": {"mode": route.policy.mode,
                               "wake_on_request": route.policy.wake_on_request,
                               "idle_after_seconds": route.policy.idle_after_seconds,
                               "wake_timeout_seconds": route.policy.wake_timeout_seconds,
                               "stop_grace_seconds": route.policy.stop_grace_seconds,
                               "max_pending_requests": route.policy.max_pending_requests}}


__all__ = ["ActivationClaim", "ActivationCoordinator", "ActivationPolicy",
           "ActivationState", "ActivityLease", "DrainClaim"]
