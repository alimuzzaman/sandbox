from __future__ import annotations

from pathlib import Path
from typing import Callable

from .descriptors import discover_project_kind
from .compose import ComposeSchemaProvider
from .registry import SchemaRegistry
from .wordpress import WordPressSchemaProvider
from .manifest import apply_common_config


def resolve_project_config(
    project_dir,
    *,
    label: str | None = None,
    legacy_loader: Callable,
    schemas: SchemaRegistry | None = None,
    root_finder: Callable | None = None,
) -> dict:
    """Select a schema before invoking any kind-specific normalization."""
    root = root_finder(project_dir) if root_finder else Path(project_dir).expanduser()
    kind = discover_project_kind(root)
    registry = schemas or SchemaRegistry()
    if registry.get("wordpress") is None:
        registry.register(
            "wordpress", WordPressSchemaProvider(legacy_loader),
            owner="sandbox.config.wordpress", order=10,
        )
    if registry.get("compose") is None:
        registry.register("compose", ComposeSchemaProvider(), owner="sandbox.config.compose", order=20)
    spec = registry.get(kind)
    if spec is None:
        raise ValueError(f"unsupported project kind: {kind}")
    result = spec.provider.resolve(root, label=label)
    if not isinstance(result, dict):
        raise TypeError(f"schema {kind!r} returned {type(result).__name__}, expected dict")
    result.setdefault("kind", kind)
    return apply_common_config(result)
