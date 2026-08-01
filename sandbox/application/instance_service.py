"""Small registry-owned instance identity transitions shared by clean naming."""

from __future__ import annotations

from pathlib import Path

from sandbox.config.domains import normalize_hostname


def persist_hostname_intent(registry, project_root: str, label: str,
                            hostname: str, source: str) -> dict:
    root = str(Path(project_root).expanduser().resolve())
    hostname = normalize_hostname(hostname)
    current = registry.registry_get(root, label=label)
    if not current:
        raise ValueError("project instance must be registered before hostname persistence")
    if current.get("domain"):
        return current
    return registry.registry_put(
        root, label=label, domain=hostname, domain_source=source,
    )
