"""Whether the default clean-URL proxy may stop now (spec 082).

The Sandbox Caddy proxy only routes clean hostnames to local Docker instances.
It is started on demand by instance lifecycle and must stop when nothing on the
machine needs it. This module is the one decision: callers gather the facts,
pass them here, and act on the result. It reads nothing and starts nothing.

Unknown is never idle: when any fact could not be observed the proxy is kept.
"""

from __future__ import annotations

from typing import Literal, NamedTuple


ProxyAction = Literal["stop", "keep", "none"]


class ProxyDemandFacts(NamedTuple):
    """What the caller observed. `None` means the fact could not be read."""

    proxy_running: bool | None
    running_instances: tuple[str, ...] | None
    wake_routes: int | None
    clean_urls_disabled: bool = False


class ProxyDemandDecision(NamedTuple):
    action: ProxyAction
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "reason": self.reason}


def decide(facts: ProxyDemandFacts) -> ProxyDemandDecision:
    """Stop only a running proxy that provably has no demand."""
    if facts.proxy_running is False:
        return ProxyDemandDecision("none", "proxy_not_running")
    if facts.proxy_running is None:
        return ProxyDemandDecision("keep", "proxy_state_unknown")
    # A running local instance always keeps the proxy, whatever the switches
    # say: stopping it would break a URL someone may be using right now.
    if facts.running_instances is None:
        return ProxyDemandDecision("keep", "instances_unknown")
    if facts.running_instances:
        return ProxyDemandDecision("keep", "instances_running")
    # Clean URLs switched off: nothing is routed through the proxy, so a wake
    # route cannot need it either.
    if facts.clean_urls_disabled:
        return ProxyDemandDecision("stop", "clean_urls_disabled")
    if facts.wake_routes is None:
        return ProxyDemandDecision("keep", "wake_routes_unknown")
    if facts.wake_routes > 0:
        return ProxyDemandDecision("keep", "wake_routes")
    return ProxyDemandDecision("stop", "idle")


__all__ = ["ProxyAction", "ProxyDemandDecision", "ProxyDemandFacts", "decide"]
