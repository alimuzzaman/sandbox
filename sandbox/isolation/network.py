"""Compile per-instance point-to-point, default-deny nftables policy."""

from __future__ import annotations

import ipaddress
import re
import hashlib


_ID = re.compile(r"^sb-[a-f0-9]{12,32}$")


class NetworkPolicyCompiler:
    def __init__(self, *, resolver=None): self.resolver = resolver

    def _networks(self, grant):
        result = []
        for destination in grant.destinations:
            try: values = (ipaddress.ip_network(destination, strict=False),)
            except ValueError:
                if self.resolver is None: raise ValueError("hostname egress grant is unresolved")
                values = tuple(ipaddress.ip_network(f"{value}/32", strict=False)
                               for value in self.resolver(destination))
            for network in values:
                if network.version != 4 or network.is_private or network.is_loopback \
                        or network.is_link_local or network.is_multicast or network.is_unspecified:
                    raise ValueError("resolved egress destination must be public IPv4")
                result.append(network)
        return tuple(result)

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
        routes, grant_ids = [], []
        for grant in grants:
            if grant.revoked: continue
            grant_ids.append(grant.grant_id)
            for network in self._networks(grant):
                routes.append(str(network))
                for allowed_port in grant.ports:
                    rules.insert(-1, f"iifname \"{veth}\" ip saddr {guest.ip} "
                                    f"ip daddr {network} tcp dport {allowed_port} "
                                    f"ct state new counter accept comment \"{grant.grant_id}\"")
        return {"table": table, "family": "inet", "forward_policy": "drop",
                "host_address": str(host), "guest_address": str(guest),
                "veth": veth, "rules": tuple(rules), "default_route": False,
                "routes": tuple(sorted(set(routes))), "grant_ids": tuple(grant_ids),
                "grant_counters": {grant_id: f"{table}_{grant_id}" for grant_id in grant_ids}}


class SubnetAllocator:
    def __init__(self, pool="10.203.0.0/16"):
        self.pool = ipaddress.ip_network(pool)
        if self.pool.version != 4 or self.pool.prefixlen > 28:
            raise ValueError("managed subnet pool is invalid")

    def allocate(self, machine_id, *, used=()):
        if not _ID.fullmatch(machine_id): raise ValueError("managed network identity is invalid")
        subnets = tuple(self.pool.subnets(new_prefix=30)); occupied = {
            ipaddress.ip_network(value, strict=False) for value in used}
        start = int(hashlib.sha256(machine_id.encode()).hexdigest()[:8], 16) % len(subnets)
        for offset in range(len(subnets)):
            subnet = subnets[(start + offset) % len(subnets)]
            if any(subnet.overlaps(value) for value in occupied): continue
            host, guest = tuple(subnet.hosts())
            return {"subnet": str(subnet), "host_address": f"{host}/30",
                    "guest_address": f"{guest}/30",
                    "veth": "ve-" + hashlib.sha256(machine_id.encode()).hexdigest()[:10]}
        raise ValueError("managed subnet pool is exhausted")


class ManagedNetwork:
    """Delegate only digest-bound network lifecycle verbs to the root helper."""

    def __init__(self, *, process, helper):
        self.process = process
        self.helper = helper

    def plan(self, policy):
        network = dict(policy.network)
        if network.get("egress") != "deny" or network.get("default_route") is not False:
            raise ValueError("managed network must remain default-deny")
        if not isinstance(network.get("ingress_port"), int):
            raise ValueError("managed ingress port is unavailable")
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                **network}

    def _run(self, verb, plan):
        return self.process.run(("sudo", "-n", self.helper, verb,
                                 plan["machine_id"], plan["policy_digest"]), timeout=120)

    def apply(self, plan):
        result = self._run("network-apply", plan)
        if result.returncode != 0:
            raise RuntimeError("managed default-deny network apply failed")
        return {"ok": True, "mutated": True}

    def status(self, plan):
        result = self._run("network-status", plan)
        return {"ok": result.returncode == 0, "mutated": False,
                "stdout": result.stdout or ""}

    def remove(self, plan):
        result = self._run("network-remove", plan)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}
