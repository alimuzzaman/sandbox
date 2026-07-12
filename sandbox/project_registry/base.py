from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Mapping, Protocol

RegistryRecord = dict[str, Any]


class RegistryError(RuntimeError):
    pass


class RegistryCorruption(RegistryError):
    pass


class UnsupportedRegistryVersion(RegistryError):
    pass


class AmbiguousRegistryIdentity(RegistryError):
    pass


class RegistryRepository(Protocol):
    def all(self) -> Mapping[str, RegistryRecord]: ...
    def get(self, root: str, label: str | None = None) -> RegistryRecord | None: ...
    def list_for_root(self, root: str) -> list[RegistryRecord]: ...
    def put(self, root: str, label: str = "default", **fields: Any) -> RegistryRecord: ...
    def remove(self, root: str, label: str | None = None) -> bool: ...
    def transaction(self) -> AbstractContextManager["RegistryRepository"]: ...
