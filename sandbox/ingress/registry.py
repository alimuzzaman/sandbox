"""Deterministic proof-gated ingress adapter registry."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SupportDeclaration


@dataclass(frozen=True)
class IngressAdapterSpec:
    declaration: SupportDeclaration
    adapter: object | None
    order: int

    @property
    def adapter_id(self):
        return self.declaration.adapter_id

    @property
    def adoptable(self):
        return self.adapter is not None and self.declaration.adoptable


class IngressAdapterRegistry:
    def __init__(self):
        self._items = {}

    def register(self, spec: IngressAdapterSpec):
        if spec.adapter_id in self._items:
            raise ValueError(f"duplicate ingress adapter: {spec.adapter_id}")
        self._items[spec.adapter_id] = spec

    def get(self, adapter_id):
        return self._items.get(adapter_id)

    def items(self):
        return tuple(sorted(self._items.values(), key=lambda item: (item.order, item.adapter_id)))

    def candidates(self, *, platform, capabilities=()):
        required = frozenset(capabilities)
        return tuple(item for item in self.items()
                     if platform in item.declaration.platforms
                     and required.issubset(item.declaration.capabilities))
