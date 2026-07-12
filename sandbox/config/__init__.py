"""Project descriptor and kind-specific schema ownership."""

from .descriptors import discover_project_kind
from .facade import resolve_project_config
from .registry import SchemaRegistry

__all__ = ["SchemaRegistry", "discover_project_kind", "resolve_project_config"]
