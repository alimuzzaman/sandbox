from __future__ import annotations

from collections.abc import Mapping
from typing import Callable

from sandbox.runtimes.base import (
    AdapterRegistry,
    OperationError,
    OperationRequest,
    OperationResult,
)
from sandbox.runtimes.wordpress import capability_envelope, safe_alternative


class RuntimeService:
    def __init__(self, *, resolve_descriptor: Callable, adapters: AdapterRegistry,
                 backends=None, resolve_persisted=None) -> None:
        self._resolve_descriptor = resolve_descriptor
        self._adapters = adapters
        self._backends = backends
        self._resolve_persisted = resolve_persisted

    def resolve_descriptor(self, project_root: str, *, label: str = "default"):
        """Load the exact, label-scoped descriptor through the application boundary."""
        return self._resolve_descriptor(project_root, label=label)

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

    @staticmethod
    def _unsupported_capability(kind: str, capability: str, adapter) -> OperationError:
        capabilities = frozenset(getattr(adapter, "capabilities", ()))
        return OperationError(
            code="unsupported_capability",
            message=f"project kind {kind!r} does not support {capability!r}",
            project_kind=kind,
            requested_capability=capability,
            available_capabilities=tuple(sorted(capabilities)),
            suggestion=safe_alternative(capability) or "Use an operation listed in available_capabilities.",
        )

    @staticmethod
    def _with_capability_envelope(result: OperationResult, adapter, *, runtime=None) -> OperationResult:
        data = dict(result.data)
        data.setdefault("capabilities", capability_envelope(adapter))
        if isinstance(runtime, Mapping):
            mode = runtime.get("mode", "compose")
            adapter_id = runtime.get("adapter", "compose")
            data.setdefault("runtime", {
                "mode": mode, "adapter": adapter_id,
                "isolation": "compose_container" if mode == "compose" else "declared",
            })
        return OperationResult(result.ok, result.operation, result.project_root,
                               result.project_kind, data)

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
            return self._unsupported_capability(kind, capability, spec.adapter)
        return None

    def _runtime_selection_error(self, descriptor, request: OperationRequest):
        """Reject implicit native selection and populated mode switches first."""
        runtime = descriptor.get("wordpressRuntime") if isinstance(descriptor, Mapping) else None
        if not isinstance(runtime, Mapping) or self._backends is None:
            return None, None
        mode = runtime.get("mode", "compose")
        adapter_id = runtime.get("adapter", "compose")
        persisted = self._resolve_persisted(request.project_root, request.label) \
            if self._resolve_persisted else None
        if persisted and persisted.get("populated") and (
            persisted.get("mode") != mode or persisted.get("adapter") != adapter_id
        ):
            return None, OperationError(
                "runtime_mode_change",
                "a populated instance cannot change runtime mode through an ordinary operation",
                "wordpress", request.operation,
                suggestion="Export, recreate in the explicit mode, then import.",
            )
        if mode != "compose" and not runtime.get("explicit"):
            return None, OperationError(
                "explicit_selection_required",
                "native runtime requires an explicit machine-local selection",
                "wordpress", request.operation, suggestion="Use a gitignored machine override.",
            )
        spec = self._backends.resolve("wordpress", mode, adapter_id)
        if spec is None:
            return None, OperationError("unsupported_runtime",
                                        f"runtime backend {adapter_id!r} is unavailable for {mode!r}",
                                        "wordpress", request.operation)
        return spec, None

    def invoke(self, request: OperationRequest) -> OperationResult | OperationError:
        try:
            descriptor = self._resolve_descriptor(request.project_root, label=request.label)
            kind = self._descriptor_kind(descriptor)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return OperationError("invalid_descriptor", f"runtime descriptor is invalid: {exc}",
                                  requested_capability=request.operation)
        if kind == "wordpress":
            spec, selection_error = self._runtime_selection_error(descriptor, request)
            if selection_error is not None:
                return selection_error
            if spec is not None:
                capabilities = frozenset(getattr(spec.adapter, "capabilities", ()))
                if request.operation not in capabilities:
                    return self._unsupported_capability(kind, request.operation, spec.adapter)
                result = spec.adapter.invoke(request)
                if not isinstance(result, OperationResult) or result.operation != request.operation \
                        or result.project_kind != kind:
                    return OperationError("invalid_adapter_result",
                                          "runtime adapter returned an invalid or mismatched operation result",
                                          kind, request.operation)
                runtime = descriptor.get("wordpressRuntime") \
                    if isinstance(descriptor, Mapping) else None
                return self._with_capability_envelope(result, spec.adapter, runtime=runtime)

        error = self._capability_error(kind, request.operation)
        if error is not None:
            return error
        spec = self._adapters.for_kind(kind)
        result = spec.adapter.invoke(request)
        expected_adapter_label = kind
        valid_result = isinstance(result, OperationResult)
        mismatch = False
        if valid_result:
            mismatch = (result.operation != request.operation or
                        result.project_kind != expected_adapter_label)
        if not valid_result or mismatch:
            return OperationError(
                code="invalid_adapter_result",
                message="runtime adapter returned an invalid or mismatched operation result",
                project_kind=kind,
                requested_capability=request.operation,
            )
        return self._with_capability_envelope(result, spec.adapter)

    def check(self, project_root: str, capability: str, *, label: str = "default") -> OperationError | None:
        try:
            descriptor = self._resolve_descriptor(project_root, label=label)
            kind = self._descriptor_kind(descriptor)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return OperationError(
                code="invalid_descriptor",
                message=f"runtime descriptor is invalid: {exc}",
                requested_capability=capability,
            )
        if kind == "wordpress":
            spec, selection_error = self._runtime_selection_error(
                descriptor, OperationRequest(project_root, capability, label=label),
            )
            if selection_error is not None:
                return selection_error
            if spec is not None:
                capabilities = frozenset(getattr(spec.adapter, "capabilities", ()))
                if capability not in capabilities:
                    return self._unsupported_capability(kind, capability, spec.adapter)
                return None
        return self._capability_error(kind, capability)
