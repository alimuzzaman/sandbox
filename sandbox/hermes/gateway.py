from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True)
class GatewayPlan:
    fqdn: str
    origin: str
    access_first: bool = True
    basic_auth: bool = False


class GatewayBackend(Protocol):
    def apply_access(self, plan: GatewayPlan) -> Any: ...
    def apply_route(self, plan: GatewayPlan) -> Any: ...
    def remove_route(self, plan: GatewayPlan) -> Any: ...
    def remove_access(self, plan: GatewayPlan) -> Any: ...


class HermesGatewayService:
    def __init__(self, backend: GatewayBackend) -> None:
        self.backend = backend

    @staticmethod
    def _validate(fqdn: str, origin: str) -> None:
        labels = fqdn.split(".") if isinstance(fqdn, str) else ()
        valid_fqdn = (
            0 < len(fqdn) <= 253
            and all(
                0 < len(label) <= 63
                and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                for label in labels
            )
        )
        parsed = urlsplit(origin)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Hermes gateway origin must be explicit loopback HTTP") from exc
        if not valid_fqdn:
            raise ValueError("Hermes gateway FQDN must be a valid hostname")
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or port is None or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Hermes gateway origin must be explicit loopback HTTP")

    def plan(self, fqdn: str, origin: str, *, basic_auth: bool = False) -> GatewayPlan:
        self._validate(fqdn, origin)
        return GatewayPlan(fqdn=fqdn, origin=origin, basic_auth=basic_auth)

    def apply(self, plan: GatewayPlan) -> Any:
        """Validate, install access controls, then expose a route; unwind in reverse."""
        self._validate(plan.fqdn, plan.origin)
        self.backend.apply_access(plan)
        try:
            return self.backend.apply_route(plan)
        except Exception:
            try:
                self.backend.remove_route(plan)
            finally:
                self.backend.remove_access(plan)
            raise

    def remove(self, plan: GatewayPlan) -> None:
        """Validate, then reverse exposure order; safe backends may be idempotent."""
        self._validate(plan.fqdn, plan.origin)
        try:
            self.backend.remove_route(plan)
        finally:
            self.backend.remove_access(plan)
