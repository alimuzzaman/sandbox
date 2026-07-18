"""Host-local durable-job transport contract and launcher."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


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
