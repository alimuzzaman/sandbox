"""Deterministic, side-effect-free test doubles for the Hermes extraction seams."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RecordingJobBackend:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    statuses: dict[str, dict[str, Any]] = field(default_factory=dict)

    def run(self, target: str, prompt: str, worktree: str | None = None) -> dict[str, Any]:
        job_id = f"job-{len([call for call in self.calls if call[0] == 'run']) + 1}"
        self.calls.append(("run", (target, prompt), {"worktree": worktree}))
        return {"job_id": job_id, "status": "running", "worktree": worktree}

    def status(self, remote: str, job_id: str, offset: int = 0) -> dict[str, Any]:
        self.calls.append(("status", (remote, job_id), {"offset": offset}))
        return self.statuses.get(job_id, {"job_id": job_id, "status": "not_found"})

    def cancel(self, remote: str, job_id: str) -> dict[str, Any]:
        self.calls.append(("cancel", (remote, job_id), {}))
        return {"job_id": job_id, "status": "cancelled"}

    def cleanup(self, remote: str, confirm: bool, dry_run: bool) -> dict[str, Any]:
        self.calls.append(("cleanup", (remote,), {"confirm": confirm, "dry_run": dry_run}))
        return {"status": "planned" if dry_run else "cleaned"}


@dataclass
class RecordingGatewayBackend:
    calls: list[tuple[str, Any]] = field(default_factory=list)
    fail_route: bool = False

    def apply_access(self, plan: Any) -> None:
        self.calls.append(("apply_access", plan))

    def apply_route(self, plan: Any) -> None:
        self.calls.append(("apply_route", plan))
        if self.fail_route:
            raise RuntimeError("route failed")

    def remove_route(self, plan: Any) -> None:
        self.calls.append(("remove_route", plan))

    def remove_access(self, plan: Any) -> None:
        self.calls.append(("remove_access", plan))

    # Legacy protocol methods remain solely so a missing split seam fails as an
    # assertion about ordering, not as an AttributeError from the test double.
    def apply(self, plan: Any) -> None:
        self.calls.append(("legacy_apply", plan))
        if self.fail_route:
            raise RuntimeError("route failed")

    def remove(self, plan: Any) -> None:
        self.calls.append(("legacy_remove", plan))


@dataclass
class RecordingArtifactStore:
    artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def put(self, artifact: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("put", artifact))
        self.artifacts[artifact["id"]] = dict(artifact)
        return dict(artifact)

    def list(self) -> tuple[dict[str, Any], ...]:
        self.calls.append(("list", None))
        return tuple(self.artifacts[key] for key in sorted(self.artifacts))

    def read(self, artifact_id: str) -> dict[str, Any]:
        self.calls.append(("read", artifact_id))
        return dict(self.artifacts[artifact_id])
