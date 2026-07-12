from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from dependencies import ToolDependencies


@dataclass(frozen=True)
class ToolGroupSpec:
    group_id: str
    register: Callable
    owner: str
    dependencies: tuple[str, ...] = ()
    order: int = 100
    project_scope: str = "legacy"
    required_capability: str | None = None


class ToolGroupRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolGroupSpec] = {}

    def add(self, spec: ToolGroupSpec) -> ToolGroupSpec:
        if spec.group_id in self._specs:
            raise ValueError(f"duplicate tool group: {spec.group_id}")
        self._specs[spec.group_id] = spec
        return spec

    def specs(self) -> tuple[ToolGroupSpec, ...]:
        return tuple(sorted(self._specs.values(), key=lambda item: (item.order, item.group_id)))

    def compose(self, server, dependencies: ToolDependencies) -> None:
        for spec in self.specs():
            for key in spec.dependencies:
                dependencies.require(key)
            spec.register(server, dependencies)

    def group_ids(self) -> tuple[str, ...]:
        return tuple(spec.group_id for spec in self.specs())
