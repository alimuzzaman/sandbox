"""Owned filesystem layout and atomic persistence helpers for durable jobs."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .models import validate_job_id


class StoragePressureError(RuntimeError):
    pass


class JobStorage:
    def __init__(self, runtime_dir: str | Path, *, free_disk_reserve: int = 2_147_483_648):
        self.root = Path(runtime_dir).expanduser().resolve() / "jobs"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.free_disk_reserve = int(free_disk_reserve)

    def job_dir(self, job_id: str, *, create: bool = False) -> Path:
        path = self.root / validate_job_id(job_id)
        if create:
            self.require_capacity(0)
            path.mkdir(mode=0o700, parents=False, exist_ok=False)
        return path

    def require_capacity(self, incoming_bytes: int) -> None:
        free = shutil.disk_usage(self.root).free
        if free - max(0, incoming_bytes) < self.free_disk_reserve:
            raise StoragePressureError("job storage free-disk reserve would be crossed")

    def is_under_pressure(self) -> bool:
        return shutil.disk_usage(self.root).free < self.free_disk_reserve

    @staticmethod
    def safe_relative(value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("job storage path must be relative and contained")
        return path

    def write_json_atomic(self, job_id: str, relative: str | Path,
                          value: Any, *, mode: int = 0o600) -> Path:
        directory = self.job_dir(job_id)
        target = directory / self.safe_relative(relative)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.require_capacity(len(payload))
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target
