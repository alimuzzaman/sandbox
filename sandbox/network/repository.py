"""Locked, atomic storage for attributable resolver integration state."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator

from .models import CleanupRecovery, ConsentRecord, ResolutionBinding


CURRENT_VERSION = 1


def _empty() -> dict[str, Any]:
    return {
        "version": CURRENT_VERSION,
        "bindings": {},
        "authority": None,
        "consents": {},
        "recovery": {},
    }


class DomainRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = threading.RLock()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with self._thread_lock:
            descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _read(self) -> tuple[dict[str, Any], bool]:
        if not self.path.exists():
            return _empty(), False
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict):
            raise ValueError("resolver state must be an object")
        migrated = value.get("version") != CURRENT_VERSION
        if value.get("version") not in {None, 0, 1}:
            raise ValueError("unsupported resolver state version")
        result = _empty()
        for key in ("bindings", "consents", "recovery"):
            item = value.get(key, {})
            if not isinstance(item, dict):
                raise ValueError(f"resolver state {key} must be an object")
            result[key] = item
        result["authority"] = value.get("authority")
        return result, migrated

    def _write(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        descriptor, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        with self._lock():
            value, _migrated = self._read()
            working = copy.deepcopy(value)
            yield working
            working["version"] = CURRENT_VERSION
            self._write(working)

    def snapshot(self) -> dict[str, Any]:
        with self._lock():
            value, migrated = self._read()
            if migrated:
                self._write(value)
            return copy.deepcopy(value)

    def binding(self, binding_id: str) -> ResolutionBinding | None:
        value = self.snapshot()["bindings"].get(binding_id)
        return ResolutionBinding.from_dict(value) if value is not None else None

    def put_binding(self, binding: ResolutionBinding) -> None:
        with self.transaction() as value:
            existing = value["bindings"].get(binding.binding_id)
            if existing is not None:
                prior = ResolutionBinding.from_dict(existing)
                if (prior.adapter_id != binding.adapter_id or prior.name != binding.name
                        or prior.target != binding.target):
                    raise ValueError("binding identity collision")
                binding = prior.with_owners(tuple((*prior.owners, *binding.owners)))
            value["bindings"][binding.binding_id] = binding.to_dict()

    def release_binding_owner(self, binding_id: str, owner: str) -> str:
        """Release one owner, retaining external state until the final owner."""
        with self.transaction() as value:
            raw = value["bindings"].get(binding_id)
            if raw is None:
                return "absent"
            binding = ResolutionBinding.from_dict(raw)
            if owner not in binding.owners:
                return "absent"
            remaining = tuple(item for item in binding.owners if item != owner)
            if not remaining:
                return "last"
            value["bindings"][binding_id] = binding.with_owners(remaining).to_dict()
            return "retained"

    def remove_binding_if_unchanged(self, binding_id: str, observed_digest: str) -> str:
        with self.transaction() as value:
            raw = value["bindings"].get(binding_id)
            if raw is None:
                return "absent"
            binding = ResolutionBinding.from_dict(raw)
            if not binding.last_applied_digest:
                raise ValueError("binding has no applied ownership state")
            if observed_digest != binding.last_applied_digest:
                value["recovery"][binding_id] = CleanupRecovery(
                    binding_id, binding.adapter_id, binding.last_applied_digest,
                    observed_digest, "observed_state_changed", None, "drifted",
                ).to_dict()
                return "drifted"
            del value["bindings"][binding_id]
            value["recovery"].pop(binding_id, None)
            return "removed"

    def put_consent(self, consent: ConsentRecord) -> None:
        with self.transaction() as value:
            value["consents"][consent.owner_id] = consent.to_dict()

    def remove_consent(self, owner_id: str) -> bool:
        with self.transaction() as value:
            return value["consents"].pop(owner_id, None) is not None

    def put_recovery(self, recovery: CleanupRecovery) -> None:
        with self.transaction() as value:
            value["recovery"][recovery.binding_id] = recovery.to_dict()
