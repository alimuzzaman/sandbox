from __future__ import annotations

from collections.abc import Mapping
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

    @staticmethod
    def _descriptor_kind(descriptor: object) -> str:
        if isinstance(descriptor, Mapping):
            # Older WordPress descriptors omitted kind; retain that compatibility
            # default while validating any explicit value.
            kind = descriptor.get("kind", "wordpress")
        elif hasattr(descriptor, "kind"):
            kind = descriptor.kind
        else:
            raise ValueError("runtime descriptor must be a mapping or descriptor object")
        if (not isinstance(kind, str) or not kind or
                any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in kind)):
            raise ValueError("runtime descriptor kind is invalid")
        return kind

    def _resolve_kind(self, project_root: str, *, label: str,
                      capability: str | None = None) -> tuple[str | None, OperationError | None]:
        try:
            descriptor = self._resolve_descriptor(project_root, label=label)
            return self._descriptor_kind(descriptor), None
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return None, OperationError(
                code="invalid_descriptor",
                message=f"runtime descriptor is invalid: {exc}",
                requested_capability=capability,
            )

    def _capability_error(self, kind: str, capability: str) -> OperationError | None:
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

    def invoke(self, request: OperationRequest) -> OperationResult | OperationError:
        kind, error = self._resolve_kind(request.project_root, label=request.label,
                                         capability=request.operation)
        if error is not None:
            return error
        error = self._capability_error(kind, request.operation)
        if error is not None:
            return error
        spec = self._adapters.for_kind(kind)
        return spec.adapter.invoke(request)

    def check(self, project_root: str, capability: str, *, label: str = "default") -> OperationError | None:
        kind, error = self._resolve_kind(project_root, label=label, capability=capability)
        if error is not None:
            return error
        return self._capability_error(kind, capability)
