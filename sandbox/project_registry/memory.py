from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .base import AmbiguousRegistryIdentity, RegistryRecord
from .validation import canonical_root, validate_label


class MemoryRegistryRepository:
    def __init__(self, records: dict[str, RegistryRecord] | None = None) -> None:
        self._records = {key: dict(value) for key, value in (records or {}).items()}

    def all(self) -> dict[str, RegistryRecord]:
        return {key: dict(value) for key, value in self._records.items()}

    def read_only_all(self) -> dict[str, RegistryRecord]:
        return self.all()

    def list_for_root(self, root: str) -> list[RegistryRecord]:
        canonical = canonical_root(root)
        records = [dict(item) for item in self._records.values() if item.get("root") == canonical]
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
        canonical = canonical_root(root)
        label = validate_label(label)
        key = f"{canonical}::{label}"
        prior = self._records.get(key, {})
        is_default = fields.pop("is_default", prior.get("is_default", None))
        if is_default is None:
            is_default = not any(item.get("root") == canonical for item in self._records.values())
        record = {**prior, **fields, "root": canonical, "label": label,
                  "is_default": bool(is_default)}
        self._records[key] = record
        return dict(record)

    def remove(self, root: str, label: str | None = None) -> bool:
        canonical = canonical_root(root)
        if label is not None:
            label = validate_label(label)
        matches = [key for key, item in self._records.items() if item.get("root") == canonical]
        if label is not None:
            return self._records.pop(f"{canonical}::{label}", None) is not None
        if len(matches) > 1:
            raise AmbiguousRegistryIdentity(
                f"{root!r} has multiple registry labels; pass label"
            )
        if not matches:
            return False
        del self._records[matches[0]]
        return True

    @contextmanager
    def transaction(self) -> Iterator["MemoryRegistryRepository"]:
        yield self
