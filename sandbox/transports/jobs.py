"""Host-local durable-job transport contract and launcher."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol


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
