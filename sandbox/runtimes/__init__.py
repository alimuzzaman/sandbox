"""Runtime contracts. Production adapters are registered explicitly."""

from .base import (
    AdapterRegistry,
    AdapterSpec,
    OperationError,
    OperationRequest,
    OperationResult,
    ProjectDescriptor,
    RuntimeAdapter,
    SchemaRegistry,
    SchemaSpec,
)

__all__ = [
    "AdapterRegistry", "AdapterSpec", "OperationError", "OperationRequest",
    "OperationResult", "ProjectDescriptor", "RuntimeAdapter", "SchemaRegistry",
    "SchemaSpec",
]
