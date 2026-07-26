"""Host-local durable-job transport contract and launcher."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from sandbox.jobs.models import MAX_OUTPUT_PAGE_BYTES, validate_job_id
from sandbox.jobs.registry import JobNotFound


_DETACHED_SUPERVISORS: list[subprocess.Popen] = []


class JobTransport(Protocol):
    def submit(self, submission): ...
    def get(self, job_id: str): ...
    def read_output(self, job_id: str, query): ...


class LocalSupervisorLauncher:
    """Launch a supervisor with every standard descriptor disconnected."""

    def __call__(self, descriptor: Path) -> None:
        _DETACHED_SUPERVISORS[:] = [process for process in _DETACHED_SUPERVISORS if process.poll() is None]
        _DETACHED_SUPERVISORS.append(subprocess.Popen([sys.executable, "-m", "sandbox.jobs.supervisor", str(descriptor)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True))


@dataclass(frozen=True)
class LegacyAsyncJobAdapter:
    """Compatibility adapter for the historic 16-hex async-job contract.

    The old command keeps its result keys and identifier rules, but the call site
    now has one explicit adapter boundary.  It can be replaced with a durable
    transport in a composed environment without making the legacy module import
    or know about the new SQLite registry.
    """

    validate: Callable[[str], bool]
    status_reader: Callable[..., dict]
    canceler: Callable[[str], dict]

    def _check(self, job_id: str) -> str:
        if not self.validate(job_id):
            raise ValueError("invalid async job id")
        return job_id

    def status(self, job_id: str, *, offset: int = 0, limit: int = 1_048_576) -> dict:
        return self.status_reader(self._check(job_id), offset=offset, limit=limit)

    def cancel(self, job_id: str) -> dict:
        return self.canceler(self._check(job_id))


@dataclass(frozen=True)
class AsyncJobCompatibilityRouter:
    """Keep the historic async-job envelope while accepting durable job IDs.

    Sixteen-hex IDs remain exclusively owned by the legacy host-level runner.
    New thirty-two-hex IDs are read and cancelled through injected durable-service
    adapters, then translated to the old incremental polling fields.  Keeping
    these operations injected prevents this compatibility transport from owning
    registry, storage, or process mechanics.
    """

    legacy: LegacyAsyncJobAdapter
    durable_status: Callable[[str], dict]
    durable_output: Callable[..., dict]
    durable_cancel: Callable[[str], dict]

    @staticmethod
    def _is_durable(job_id: str) -> bool:
        try:
            return len(job_id) == 32 and validate_job_id(job_id) == job_id
        except (TypeError, ValueError):
            return False

    def _kind(self, job_id: str) -> str:
        if self.legacy.validate(job_id):
            return "legacy"
        if self._is_durable(job_id):
            return "durable"
        raise ValueError("invalid async job id")

    def status(self, job_id: str, *, offset: int = 0, limit: int = 1_048_576) -> dict:
        if self._kind(job_id) == "legacy":
            return self.legacy.status(job_id, offset=offset, limit=limit)
        try:
            snapshot = dict(self.durable_status(job_id))
        except JobNotFound:
            return {"job_id": job_id, "status": "not_found"}
        lifecycle = snapshot.get("lifecycle")
        terminal = {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}
        result = {
            "job_id": job_id,
            "status": "completed" if lifecycle in terminal else "running",
        }
        if snapshot.get("exit_code") is not None:
            result["exit_code"] = snapshot["exit_code"]
        try:
            page = dict(self.durable_output(
                job_id, offset=offset, limit=min(limit, MAX_OUTPUT_PAGE_BYTES)))
        except RuntimeError as exc:
            if str(exc) != "output_unavailable":
                raise
            page = {}
        result.update({
            "stdout": page.get("data", ""),
            "bytes_read": page.get("bytes_read", 0),
            "truncated": bool(page.get("has_more")),
        })
        return result

    def cancel(self, job_id: str) -> dict:
        if self._kind(job_id) == "legacy":
            return self.legacy.cancel(job_id)
        try:
            snapshot = dict(self.durable_cancel(job_id))
        except RuntimeError as exc:
            if str(exc) != "already_terminal":
                raise
            return {"job_id": job_id, "status": "completed", "killed": False}
        lifecycle = snapshot.get("lifecycle")
        return {
            "job_id": job_id,
            "status": "completed" if lifecycle in {"cancelled", "failed", "timed_out", "interrupted", "succeeded"} else "running",
            "killed": lifecycle in {"cancelling", "cancelled"},
            "lifecycle": lifecycle,
        }
