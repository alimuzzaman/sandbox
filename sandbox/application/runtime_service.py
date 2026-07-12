from __future__ import annotations

from typing import Callable

from sandbox.runtimes.base import (
    AdapterRegistry,
    OperationError,
    OperationRequest,
    OperationResult,
)


class RuntimeService:
    def __init__(self, *, resolve_descriptor: Callable, adapters: AdapterRegistry) -> None:
        self._resolve_descriptor = resolve_descriptor
        self._adapters = adapters

    def invoke(self, request: OperationRequest) -> OperationResult | OperationError:
        error = self.check(request.project_root, request.operation, label=request.label)
        if error is not None:
            return error
        descriptor = self._resolve_descriptor(request.project_root, label=request.label)
        kind = descriptor.kind if hasattr(descriptor, "kind") else descriptor.get("kind", "wordpress")
        spec = self._adapters.for_kind(kind)
        return spec.adapter.invoke(request)

    def check(self, project_root: str, capability: str, *, label: str = "default") -> OperationError | None:
        descriptor = self._resolve_descriptor(project_root, label=label)
        kind = descriptor.kind if hasattr(descriptor, "kind") else descriptor.get("kind", "wordpress")
        spec = self._adapters.for_kind(kind)
        if spec is None:
            return OperationError(
                code="unsupported_kind",
                message=f"no runtime adapter is registered for project kind {kind!r}",
                project_kind=kind,
                requested_capability=capability,
            )
        capabilities = frozenset(getattr(spec.adapter, "capabilities", ()))
        if capability not in capabilities:
            return OperationError(
                code="unsupported_capability",
                message=f"project kind {kind!r} does not support {capability!r}",
                project_kind=kind,
                requested_capability=capability,
                available_capabilities=tuple(sorted(capabilities)),
                suggestion="Use an operation listed in available_capabilities.",
            )
        return None
