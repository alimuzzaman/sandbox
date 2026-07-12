from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class GatewayPlan:
    fqdn: str
    origin: str
    access_first: bool = True
    basic_auth: bool = False


class GatewayBackend(Protocol):
    def apply(self, plan: GatewayPlan) -> Any: ...
    def remove(self, plan: GatewayPlan) -> Any: ...


class HermesGatewayService:
    def __init__(self, backend: GatewayBackend) -> None:
        self.backend = backend

    def plan(self, fqdn: str, origin: str, *, basic_auth: bool = False) -> GatewayPlan:
        if not fqdn or not origin.startswith("http://127.0.0.1:"):
            raise ValueError("Hermes gateway origin must be explicit loopback HTTP")
        return GatewayPlan(fqdn=fqdn, origin=origin, basic_auth=basic_auth)

    def apply(self, plan: GatewayPlan) -> Any:
        try:
            return self.backend.apply(plan)
        except Exception:
            self.backend.remove(plan)
            raise
