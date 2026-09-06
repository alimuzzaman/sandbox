"""Registered server-configuration adapters."""

from .base import (
    AdapterDescriptor,
    AdapterRegistry,
    RenderedFile,
    RenderedGeneration,
    ServerConfigAdapter,
)
from .manifest import default_adapter_registry

__all__ = (
    "AdapterDescriptor", "AdapterRegistry", "RenderedFile", "RenderedGeneration",
    "ServerConfigAdapter", "default_adapter_registry",
)
