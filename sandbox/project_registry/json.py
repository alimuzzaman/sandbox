from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .base import (
    AmbiguousRegistryIdentity,
    RegistryCorruption,
    RegistryRecord,
    UnsupportedRegistryVersion,
)

CURRENT_VERSION = 2


def _canonical(root: str) -> str:
    return str(Path(root).expanduser().resolve())


class JsonRegistryRepository:
    def __init__(self, path: str | Path, *, replace: Callable = os.replace) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name("registry.lock")
        self._replace = replace

    @contextmanager
    def transaction(self) -> Iterator["JsonRegistryRepository"]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield self
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"version": CURRENT_VERSION, "instances": {}}
        except json.JSONDecodeError as exc:
            raise RegistryCorruption(f"invalid registry JSON: {exc.msg}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("instances", {}), dict):
            raise RegistryCorruption("registry must contain an instances object")
        version = value.get("version", 1)
        if not isinstance(version, int) or version < 1:
            raise RegistryCorruption("registry version must be a positive integer")
        if version > CURRENT_VERSION:
            raise UnsupportedRegistryVersion(
                f"registry version {version} is newer than supported version {CURRENT_VERSION}"
            )
        value.setdefault("instances", {})
        return value

    @staticmethod
    def _migrate_v1(value: dict[str, Any]) -> dict[str, Any]:
        if value.get("version", 1) >= CURRENT_VERSION:
            return value
        instances = {}
        for root, original in value.get("instances", {}).items():
            entry = dict(original)
            label = entry.get("label", "default")
            instances[f"{root}::{label}"] = {
                **entry,
                "root": root,
                "label": label,
                "is_default": True,
            }
        return {**value, "version": CURRENT_VERSION, "instances": instances}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(value, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _read_current(self) -> tuple[dict[str, Any], bool]:
        value = self._read()
        migrated = value.get("version", 1) < CURRENT_VERSION
        return (self._migrate_v1(value) if migrated else value), migrated

    def all(self) -> dict[str, RegistryRecord]:
        with self.transaction():
            value, migrated = self._read_current()
            if migrated:
                self._write(value)
            return {key: dict(record) for key, record in value["instances"].items()}

    def list_for_root(self, root: str) -> list[RegistryRecord]:
        canonical = _canonical(root)
        records = [record for record in self.all().values() if record.get("root") == canonical]
        records.sort(key=lambda item: (not item.get("is_default"), item.get("label", "")))
        return records

    def get(self, root: str, label: str | None = None) -> RegistryRecord | None:
        records = self.list_for_root(root)
        if label is not None:
            return next((item for item in records if item.get("label") == label), None)
        if len(records) == 1:
            return records[0]
        return next((item for item in records if item.get("is_default")), None)

    def put(self, root: str, label: str = "default", **fields: Any) -> RegistryRecord:
        canonical = _canonical(root)
        key = f"{canonical}::{label}"
        with self.transaction():
            value, _migrated = self._read_current()
            prior = value["instances"].get(key, {})
            is_default = fields.pop("is_default", prior.get("is_default", None))
            if is_default is None:
                is_default = not any(
                    item.get("root") == canonical for item in value["instances"].values()
                )
            record = {
                **prior,
                **fields,
                "root": canonical,
                "label": label,
                "is_default": bool(is_default),
            }
            value["instances"][key] = record
            self._write(value)
            return dict(record)

    def remove(self, root: str, label: str | None = None) -> bool:
        canonical = _canonical(root)
        with self.transaction():
            value, _migrated = self._read_current()
            matches = [
                key for key, item in value["instances"].items()
                if item.get("root") == canonical
            ]
            if label is not None:
                existed = value["instances"].pop(f"{canonical}::{label}", None) is not None
            elif len(matches) <= 1:
                existed = bool(matches)
                for key in matches:
                    del value["instances"][key]
            else:
                raise AmbiguousRegistryIdentity(
                    f"{root!r} has {len(matches)} registry labels; pass label"
                )
            if existed:
                self._write(value)
            return existed
