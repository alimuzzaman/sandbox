from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class HermesStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesState:
    schema_version: int = 1
    installation: dict[str, Any] | None = None
    sessions: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "installation": dict(self.installation or {}),
            "sessions": dict(self.sessions or {}),
        }


class HermesStateRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    def read(self) -> HermesState:
        if not self.path.exists():
            return HermesState()
        try:
            value = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise HermesStateError(f"invalid Hermes state: {exc}") from exc
        if value.get("schema_version", 1) != 1:
            raise HermesStateError("unsupported Hermes state schema")
        return HermesState(
            schema_version=1,
            installation=value.get("installation") or {},
            sessions=value.get("sessions") or {},
        )

    def write(self, state: HermesState) -> None:
        if state.schema_version != 1:
            raise HermesStateError("unsupported Hermes state schema")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(mode=0o600, exist_ok=True)
        with self.lock_path.open("r+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(state.as_dict(), stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
