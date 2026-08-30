"""Source-owned production qualification for proven incumbent ingress."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path


_SYSTEM_CADDY_EXECUTABLES = frozenset({
    Path("/usr/bin/caddy"),
    Path("/usr/sbin/caddy"),
    Path("/usr/local/bin/caddy"),
    Path("/usr/local/sbin/caddy"),
})


@dataclass(frozen=True)
class ProductionIngressQualification:
    adapter_id: str
    products: tuple[str, ...]
    platforms: tuple[str, ...]
    capabilities: frozenset[str]
    evidence_id: str

    def authority(self, *, observation, platform, protocols, capabilities):
        """Return the exact listener authority the root preflight must prove."""
        if platform not in self.platforms:
            return None
        if frozenset(protocols) != frozenset({"http"}):
            return None
        if frozenset(capabilities) != self.capabilities:
            return None
        if observation is None or observation.adapter_id != self.adapter_id:
            return None
        if str(observation.product).casefold() not in {
                product.casefold() for product in self.products}:
            return None

        relevant = tuple(endpoint for endpoint in observation.endpoints
                         if endpoint.protocol == "tcp" and endpoint.port == 80)
        if not relevant:
            return None
        identities = set()
        socket_ids = set()
        listen_addresses = set()
        for endpoint in relevant:
            try:
                address = ipaddress.ip_address(endpoint.address)
            except ValueError:
                return None
            process = dict(endpoint.process or {})
            command = str(process.get("command") or "").casefold()
            executable_path = Path(str(process.get("executable") or ""))
            executable = executable_path.name.casefold()
            executable_digest = str(process.get("executable_digest") or "")
            pid = str(process.get("pid") or "")
            start = str(process.get("start") or "")
            identity = (
                pid,
                start,
                str(executable_path),
                executable_digest,
            )
            if (endpoint.owner_confidence != "proven"
                    or not endpoint.socket_id
                    or not (address.is_loopback or address.is_unspecified)
                    or command != "caddy"
                    or executable != "caddy"
                    or executable_path not in _SYSTEM_CADDY_EXECUTABLES
                    or not pid.isdigit()
                    or not start.isdigit()
                    or int(pid) <= 1
                    or len(executable_digest) != 64
                    or any(char not in "0123456789abcdef" for char in executable_digest)
                    or not str(endpoint.socket_id).isdigit()):
                return None
            identities.add(identity)
            socket_ids.add(str(endpoint.socket_id))
            listen_addresses.add(str(address))
        if len(identities) != 1:
            return None
        pid, start, _executable, executable_digest = identities.pop()
        return {
            "pid": int(pid),
            "start": start,
            "executable_digest": executable_digest,
            "socket_ids": tuple(sorted(socket_ids)),
            "listen_address": tuple(sorted(listen_addresses))[0],
            "listen_port": 80,
        }

    def qualifies(self, *, observation, platform, protocols, capabilities,
                  control_ready) -> bool:
        """Accept only after root binds the observed socket to caddy.service."""
        return self.authority(
            observation=observation,
            platform=platform,
            protocols=protocols,
            capabilities=capabilities,
        ) is not None and control_ready is True


SYSTEM_CADDY_QUALIFICATION = ProductionIngressQualification(
    adapter_id="system-caddy",
    products=("caddy",),
    platforms=("linux",),
    capabilities=frozenset({"http"}),
    evidence_id="037-t044-ubuntu-2404",
)
