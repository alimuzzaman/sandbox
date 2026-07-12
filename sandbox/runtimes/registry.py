from __future__ import annotations

from .base import AdapterRegistry
from .wordpress import WordPressAdapter


def wordpress_registry(operations, *, capabilities=()) -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(
        "wordpress",
        WordPressAdapter(operations, capabilities=capabilities),
        kinds=("wordpress",),
        owner="sandbox.runtimes.wordpress",
        order=10,
    )
    return registry
