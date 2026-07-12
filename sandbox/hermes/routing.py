from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    profile: str
    model: str
    effort: str
    write_allowed: bool


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
