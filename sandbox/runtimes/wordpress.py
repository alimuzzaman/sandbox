from __future__ import annotations

from collections.abc import Callable, Mapping

from .base import OperationRequest, OperationResult


class WordPressAdapter:
    adapter_id = "wordpress"
    kinds = ("wordpress",)

    def __init__(self, operations: Mapping[str, Callable], *, capabilities=()) -> None:
        self._operations = dict(operations)
        self.capabilities = frozenset((*self._operations, *capabilities))

    def invoke(self, request: OperationRequest) -> OperationResult:
        handler = self._operations[request.operation]
        value = handler(request)
        if isinstance(value, OperationResult):
            return value
        if not isinstance(value, dict):
            value = {"value": value}
        return OperationResult(
            ok=bool(value.get("ok", True)),
            operation=request.operation,
            project_root=request.project_root,
            project_kind="wordpress",
            data=value,
        )
