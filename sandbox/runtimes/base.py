from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping as ABCMapping
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence


def _registry_text(value: object, label: str) -> str:
    if (not isinstance(value, str) or not value or
            any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in value)):
        raise ValueError(f"{label} is invalid")
    return value


def _registry_order(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("registry order is invalid")
    return value


def _is_text_sequence(value: object) -> bool:
    return not isinstance(value, (str, bytes))


def _transport_text(value: object, label: str, *, allow_spaces: bool = True) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} is invalid")
    if not allow_spaces and any(char.isspace() for char in value):
        raise ValueError(f"{label} is invalid")
    return value


def _transport_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, ABCMapping):
        raise ValueError(f"{label} must be a mapping")
    return dict(value)


CAPABILITY_ENSURE = "ensure"
CAPABILITY_STATUS = "status"
CAPABILITY_START = "start"
CAPABILITY_STOP = "stop"
CAPABILITY_RESUME = "resume"
CAPABILITY_SUSPEND = "suspend"
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
    CAPABILITY_RESUME,
    CAPABILITY_SUSPEND,
    CAPABILITY_LOGS,
    CAPABILITY_EXEC,
    CAPABILITY_APPLY,
    CAPABILITY_DESTROY,
    CAPABILITY_OPEN,
})


_EXECUTION_PATHS = frozenset({
    "wordpress_cli", "wp_eval", "exec", "composer", "plugin_activation",
    "phpunit", "durable_job",
})


@dataclass(frozen=True)
class ExecutionRequest:
    """Validated project-code request for an isolation-aware runtime gateway."""

    project_root: str
    label: str
    entry_path: str
    argv: tuple[str, ...]
    timeout: int = 300
    config_file: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "project_root", _transport_text(self.project_root, "project root"))
        object.__setattr__(self, "label", _transport_text(self.label, "project label", allow_spaces=False))
        if self.entry_path not in _EXECUTION_PATHS:
            raise ValueError("execution entry path is invalid")
        if (not isinstance(self.argv, tuple) or not self.argv or
                any(not isinstance(item, str) or not item or "\x00" in item for item in self.argv)):
            raise ValueError("execution argv is invalid")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, int) or not 1 <= self.timeout <= 3600:
            raise ValueError("execution timeout is invalid")
        if self.config_file is not None:
            object.__setattr__(self, "config_file",
                               _transport_text(self.config_file, "config file"))


@dataclass(frozen=True)
class ExecutionResult:
    """Secret-free synchronous result shape returned by an execution gateway."""

    ok: bool
    exit_code: int | None
    state: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.ok, bool) or not isinstance(self.state, str) or not self.state:
            raise ValueError("execution result is invalid")
        if self.exit_code is not None and (isinstance(self.exit_code, bool) or
                                           not isinstance(self.exit_code, int)):
            raise ValueError("execution exit code is invalid")
        object.__setattr__(self, "data", MappingProxyType(_transport_mapping(self.data, "execution result data")))


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
class RuntimeSelection:
    project_root: str
    label: str
    mode: str
    adapter_id: str
    source: str
    versions: Mapping[str, str] = field(default_factory=dict)
    isolation_level: str = "compose"
    capabilities: frozenset[str] = frozenset()

    def __post_init__(self):
        if self.mode not in {"compose", "incumbent_native", "managed_native"}:
            raise ValueError("runtime selection mode is invalid")
        if self.source not in {"default", "project_requirement", "machine_override", "persisted"}:
            raise ValueError("runtime selection source is invalid")
        expected = {"compose": "compose", "incumbent_native": "trusted_shared_host",
                    "managed_native": "managed_container"}[self.mode]
        if self.isolation_level != expected:
            raise ValueError("runtime selection isolation level is invalid")
        object.__setattr__(self, "versions", MappingProxyType(dict(self.versions)))
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


@dataclass(frozen=True)
class RuntimeCapabilityDeclaration:
    adapter_id: str
    project_kinds: tuple[str, ...]
    modes: tuple[str, ...]
    isolation_level: str
    capabilities: frozenset[str]
    support_tier: str
    evidence_id: str | None = None

    @property
    def adoptable(self):
        return self.support_tier == "adoptable" and bool(self.evidence_id)


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
        object.__setattr__(self, "project_root", _transport_text(self.project_root, "project root"))
        object.__setattr__(self, "operation", _transport_text(self.operation, "operation", allow_spaces=False))
        object.__setattr__(self, "label", _transport_text(self.label, "project label", allow_spaces=False))
        object.__setattr__(self, "arguments", MappingProxyType(_transport_mapping(self.arguments, "operation arguments")))


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    operation: str
    project_root: str
    project_kind: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise ValueError("operation result status is invalid")
        object.__setattr__(self, "operation", _transport_text(self.operation, "operation", allow_spaces=False))
        object.__setattr__(self, "project_root", _transport_text(self.project_root, "project root"))
        object.__setattr__(self, "project_kind", _transport_text(self.project_kind, "project kind", allow_spaces=False))
        object.__setattr__(self, "data", MappingProxyType(_transport_mapping(self.data, "operation result data")))


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
        kind = _registry_text(kind, "schema kind")
        owner = _registry_text(owner, "schema owner")
        order = _registry_order(order)
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
        adapter_id = _registry_text(adapter_id, "adapter id")
        owner = _registry_text(owner, "adapter owner")
        order = _registry_order(order)
        values = kinds
        if not _is_text_sequence(values):
            raise ValueError("adapter kinds must be a sequence")
        normalized = tuple(_registry_text(kind, "adapter kind") for kind in values)
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("adapter kinds must be unique and non-empty")
        if adapter_id in self._items:
            raise ValueError(f"duplicate adapter: {adapter_id}")
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
