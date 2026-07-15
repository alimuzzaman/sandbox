from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class RouteDecision:
    profile: str
    model: str
    effort: str
    write_allowed: bool


@dataclass(frozen=True)
class HermesTarget:
    """Validated remote target resolved without loading transport providers."""
    name: str
    host: str
    metadata: Mapping[str, Any]


def resolve_target(target: str, remotes: Mapping[str, Mapping[str, Any]]) -> HermesTarget:
    """Resolve one configured, provisioned remote without side effects."""
    name = str(target or "").strip()
    if not name:
        raise ValueError("Hermes target name is required")
    entry = remotes.get(name)
    if not isinstance(entry, Mapping):
        raise ValueError(f"unknown Hermes target: {name}")
    host = entry.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"Hermes target {name!r} has no host")
    if entry.get("provisioned") is False:
        raise ValueError(f"Hermes target {name!r} is not provisioned")
    return HermesTarget(name=name, host=host.strip(), metadata=dict(entry))


def recommended_route(task_class: str, *, failures: int = 0,
                      security_sensitive: bool = False) -> RouteDecision:
    """Pure routing policy matching the repository's model boundaries."""
    normalized = task_class.strip().lower()
    if security_sensitive or failures >= 2 or normalized in {
        "architecture", "threat-model", "data-loss", "auth",
    }:
        return RouteDecision("sol", "gpt-5.6-sol", "high", False)
    if normalized in {"inventory", "triage", "summarize", "read-only"}:
        return RouteDecision("luna", "gpt-5.6-luna", "medium", False)
    return RouteDecision("terra", "gpt-5.6-terra", "high", True)
