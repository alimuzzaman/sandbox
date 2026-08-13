from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_BASENAMES = ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml")
# A project may keep the complete Sandbox descriptor family in the repository
# root (the historical layout) or in this one conventional project-local
# directory.  The home is selected once per resolution; related override and
# label layers must never be mixed across homes.
CONFIG_SUBDIRECTORY = (".config", "sandbox")
COMPOSE_KIND_ALIASES = frozenset({
    "compose", "generic", "docker", "php", "javascript", "js", "node",
    "laravel", "laravel-sail", "astro",
})


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if path.suffix in {".yml", ".yaml"}:
        try:
            import yaml
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise ValueError(f"{path.name} requires PyYAML") from exc
        value = yaml.safe_load(text) or {}
    else:
        value = json.loads(text) if text.strip() else {}
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected an object at the top level")
    return value


def _first_config(home: Path) -> Path | None:
    """Return the first primary descriptor in a config home."""
    return next((home / name for name in CONFIG_BASENAMES
                 if (home / name).exists()), None)


def _inside(root: Path, path: Path, *, label: str) -> Path:
    """Resolve *path* and reject a config home that escapes its project root."""
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            f"Sandbox {label} must stay within the project root ({root})"
        ) from exc
    return resolved


def config_home(root: str | Path) -> Path:
    """Select the authoritative project-local Sandbox config home.

    Root-level configuration remains the compatibility default.  When the
    conventional ``.config/sandbox`` home contains a primary descriptor it
    owns the whole descriptor family.  Defining primary descriptors in both
    homes is ambiguous and fails closed before any schema-specific work.
    """
    root = Path(root).expanduser().resolve()
    root_home = root
    nested_home = root.joinpath(*CONFIG_SUBDIRECTORY)

    # A symlinked conventional directory is allowed only when it resolves
    # inside the project.  Do this check before looking for its descriptor so a
    # malicious external target cannot be selected by discovery.
    if nested_home.exists() or nested_home.is_symlink():
        _inside(root, nested_home, label="config directory")

    root_primary = _first_config(root_home)
    nested_primary = _first_config(nested_home) if nested_home.exists() else None
    if root_primary is not None and nested_primary is not None:
        raise ValueError(
            "ambiguous Sandbox project configuration: primary descriptors "
            f"exist in {root_home} and {nested_home}; keep exactly one "
            "config home (project root or .config/sandbox)"
        )
    return nested_home if nested_primary is not None else root_home


def primary_config(root: str | Path) -> Path | None:
    """Return the selected home's primary descriptor, if one exists."""
    return _first_config(config_home(root))


def config_layer(root: str | Path, names: tuple[str, ...], *, home: Path | None = None) -> Path | None:
    """Find one optional layer in the selected config home only."""
    selected = home if home is not None else config_home(root)
    return next((selected / name for name in names
                 if (selected / name).exists()), None)


def discover_project_kind(root: str | Path) -> str:
    """Read only the committed native descriptor needed to select its schema."""
    root = Path(root).expanduser().resolve()
    path = primary_config(root)
    if path is not None:
        kind = _load_mapping(path).get("kind", "wordpress")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("project kind must be a non-empty string")
        kind = kind.strip().lower()
        return "compose" if kind in COMPOSE_KIND_ALIASES else kind
    return "wordpress"
