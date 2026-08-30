"""Source-owned production qualification for proven incumbent ingress."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path


@dataclass(frozen=True)
class ProductionIngressQualification:
    adapter_id: str
    products: tuple[str, ...]
    platforms: tuple[str, ...]
    capabilities: frozenset[str]
    evidence_id: str

    def qualifies(self, *, observation, platform, protocols, capabilities,
                  control_ready) -> bool:
        """Accept only the exact source-reviewed system-Caddy HTTP shape."""
        if platform not in self.platforms:
            return False
        if frozenset(protocols) != frozenset({"http"}):
            return False
        if frozenset(capabilities) != self.capabilities:
            return False
        if observation is None or observation.adapter_id != self.adapter_id:
            return False
        if str(observation.product).casefold() not in {
                product.casefold() for product in self.products}:
            return False
        if control_ready is not True:
            return False

        relevant = tuple(endpoint for endpoint in observation.endpoints
                         if endpoint.protocol == "tcp" and endpoint.port == 80)
        if not relevant:
            return False
        identities = set()
        for endpoint in relevant:
            try:
                address = ipaddress.ip_address(endpoint.address)
            except ValueError:
                return False
            process = dict(endpoint.process or {})
            command = str(process.get("command") or "").casefold()
            executable_path = Path(str(process.get("executable") or ""))
            executable = executable_path.name.casefold()
            pid = str(process.get("pid") or "")
            start = str(process.get("start") or "")
            identity = (
                pid,
                start,
                executable,
            )
            if (endpoint.owner_confidence != "proven"
                    or not endpoint.socket_id
                    or not (address.is_loopback or address.is_unspecified)
                    or command != "caddy"
                    or executable != "caddy"
                    or not executable_path.is_absolute()
                    or not pid.isdigit()
                    or not start.isdigit()
                    or int(pid) <= 0):
                return False
            identities.add(identity)
        return len(identities) == 1


SYSTEM_CADDY_QUALIFICATION = ProductionIngressQualification(
    adapter_id="system-caddy",
    products=("caddy",),
    platforms=("linux",),
    capabilities=frozenset({"http"}),
    evidence_id="037-t044-ubuntu-2404",
)
