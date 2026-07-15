"""Runtime contracts and explicit built-in adapter composition."""

from collections.abc import Callable, Mapping

from .base import (
    AdapterRegistry,
    AdapterSpec,
    OperationError,
    OperationRequest,
    OperationResult,
    ProjectDescriptor,
    RuntimeAdapter,
    RuntimeDependencies,
    SchemaRegistry,
    SchemaSpec,
)
from .registry import wordpress_registry


def builtin_adapter_registry(operations: Mapping[str, Callable]) -> AdapterRegistry:
    """Register only adapters implemented in this increment.

    Compose remains deliberately unregistered until its validated adapter exists;
    callers receive the structured unsupported-kind result instead of a fallback.
    """
    return wordpress_registry(operations)


__all__ = [
    "AdapterRegistry", "AdapterSpec", "OperationError", "OperationRequest",
    "OperationResult", "ProjectDescriptor", "RuntimeAdapter", "RuntimeDependencies",
    "SchemaRegistry", "SchemaSpec", "builtin_adapter_registry",
]
