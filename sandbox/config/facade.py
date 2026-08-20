from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping
import hashlib
import re

from .descriptors import discover_project_kind
from .compose import ComposeSchemaProvider
from .registry import SchemaRegistry
from .wordpress import WordPressSchemaProvider
from .manifest import apply_common_config


_RUNTIME_ID_RE = re.compile(r"[^a-z0-9-]+")


def project_identity(
    descriptor: Mapping,
    *,
    label: str | None = None,
    remote: str | None = None,
    adapter: str | None = None,
    capabilities=None,
) -> dict:
    """Return the one kind-neutral identity envelope shared by adapters.

    The descriptor resolver is deliberately the only place that knows how a
    caller's path becomes a canonical project root.  Runtime adapters should
    consume this small envelope instead of deriving an instance name from a
    plugin slug, a WordPress container, or their own path hashing.  The
    optional ``remote`` component is part of the *execution context*, not the
    project identity itself, so a local and a remote execution of one project
    remain distinguishable without changing the stable root/label identity.

    ``descriptor`` is accepted as a mapping rather than a concrete schema
    object so both the CLI and MCP composition roots can use the same primitive
    without importing a runtime adapter.
    """
    if not isinstance(descriptor, Mapping):
        raise ValueError("project descriptor must be a mapping")
    raw_root = descriptor.get("root") or descriptor.get("project_root")
    if not isinstance(raw_root, (str, Path)) or not str(raw_root).strip():
        raise ValueError("project descriptor has no canonical root")
    root = Path(raw_root).expanduser().resolve()
    project_type = descriptor.get("kind", "wordpress")
    if not isinstance(project_type, str) or not project_type.strip():
        raise ValueError("project kind must be a non-empty string")
    project_type = project_type.strip().lower()
    selected_label = label if label is not None else descriptor.get("label", "default")
    if not isinstance(selected_label, str) or not selected_label.strip():
        raise ValueError("project label must be a non-empty string")
    selected_label = selected_label.strip()
    display_name = descriptor.get("display_name") or root.name
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = root.name
    display_name = display_name.strip()
    # Keep the readable name for presentation, but make the runtime identifier
    # safe and collision-resistant.  The digest is based only on canonical
    # root + label; no plugin-shaped field is consulted.
    readable = _RUNTIME_ID_RE.sub("-", display_name.lower()).strip("-") or "project"
    digest = hashlib.sha256(f"{root}\0{selected_label}".encode()).hexdigest()[:10]
    runtime_id = f"{readable[:40].rstrip('-')}-{digest}"
    # Keep the truncation local; avoid relying on a plugin slug or an
    # adapter-specific identity derivation.
    runtime_id = runtime_id[:63].rstrip("-")
    identity = hashlib.sha256(f"{root}\0{selected_label}".encode()).hexdigest()
    if remote is not None:
        if not isinstance(remote, str) or not remote.strip():
            raise ValueError("remote context must be a non-empty string")
        remote = remote.strip()
    capability_values = capabilities
    if capability_values is None:
        capability_values = descriptor.get("capabilities", ())
    if isinstance(capability_values, str) or capability_values is None:
        capability_values = ()
    try:
        capability_values = tuple(sorted({str(value) for value in capability_values}))
    except TypeError as exc:
        raise ValueError("project capabilities must be a sequence") from exc
    return {
        "identity": f"project:{identity}",
        "root": str(root),
        "canonical_root": str(root),
        "label": selected_label,
        "display_name": display_name,
        "runtime_id": runtime_id,
        "kind": project_type,
        "adapter": adapter or descriptor.get("adapter") or f"{project_type}/1",
        "capabilities": capability_values,
        "remote": remote,
    }


def resolve_project_identity(
    project_dir,
    *,
    label: str | None = None,
    config_loader: Callable | None = None,
    remote: str | None = None,
    adapter: str | None = None,
    capabilities=None,
) -> dict:
    """Resolve a path once, then return :func:`project_identity`.

    Composition roots must pass their existing normalized ``config_loader``;
    keeping that dependency at the boundary prevents this pure config facade
    from importing the legacy compatibility module.
    """
    if isinstance(project_dir, Mapping):
        descriptor = project_dir
    else:
        if config_loader is None:
            raise ValueError(
                "config_loader is required when resolving a project path; "
                "pass the composition-root loader explicitly"
            )
        root = Path(project_dir).expanduser().resolve()
        try:
            descriptor = config_loader(str(root), label=label)
        except TypeError:
            # Small test/adapter loaders from older composition roots accepted
            # only the project path.  The canonical loader is label-aware, but
            # retaining this narrow compatibility fallback keeps the shared
            # identity primitive usable during adapter migration.
            descriptor = config_loader(str(root))
    return project_identity(
        descriptor, label=label, remote=remote, adapter=adapter,
        capabilities=capabilities,
    )


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
