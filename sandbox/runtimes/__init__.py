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
from .wordpress import WordPressAdapter
from .compose import ComposeAdapter
from .manifest import RUNTIME_DECLARATIONS


def builtin_adapter_registry(operations: Mapping[str, Callable], *, compose=None) -> AdapterRegistry:
    """Compose the shipped adapters in deterministic, feature-owned order."""
    registry = AdapterRegistry()
    registry.register(
        "wordpress", WordPressAdapter(operations), kinds=("wordpress",),
        owner="sandbox.runtimes.wordpress", order=10,
    )
    if compose is not None:
        registry.register(
            "compose", compose, kinds=("compose",),
            owner="sandbox.runtimes.compose", order=20,
        )
    return registry


__all__ = [
    "AdapterRegistry", "AdapterSpec", "OperationError", "OperationRequest",
    "OperationResult", "ProjectDescriptor", "RuntimeAdapter", "RuntimeDependencies",
    "SchemaRegistry", "SchemaSpec", "builtin_adapter_registry",
    "RUNTIME_DECLARATIONS",
]
