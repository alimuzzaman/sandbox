"""Compile per-instance point-to-point, default-deny nftables policy."""

from __future__ import annotations

import ipaddress
import re
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Mapping

from sandbox.isolation.models import (
    EGRESS_GRANT_AUTHORITY, EgressGrantSet, ManagedIsolationPolicy,
    parse_utc_timestamp,
)


_ID = re.compile(r"^sb-[a-f0-9]{12,32}$")
# The helper treats no installed grant document as a distinct CAS state.  This
# is intentionally not the digest of an empty document: the first reconcile
# must prove there was no prior helper-owned capability to overwrite.
ABSENT_GRANT_DIGEST = "0" * 64


class NetworkPolicyCompiler:
    def __init__(self, *, resolver=None, clock=None):
        self.resolver = resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def compile(self, *, machine_id, veth, host_address, guest_address,
                ingress_port, grants=(), owner=None):
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
            f"oifname \"{veth}\" ip daddr {guest.ip} ct state established,related accept",
            f"oifname \"{veth}\" counter drop",
            f"iifname \"{veth}\" counter drop",
        ]
        grant_ids = []
        expected_owner = owner or machine_id
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("egress policy clock must be timezone-aware")
        now = now.astimezone(timezone.utc)
        for grant in grants:
            if grant.revoked: continue
            if grant.owner != expected_owner:
                raise ValueError("egress grant owner does not match the managed instance")
            if parse_utc_timestamp(grant.expires_at) <= now:
                raise ValueError("active egress grant is expired")
            grant_ids.append(grant.grant_id)
        if grant_ids:
            rules.insert(1, f'iifname "{veth}" ip saddr {guest.ip} ip daddr {host.ip} '
                            f'tcp dport 18443 ct state new counter accept '
                            f'comment "egress_broker_request"')
            rules.insert(-2, f'oifname "{veth}" ip saddr {host.ip} ip daddr {guest.ip} '
                             f'tcp sport 18443 ct state established,related counter accept '
                             f'comment "egress_broker_reply"')
        return {"table": table, "family": "inet", "forward_policy": "drop",
                "chain_policies": {"input": "accept", "output": "accept",
                                   "forward": "accept"},
                "host_address": str(host), "guest_address": str(guest),
                "veth": veth, "rules": tuple(rules), "default_route": False,
                "routes": (), "grant_ids": tuple(grant_ids),
                "broker": {"address": str(host.ip), "port": 18443,
                           "grant_ids": tuple(grant_ids)} if grant_ids else None,
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
        if (not isinstance(getattr(policy, "machine_id", None), str) or
                not isinstance(getattr(policy, "digest", None), str) or
                not isinstance(getattr(policy, "network", None), Mapping)):
            raise ValueError("managed network policy is invalid")
        network = dict(policy.network)
        if network.get("egress") != "deny" or network.get("default_route") is not False:
            raise ValueError("managed network must remain default-deny")
        if not isinstance(network.get("ingress_port"), int):
            raise ValueError("managed ingress port is unavailable")
        if network.get("grant_authority") != EGRESS_GRANT_AUTHORITY:
            raise ValueError("managed network grant authority is unavailable")
        # Transitional empty lists are accepted for old persisted policy files,
        # but no capability data ever reaches a current policy plan.
        if network.pop("grants", ()) not in ((), []):
            raise ValueError("managed isolation policy must not embed egress grants")
        return {"machine_id": policy.machine_id, "policy_digest": policy.digest,
                **network}

    def _run(self, verb, plan):
        return self.process.run(("sudo", "-n", self.helper, verb,
                                 plan["machine_id"], plan["policy_digest"]), timeout=120)

    def apply(self, plan):
        stopped = self._run("egress-remove", plan)
        if stopped.returncode != 0:
            raise RuntimeError("managed egress could not be closed before reconcile")
        baseline = self._run("network-apply", plan)
        if baseline.returncode != 0:
            raise RuntimeError("managed default-deny network apply failed")
        return {"ok": True, "mutated": True}

    def status(self, plan):
        result = self._run("network-status", plan)
        try: observed = json.loads(result.stdout or "")
        except (TypeError, json.JSONDecodeError): observed = {}
        ok = bool(result.returncode == 0 and isinstance(observed, dict) and
                  observed.get("ok") is True and
                  observed.get("policy_digest") == plan["policy_digest"])
        return {"ok": ok, "mutated": False, "stdout": result.stdout or "",
                "counters": observed.get("counters", {}) if ok else {},
                "grant_counters": observed.get("grant_counters", {}) if ok else {},
                "observed": observed if ok else {}}

    def deactivate(self, plan):
        result = self._run("egress-remove", plan)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}

    def remove(self, plan):
        broker = self._run("egress-remove", plan)
        if broker.returncode != 0:
            return {"ok": False, "mutated": False}
        result = self._run("network-remove", plan)
        return {"ok": result.returncode == 0,
                "mutated": result.returncode == 0 or broker.returncode == 0}


class EgressGrantReconciler:
    """CAS-reconcile separately staged egress capabilities.

    The helper derives the staging path from the machine and desired digest,
    rather than accepting a caller-selected path.  The document is therefore
    both secret-free and unavailable to argv/path substitution attacks.
    """

    def __init__(self, *, process, helper,
                 staging_root="/var/lib/sandbox/native/staging"):
        self.process = process
        self.helper = helper
        self.staging_root = Path(staging_root)

    @staticmethod
    def _digest(value, label):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"{label} is invalid")
        return value

    def _path(self, grants):
        return self.staging_root / (
            f"grants-{os.getuid()}-{grants.digest}.json"
        )

    def _stage(self, grants):
        path = self._path(grants)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(grants.to_dict(), sort_keys=True,
                              separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def reconcile(self, policy, grants, *, expected_digest):
        if not isinstance(policy, ManagedIsolationPolicy) or not isinstance(grants, EgressGrantSet):
            raise ValueError("managed egress reconciliation is invalid")
        if grants.machine_id != policy.machine_id or grants.base_policy_digest != policy.digest:
            raise ValueError("managed egress reconciliation binding is invalid")
        expected = self._digest(expected_digest, "expected egress grant digest")
        path = self._stage(grants)
        try:
            result = self.process.run(
                ("sudo", "-n", self.helper, "grant-reconcile", policy.machine_id,
                 policy.digest, expected, grants.digest),
                timeout=120,
            )
        finally:
            path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("managed scoped egress reconciliation failed")
        return {"ok": True, "mutated": expected != grants.digest,
                "grant_digest": grants.digest}

    @staticmethod
    def empty(policy):
        if not isinstance(policy, ManagedIsolationPolicy):
            raise ValueError("managed egress policy is invalid")
        return EgressGrantSet(policy.machine_id, policy.digest)
