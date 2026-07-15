from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence


CAPABILITY_ENSURE = "ensure"
CAPABILITY_STATUS = "status"
CAPABILITY_START = "start"
CAPABILITY_STOP = "stop"
CAPABILITY_LOGS = "logs"
CAPABILITY_EXEC = "exec"
CAPABILITY_APPLY = "apply"
CAPABILITY_DESTROY = "destroy"
CAPABILITY_OPEN = "open"

SHARED_CAPABILITIES = frozenset({
    CAPABILITY_ENSURE,
    CAPABILITY_STATUS,
    CAPABILITY_START,
    CAPABILITY_STOP,
    CAPABILITY_LOGS,
    CAPABILITY_EXEC,
    CAPABILITY_APPLY,
    CAPABILITY_DESTROY,
    CAPABILITY_OPEN,
})


@dataclass(frozen=True)
class RuntimeDependencies:
    """Explicitly supplied shared services available to a runtime adapter."""

    process: Any
    http: Any
    ports: Any
    paths: Any
    proxy: Any
    registry: Any
    clock: Any | None = None


@dataclass(frozen=True)
class ProjectDescriptor:
    root: str
    kind: str = "wordpress"
    label: str = "default"
    common: Mapping[str, Any] = field(default_factory=dict)
    settings: Mapping[str, Any] = field(default_factory=dict)
    source_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "common", MappingProxyType(dict(self.common)))
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))


@dataclass(frozen=True)
class OperationRequest:
    project_root: str
    operation: str
    label: str = "default"
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    operation: str
    project_root: str
    project_kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True)
class OperationError:
    code: str
    message: str
    project_kind: str | None = None
    requested_capability: str | None = None
    available_capabilities: tuple[str, ...] = ()
    suggestion: str | None = None


class SchemaProvider(Protocol):
    def resolve(self, root: str, layers: Sequence[Mapping[str, Any]]) -> ProjectDescriptor: ...


class RuntimeAdapter(Protocol):
    adapter_id: str
    kinds: tuple[str, ...]
    capabilities: frozenset[str]

    def invoke(self, request: OperationRequest) -> OperationResult: ...


@dataclass(frozen=True)
class SchemaSpec:
    kind: str
    provider: Any
    owner: str
    order: int = 100


class SchemaRegistry:
    def __init__(self) -> None:
        self._items: dict[str, SchemaSpec] = {}

    def register(self, kind: str, provider: Any, *, owner: str, order: int = 100) -> SchemaSpec:
        if kind in self._items:
            raise ValueError(f"duplicate schema kind: {kind}")
        spec = SchemaSpec(kind=kind, provider=provider, owner=owner, order=order)
        self._items[kind] = spec
        return spec

    def get(self, kind: str) -> SchemaSpec | None:
        return self._items.get(kind)

    def items(self) -> tuple[SchemaSpec, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: (item.order, item.kind)))


@dataclass(frozen=True)
class AdapterSpec:
    adapter_id: str
    adapter: Any
    kinds: tuple[str, ...]
    owner: str
    order: int = 100


class AdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, AdapterSpec] = {}
        self._kind_owners: dict[str, str] = {}

    def register(self, adapter_id: str, adapter: Any, *, kinds: Sequence[str], owner: str,
                 order: int = 100) -> AdapterSpec:
        if adapter_id in self._items:
            raise ValueError(f"duplicate adapter: {adapter_id}")
        normalized = tuple(kinds)
        for kind in normalized:
            prior = self._kind_owners.get(kind)
            if prior is not None:
                raise ValueError(f"kind {kind!r} already owned by adapter {prior!r}")
        spec = AdapterSpec(adapter_id, adapter, normalized, owner, order)
        self._items[adapter_id] = spec
        for kind in normalized:
            self._kind_owners[kind] = adapter_id
        return spec

    def for_kind(self, kind: str) -> AdapterSpec | None:
        adapter_id = self._kind_owners.get(kind)
        return self._items.get(adapter_id) if adapter_id else None

    def items(self) -> tuple[AdapterSpec, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: (item.order, item.adapter_id)))
