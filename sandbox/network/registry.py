"""Explicit resolver-adapter ownership and proof gates."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .models import SUPPORT_TIERS


_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


@dataclass(frozen=True)
class ResolverAdapterSpec:
    adapter_id: str
    adapter: Any
    managers: tuple[str, ...]
    platforms: tuple[str, ...]
    support_tier: str
    capabilities: frozenset[str]
    evidence_id: str | None
    order: int = 100

    def __post_init__(self) -> None:
        if not _ID.fullmatch(self.adapter_id):
            raise ValueError("resolver adapter id is invalid")
        if not self.managers or not all(isinstance(item, str) and item for item in self.managers):
            raise ValueError("resolver adapter managers are invalid")
        if not self.platforms or not all(isinstance(item, str) and item for item in self.platforms):
            raise ValueError("resolver adapter platforms are invalid")
        if self.support_tier not in SUPPORT_TIERS:
            raise ValueError("resolver adapter support tier is invalid")
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise ValueError("resolver adapter order is invalid")
        if self.support_tier == "adoptable" and not self.evidence_id:
            raise ValueError("adoptable resolver adapter requires live evidence")

    @property
    def adoptable(self) -> bool:
        return self.support_tier == "adoptable" and bool(self.evidence_id)


class ResolverAdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ResolverAdapterSpec] = {}

    def register(self, spec: ResolverAdapterSpec) -> ResolverAdapterSpec:
        if spec.adapter_id in self._items:
            raise ValueError(f"duplicate resolver adapter: {spec.adapter_id}")
        self._items[spec.adapter_id] = spec
        return spec

    def get(self, adapter_id: str) -> ResolverAdapterSpec | None:
        return self._items.get(adapter_id)

    def items(self) -> tuple[ResolverAdapterSpec, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: (item.order, item.adapter_id)))

    def matching(self, manager: str, platform: str) -> tuple[ResolverAdapterSpec, ...]:
        return tuple(item for item in self.items()
                     if manager in item.managers and platform in item.platforms)
