"""Schema registration is explicit; dynamic project code is never imported."""

from sandbox.runtimes.base import SchemaRegistry, SchemaSpec

__all__ = ["SchemaRegistry", "SchemaSpec"]
