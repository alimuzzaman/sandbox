"""Deterministic idle-stop scheduler with fail-closed activity evidence."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

from .catalog import ActivationCatalog, ActivationRoute
from .coordinator import ActivationPolicy, ActivationState
from .service import ActivationService


@dataclass(frozen=True)
class ScanResult:
    route_id: str
    action: str
    reason: str


class TcpActivityObserver:
    """Observe established backend connections using fixed host utilities.

    ``None`` means evidence is unavailable and therefore pins the route.
    """
    def __call__(self, port: int) -> bool | None:
        if sys.platform == "darwin" and shutil.which("lsof"):
            result = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED", "-t"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode not in {0, 1}:
                return None
            return bool(result.stdout.strip())
        if sys.platform.startswith("linux") and shutil.which("ss"):
            result = subprocess.run(
                ["ss", "-Htn", "state", "established", f"sport = :{port}"],
                capture_output=True, text=True, timeout=5,
            )
            return bool(result.stdout.strip()) if result.returncode == 0 else None
        return None


class ActivationScheduler:
    def __init__(self, catalog: ActivationCatalog, service: ActivationService, *,
                 observe_state: Callable[[ActivationRoute], str],
                 activity_safe: Callable[[ActivationRoute], tuple[bool, str]],
                 suspend: Callable[[ActivationRoute, int], bool],
                 clock: Callable[[], float] | None = None) -> None:
        self.catalog, self.service = catalog, service
        self.observe_state, self.activity_safe, self.suspend = observe_state, activity_safe, suspend
        self.clock = clock or time.monotonic
        self._routes = {route.route_id: route for route in catalog.routes()}

    def reconcile(self) -> tuple[ScanResult, ...]:
        results = []
        for route in self._routes.values():
            observed = self.observe_state(route)
            state = (ActivationState.READY if observed == "ready" else
                     ActivationState.ASLEEP if observed == "asleep" else ActivationState.ERROR)
            policy = route.policy
            self.service.coordinator.register(route.route_id, ActivationPolicy(
                mode=str(policy["mode"]), wake_on_request=bool(policy["wakeOnRequest"]),
                idle_after_seconds=int(policy["idleAfterSeconds"]),
                wake_timeout_seconds=int(policy["wakeTimeoutSeconds"]),
                stop_grace_seconds=int(policy["stopGraceSeconds"]),
                max_pending_requests=int(policy["maxPendingRequests"]),
            ), state=state, reconcile=True)
            results.append(ScanResult(route.route_id, "reconciled", state.value))
        return tuple(results)

    def scan(self, *, dry_run: bool = False, now: float | None = None) -> tuple[ScanResult, ...]:
        results: list[ScanResult] = []
        for claim in self.service.coordinator.claim_due_for_suspend(now=now):
            route = self._routes[claim.route_id]
            safe, reason = self.activity_safe(route)
            if not safe:
                self.service.coordinator.touch(route.route_id, now=now)
                results.append(ScanResult(route.route_id, "pinned", reason))
                continue
            if self.observe_state(route) != "ready":
                self.service.coordinator.mark_error(route.route_id, "runtime_state_uncertain",
                                                    generation=claim.generation, now=now)
                results.append(ScanResult(route.route_id, "pinned", "runtime_state_uncertain"))
                continue
            if dry_run:
                self.service.coordinator.touch(route.route_id, now=now)
                results.append(ScanResult(route.route_id, "would_suspend", "idle"))
                continue
            policy = self.service.coordinator.snapshot(route.route_id)["policy"]
            current, ok = self.service.coordinator.run_if_drain_current(
                claim, lambda: self.suspend(route, int(policy["stop_grace_seconds"])),
            )
            if not current:
                results.append(ScanResult(route.route_id, "cancelled", "new_activity"))
            elif ok and self.service.coordinator.mark_asleep(
                    route.route_id, generation=claim.generation, now=now):
                results.append(ScanResult(route.route_id, "suspended", "idle"))
            else:
                self.service.coordinator.mark_error(route.route_id, "suspend_failed",
                                                    generation=claim.generation, now=now)
                results.append(ScanResult(route.route_id, "error", "suspend_failed"))
        return tuple(results)

    def run(self, *, interval_seconds: int = 15, stop: threading.Event | None = None) -> None:
        if not 1 <= interval_seconds <= 300:
            raise ValueError("activation scan interval is invalid")
        stopper = stop or threading.Event()
        while not stopper.wait(interval_seconds):
            self.scan()


__all__ = ["ActivationScheduler", "ScanResult", "TcpActivityObserver"]
