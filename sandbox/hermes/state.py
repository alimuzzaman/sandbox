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
    extra: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value = dict(self.extra or {})
        value.update({
            "schema_version": self.schema_version,
            "installation": dict(self.installation or {}),
            "sessions": dict(self.sessions or {}),
        })
        return value


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
        if not isinstance(value, dict):
            raise HermesStateError("invalid Hermes state: root must be an object")
        if (isinstance(value.get("schema_version", 1), bool) or
                value.get("schema_version", 1) != 1):
            raise HermesStateError("unsupported Hermes state schema")
        for field in ("installation", "sessions"):
            if field in value and value[field] is not None and not isinstance(value[field], dict):
                raise HermesStateError(f"invalid Hermes state: {field} must be an object")
        known = {"schema_version", "installation", "sessions"}
        return HermesState(
            schema_version=1,
            installation=value.get("installation") or {},
            sessions=value.get("sessions") or {},
            extra={key: item for key, item in value.items() if key not in known},
        )

    def write(self, state: HermesState) -> None:
        if (isinstance(state.schema_version, bool) or state.schema_version != 1 or
                not isinstance(state.installation, (dict, type(None))) or
                not isinstance(state.sessions, (dict, type(None))) or
                not isinstance(state.extra, (dict, type(None)))):
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
                directory_fd = os.open(str(self.path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
