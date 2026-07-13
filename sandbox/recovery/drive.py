from __future__ import annotations

from .errors import RecoveryError


class MemoryDrive:
    """Fixture-only immutable object store used by recovery tests."""
    def __init__(self) -> None: self.objects: dict[str, bytes] = {}
    def put(self, key: str, payload: bytes) -> None:
        if key in self.objects: raise RecoveryError("recovery object already exists", "object_exists")
        self.objects[key] = payload
    def get(self, key: str) -> bytes:
        try: return self.objects[key]
        except KeyError as exc: raise RecoveryError("recovery object is absent", "object_missing") from exc
