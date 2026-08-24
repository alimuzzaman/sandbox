"""Single-flight activation service used by the future HTTP gateway.

This module owns coordination only.  Docker lifecycle and readiness probes are
injected callbacks so the service can run behind local Caddy or the authenticated
remote controller without giving a browser access to Docker or SSH.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from collections.abc import Callable

from .coordinator import ActivationCoordinator, ActivationPolicy, ActivationState


@dataclass(frozen=True)
class ActivationResult:
    ok: bool
    state: str
    cold_start: bool
    error: str | None = None


class ActivationService:
    def __init__(self, coordinator: ActivationCoordinator | None = None) -> None:
        self.coordinator = coordinator or ActivationCoordinator()
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()

    def register(self, route_id: str, policy: ActivationPolicy,
                 *, state: ActivationState = ActivationState.ASLEEP) -> None:
        self.coordinator.register(route_id, policy, state=state)
        with self._lock:
            self._events.setdefault(route_id, threading.Event())

    def activate(self, route_id: str, *, resume: Callable[[str, int], bool]) -> ActivationResult:
        snapshot = self.coordinator.snapshot(route_id)
        if snapshot["state"] in {ActivationState.READY.value, ActivationState.PINNED.value}:
            self.coordinator.touch(route_id)
            return ActivationResult(True, snapshot["state"], False)
        policy = snapshot["policy"]
        accepted, owner = self.coordinator.begin_activation(route_id)
        if not accepted:
            return ActivationResult(False, ActivationState.ERROR.value, False, "pending_request_limit")
        event = self._events[route_id]
        if owner:
            event.clear()
            try:
                ok = bool(resume(route_id, int(policy["wake_timeout_seconds"])))
            except Exception:
                ok = False
            if ok:
                self.coordinator.mark_ready(route_id)
                result = ActivationResult(True, ActivationState.READY.value, True)
            else:
                self.coordinator.mark_error(route_id)
                result = ActivationResult(False, ActivationState.ERROR.value, True, "resume_failed")
            event.set()
            return result

        # A waiter never starts Docker.  It observes one bounded completion
        # signal, then reads reconstructable coordinator truth.
        waited = event.wait(timeout=int(policy["wake_timeout_seconds"]))
        if not waited:
            return ActivationResult(False, ActivationState.ERROR.value, False, "wake_timeout")
        state = self.coordinator.snapshot(route_id)["state"]
        if state in {ActivationState.READY.value, ActivationState.PINNED.value}:
            return ActivationResult(True, state, True)
        return ActivationResult(False, state, True, "resume_failed")

    def release_request(self, route_id: str) -> None:
        self.coordinator.end_request(route_id)

    def suspend_due(self, *, suspend: Callable[[str, int], bool]) -> tuple[ActivationResult, ...]:
        results: list[ActivationResult] = []
        for route_id in self.coordinator.due_for_suspend():
            policy = self.coordinator.snapshot(route_id)["policy"]
            try:
                ok = bool(suspend(route_id, int(policy["stop_grace_seconds"])))
            except Exception:
                ok = False
            if ok:
                self.coordinator.mark_asleep(route_id)
                results.append(ActivationResult(True, ActivationState.ASLEEP.value, False))
            else:
                self.coordinator.mark_error(route_id)
                results.append(ActivationResult(False, ActivationState.ERROR.value, False, "suspend_failed"))
        return tuple(results)


__all__ = ["ActivationResult", "ActivationService"]
