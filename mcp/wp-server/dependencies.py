from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ToolDependencies:
    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        self._values = dict(values or {})

    def require(self, key: str) -> Any:
        if key not in self._values:
            raise KeyError(f"missing MCP dependency: {key}")
        return self._values[key]

    def with_value(self, key: str, value: Any) -> "ToolDependencies":
        return ToolDependencies({**self._values, key: value})
