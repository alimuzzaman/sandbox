from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol


class JobBackend(Protocol):
    def run(self, target: str, prompt: str, worktree: str | None = None) -> dict: ...
    def status(self, remote: str, job_id: str, offset: int = 0) -> dict: ...
    def cancel(self, remote: str, job_id: str) -> dict: ...
    def cleanup(self, remote: str, confirm: bool, dry_run: bool) -> dict: ...


@dataclass
class HermesJobService:
    backend: JobBackend
    _runs: dict[str, dict] = field(default_factory=dict, init=False, repr=False)
    _run_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def run(self, target: str, prompt: str, *, worktree: str | None = None,
            idempotency_key: str | None = None) -> dict:
        """Start one bounded job; a duplicate key returns its first result."""
        if not str(target or "").strip() or not str(prompt or "").strip():
            raise ValueError("job target and prompt are required")
        if idempotency_key is None:
            return self.backend.run(target, prompt, worktree=worktree)
        key = str(idempotency_key)
        if not key:
            raise ValueError("idempotency key must not be empty")
        with self._run_lock:
            existing = self._runs.get(key)
            if existing is not None:
                return dict(existing)
            started = self.backend.run(target, prompt, worktree=worktree)
            self._runs[key] = dict(started)
            return started

    def status(self, remote: str, job_id: str, offset: int = 0) -> dict:
        return self.backend.status(remote, job_id, offset)

    def cancel(self, remote: str, job_id: str) -> dict:
        return self.backend.cancel(remote, job_id)

    def cleanup(self, remote: str, *, confirm: bool = False, dry_run: bool = True) -> dict:
        return self.backend.cleanup(remote, confirm, dry_run)
