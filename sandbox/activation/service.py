"""Single-flight request activation with per-generation completion signals."""

from __future__ import annotations

from dataclasses import dataclass
import threading
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
        self._events: dict[tuple[str, int], threading.Event] = {}
        self._lock = threading.RLock()

    def register(self, route_id: str, policy: ActivationPolicy,
                 *, state: ActivationState = ActivationState.ASLEEP) -> None:
        self.coordinator.register(route_id, policy, state=state)

    def activate(self, route_id: str, *, resume: Callable[[str, int], bool]) -> ActivationResult:
        initial = self.coordinator.snapshot(route_id)
        if initial["state"] in {ActivationState.READY.value, ActivationState.PINNED.value}:
            self.coordinator.touch(route_id)
            return ActivationResult(True, str(initial["state"]), False)

        with self._lock:
            claim = self.coordinator.claim_activation(route_id)
            if not claim.accepted:
                return ActivationResult(False, ActivationState.ERROR.value, False,
                                        "pending_request_limit")
            key = (route_id, claim.generation)
            event = self._events.setdefault(key, threading.Event())
        policy = self.coordinator.snapshot(route_id)["policy"]
        try:
            if claim.owner:
                try:
                    ok = bool(resume(route_id, int(policy["wake_timeout_seconds"])))
                except Exception:
                    ok = False
                if ok:
                    self.coordinator.mark_ready(route_id, generation=claim.generation)
                    result = ActivationResult(True, ActivationState.READY.value, True)
                else:
                    self.coordinator.mark_error(route_id, generation=claim.generation)
                    result = ActivationResult(False, ActivationState.ERROR.value, True,
                                              "resume_failed")
                event.set()
                return result

            waited = event.wait(timeout=int(policy["wake_timeout_seconds"]))
            if not waited:
                return ActivationResult(False, ActivationState.ERROR.value, False, "wake_timeout")
            state = str(self.coordinator.snapshot(route_id)["state"])
            if state in {ActivationState.READY.value, ActivationState.PINNED.value}:
                return ActivationResult(True, state, True)
            return ActivationResult(False, state, True, "resume_failed")
        finally:
            self.coordinator.end_request(route_id)
            if event.is_set():
                with self._lock:
                    self._events.pop(key, None)

    def release_request(self, route_id: str) -> None:
        """Compatibility surface for callers that used explicit claims."""
        self.coordinator.end_request(route_id)

    def suspend_due(self, *, suspend: Callable[[str, int], bool]) -> tuple[ActivationResult, ...]:
        results: list[ActivationResult] = []
        for claim in self.coordinator.claim_due_for_suspend():
            policy = self.coordinator.snapshot(claim.route_id)["policy"]
            current, ok = self.coordinator.run_if_drain_current(
                claim, lambda: suspend(claim.route_id, int(policy["stop_grace_seconds"])),
            )
            if not current:
                continue
            if ok and self.coordinator.mark_asleep(claim.route_id, generation=claim.generation):
                results.append(ActivationResult(True, ActivationState.ASLEEP.value, False))
            else:
                self.coordinator.mark_error(claim.route_id, generation=claim.generation)
                results.append(ActivationResult(False, ActivationState.ERROR.value, False,
                                                "suspend_failed"))
        return tuple(results)


__all__ = ["ActivationResult", "ActivationService"]
