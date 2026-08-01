from __future__ import annotations

from .base import AdapterRegistry
from .wordpress import WordPressAdapter
from dataclasses import dataclass
from collections.abc import Sequence

from .base import _registry_text, _registry_order


@dataclass(frozen=True)
class RuntimeBackendSpec:
    adapter_id: str
    adapter: object
    project_kinds: tuple[str, ...]
    modes: tuple[str, ...]
    owner: str
    order: int
    declaration: object | None = None


class RuntimeBackendRegistry:
    """Resolve project kind + explicit local mode + adapter identity."""
    def __init__(self): self._items = {}

    def register(self, adapter_id, adapter, *, project_kinds, modes, owner,
                 order=100, declaration=None):
        adapter_id = _registry_text(adapter_id, "runtime backend id")
        owner = _registry_text(owner, "runtime backend owner")
        order = _registry_order(order)
        if isinstance(project_kinds, (str, bytes)) or isinstance(modes, (str, bytes)):
            raise ValueError("runtime backend kinds and modes must be sequences")
        kinds = tuple(_registry_text(item, "runtime backend kind") for item in project_kinds)
        normalized_modes = tuple(_registry_text(item, "runtime backend mode") for item in modes)
        if not kinds or not normalized_modes:
            raise ValueError("runtime backend kinds and modes must not be empty")
        keys = [(kind, mode, adapter_id) for kind in kinds for mode in normalized_modes]
        if any(key in self._items for key in keys):
            raise ValueError(f"duplicate runtime backend: {adapter_id}")
        spec = RuntimeBackendSpec(adapter_id, adapter, kinds, normalized_modes,
                                  owner, order, declaration)
        for key in keys: self._items[key] = spec
        return spec

    def resolve(self, project_kind, mode, adapter_id):
        return self._items.get((project_kind, mode, adapter_id))

    def items(self):
        unique = {item.adapter_id: item for item in self._items.values()}
        return tuple(sorted(unique.values(), key=lambda item: (item.order, item.adapter_id)))


def wordpress_registry(operations, *, capabilities=()) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(
        "wordpress",
        WordPressAdapter(operations, capabilities=capabilities),
        kinds=("wordpress",),
        owner="sandbox.runtimes.wordpress",
        order=10,
    )
    return registry
