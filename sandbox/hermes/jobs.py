from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class JobBackend(Protocol):
    def status(self, remote: str, job_id: str, offset: int = 0) -> dict: ...
    def cancel(self, remote: str, job_id: str) -> dict: ...
    def cleanup(self, remote: str, confirm: bool, dry_run: bool) -> dict: ...


@dataclass
class HermesJobService:
    backend: JobBackend

    def status(self, remote: str, job_id: str, offset: int = 0) -> dict:
        return self.backend.status(remote, job_id, offset)

    def cancel(self, remote: str, job_id: str) -> dict:
        return self.backend.cancel(remote, job_id)

    def cleanup(self, remote: str, *, confirm: bool = False, dry_run: bool = True) -> dict:
        return self.backend.cleanup(remote, confirm, dry_run)
