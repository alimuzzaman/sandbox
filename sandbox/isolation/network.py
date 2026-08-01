"""Compile per-instance point-to-point, default-deny nftables policy."""

from __future__ import annotations

import ipaddress
import re


_ID = re.compile(r"^sb-[a-f0-9]{12,32}$")


class NetworkPolicyCompiler:
    def compile(self, *, machine_id, veth, host_address, guest_address,
                ingress_port, grants=()):
        if not _ID.fullmatch(machine_id) or not re.fullmatch(r"ve-[a-z0-9-]{1,12}", veth):
            raise ValueError("managed network identity is invalid")
        host = ipaddress.ip_interface(host_address)
        guest = ipaddress.ip_interface(guest_address)
        if host.network != guest.network or host.ip == guest.ip or host.network.num_addresses != 4:
            raise ValueError("managed network must be a unique point-to-point subnet")
        port = int(ingress_port)
        if not 1 <= port <= 65535: raise ValueError("managed ingress port is invalid")
        table = "sb_" + machine_id[3:]
        rules = [
            f"iifname \"{veth}\" ct state established,related accept",
            f"oifname \"{veth}\" ip daddr {guest.ip} tcp dport {port} ct state new accept",
            f"iifname \"{veth}\" ip saddr {guest.ip} counter drop",
        ]
        for grant in grants:
            for destination in grant.destinations:
                network = ipaddress.ip_network(destination, strict=False)
                for allowed_port in grant.ports:
                    rules.insert(-1, f"iifname \"{veth}\" ip saddr {guest.ip} "
                                    f"ip daddr {network} tcp dport {allowed_port} "
                                    f"ct state new counter accept comment \"{grant.grant_id}\"")
        return {"table": table, "family": "inet", "forward_policy": "drop",
                "host_address": str(host), "guest_address": str(guest),
                "veth": veth, "rules": tuple(rules), "default_route": False}
