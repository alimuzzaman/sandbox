"""Bounded relationship trigger and serialization ownership."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import threading
import time
from typing import Callable, Iterator

from .models import Participant, utc_now, validate_identifier


class RelationshipCoordinator:
    """Coalesce local triggers and serialize relationship operations.

    The journal still owns durable request replay. This object owns only the
    bounded process-local watcher state and a cross-process operation lock.
    """

    def __init__(self, repository, *, debounce_seconds: float = 0.25,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if debounce_seconds < 0 or debounce_seconds > 10:
            raise ValueError("sync debounce is outside the supported bound")
        self.repository = repository
        self.debounce_seconds = float(debounce_seconds)
        self.clock = clock
        self._guard = threading.Lock()
        self._inflight: set[str] = set()
        self._recent: dict[str, tuple[str, float]] = {}

    @contextmanager
    def serialize(self, relationship_id: str) -> Iterator[None]:
        """Take the relationship lock after any workspace operation lock."""
        validate_identifier(relationship_id, "relationship id")
        digest = hashlib.sha256(relationship_id.encode("utf-8")).hexdigest()[:24]
        path = Path(self.repository.lock_path).parent / f".relationship-{digest}.lock"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def participant(self, relationship_id: str, participant_id: str,
                    *, role: str = "participant") -> Participant:
        participant = Participant(
            participant_id=participant_id,
            relationship_id=relationship_id,
            last_seen_at=utc_now(),
            role=role,
        )
        return self.repository.register_participant(participant)

    def submit(self, relationship_id: str, trigger_id: str,
               operation: Callable[[], object]) -> bool:
        """Launch one non-blocking operation; duplicate bursts coalesce."""
        validate_identifier(relationship_id, "relationship id")
        validate_identifier(trigger_id, "trigger id")
        now = self.clock()
        with self._guard:
            recent = self._recent.get(relationship_id)
            if relationship_id in self._inflight:
                return False
            if recent is not None and recent[0] == trigger_id and now - recent[1] < self.debounce_seconds:
                return False
            self._inflight.add(relationship_id)
            self._recent[relationship_id] = (trigger_id, now)

        def run() -> None:
            try:
                with self.serialize(relationship_id):
                    operation()
            except Exception:
                # The durable service records bounded failure/pending state.
                # A commit/event caller must never inherit this exception.
                pass
            finally:
                with self._guard:
                    self._inflight.discard(relationship_id)

        threading.Thread(
            target=run, name=f"sandbox-sync-{relationship_id[-12:]}", daemon=True,
        ).start()
        return True


__all__ = ["RelationshipCoordinator"]
