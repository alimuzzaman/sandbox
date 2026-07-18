"""Explicit registration boundary for durable-job components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JobComponentSpec:
    component_id: str
    component: Any
    owner: str
    order: int = 100

    def __post_init__(self) -> None:
        for value, label in ((self.component_id, "component id"), (self.owner, "owner")):
            if (not isinstance(value, str) or not value or
                    any(char.isspace() or ord(char) < 32 or ord(char) == 127
                        for char in value)):
                raise ValueError(f"job {label} is invalid")
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise ValueError("job component order is invalid")


class JobComponentRegistry:
    """Small deterministic manifest; composition roots supply concrete services."""

    def __init__(self) -> None:
        self._items: dict[str, JobComponentSpec] = {}

    def register(self, component_id: str, component: Any, *, owner: str,
                 order: int = 100) -> JobComponentSpec:
        if component_id in self._items:
            raise ValueError(f"duplicate job component: {component_id}")
        spec = JobComponentSpec(component_id, component, owner, order)
        self._items[component_id] = spec
        return spec

    def get(self, component_id: str) -> JobComponentSpec | None:
        return self._items.get(component_id)

    def specs(self) -> tuple[JobComponentSpec, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: (item.order, item.component_id)))
