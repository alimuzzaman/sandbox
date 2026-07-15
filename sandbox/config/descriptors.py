from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_BASENAMES = ("sandbox.config.json", "sandbox.config.yml", "sandbox.config.yaml")
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


def discover_project_kind(root: str | Path) -> str:
    """Read only the committed native descriptor needed to select its schema."""
    root = Path(root).expanduser().resolve()
    for name in CONFIG_BASENAMES:
        path = root / name
        if path.exists():
            kind = _load_mapping(path).get("kind", "wordpress")
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError("project kind must be a non-empty string")
            kind = kind.strip().lower()
            return "compose" if kind in COMPOSE_KIND_ALIASES else kind
    return "wordpress"
