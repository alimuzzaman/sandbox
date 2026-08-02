"""Deterministic, secret-free composition for one managed WordPress guest."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path

from sandbox.isolation.models import (
    EGRESS_GRANT_AUTHORITY, EgressGrant, EgressGrantSet, ManagedIsolationPolicy,
    canonical_digest,
)


class ManagedPolicyStore:
    """Stage and install only the canonical policy consumed by the fixed helper."""

    def __init__(self, *, process, helper, staging_root="/var/lib/sandbox/native/staging"):
        self.process = process
        self.helper = helper
        self.staging_root = Path(staging_root)

    def _stage(self, policy):
        path = self.staging_root / f"{policy.machine_id}.json"
        self.staging_root.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(policy.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    def install(self, policy):
        if not isinstance(policy, ManagedIsolationPolicy):
            raise ValueError("managed policy is invalid")
        path = self._stage(policy)
        try:
            result = self.process.run(("sudo", "-n", self.helper, "policy-install",
                                       policy.machine_id, str(path)), timeout=120)
        finally:
            path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError("managed policy installation failed")
        return {"ok": True, "mutated": True}

    def status(self, plan):
        result = self.process.run(("sudo", "-n", self.helper, "policy-status",
                                   plan["machine_id"]), timeout=30)
        return {"ok": result.returncode == 0, "mutated": False,
                "stdout": result.stdout or ""}

    def remove(self, plan):
        result = self.process.run(("sudo", "-n", self.helper, "policy-remove",
                                   plan["machine_id"], plan["policy_digest"]), timeout=120)
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0}


class ManagedPlanBuilder:
    """Build one canonical plan without reading secrets or mutating the host.

    Network reservation is the sole local mutation and is locked by the native
    repository.  The private UID base is derived from that unique /30 slot, so
    concurrent instances cannot receive overlapping 65536-ID ranges.
    """

    def __init__(self, *, repository, packages, resources, network, image,
                 apparmor, machine, database, services, web_server="nginx",
                 descriptor_resolver=None, paths=None):
        self.repository = repository
        self.packages = packages
        self.resources = resources
        self.network = network
        self.image = image
        self.apparmor = apparmor
        self.machine = machine
        self.database = database
        self.services = services
        self.web_server = web_server
        self.descriptor_resolver = descriptor_resolver
        self.paths = paths

    def _identity(self, request):
        root = Path(request.project_root).expanduser().resolve(strict=True)
        if self.paths is not None:
            root = Path(self.paths.require_allowed(root))
        if not root.is_dir():
            raise ValueError("managed project root is unavailable")
        owner = {"project_root": str(root), "label": request.label}
        machine_id = "sb-" + hashlib.sha256(
            f"{owner['project_root']}\0{owner['label']}".encode()).hexdigest()[:16]
        return root, owner, machine_id

    @staticmethod
    def _uid_base(allocation):
        pool = ipaddress.ip_network("10.203.0.0/16")
        subnet = ipaddress.ip_network(allocation["subnet"])
        if not subnet.subnet_of(pool) or subnet.prefixlen != 30:
            raise ValueError("managed network reservation is invalid")
        slot = (int(subnet.network_address) - int(pool.network_address)) // 4
        return (1000 + slot) * 65536

    def __call__(self, request):
        root, owner, machine_id = self._identity(request)
        allocation = self.repository.reserve_network(machine_id, owner=owner)
        descriptor = self.descriptor_resolver(str(root), label=request.label) \
            if self.descriptor_resolver is not None else {}
        runtime = descriptor.get("wordpressRuntime", {}) if isinstance(descriptor, dict) else {}
        values = request.arguments.get("resources", runtime.get("resources", {}))
        resources = self.resources.compile(values)
        requested_egress = (request.arguments["egress"] if "egress" in request.arguments
                            else runtime.get("egress", ()))
        if not isinstance(requested_egress, (tuple, list)):
            raise ValueError("managed egress grant set is invalid")
        grants = []
        for raw in requested_egress:
            if not isinstance(raw, dict): raise ValueError("managed egress grant is invalid")
            grants.append(EgressGrant(
                raw.get("grant_id"), raw.get("owner", machine_id), raw.get("kind"),
                tuple(raw.get("destinations", ())), tuple(raw.get("ports", ())),
                raw.get("expires_at"), revoked=raw.get("revoked", False),
            ))
        web_server = request.arguments.get("web_server", runtime.get("webServer") or self.web_server)
        network_values = {
            "egress": "deny", "veth": allocation["veth"],
            "host_address": allocation["host_address"],
            "guest_address": allocation["guest_address"],
            "default_route": False, "ingress_port": 8080,
            "grant_authority": EGRESS_GRANT_AUTHORITY,
        }
        database = self.database.plan(
            owner=f"{owner['project_root']}::{owner['label']}", machine_id=machine_id)
        policy = ManagedIsolationPolicy(
            policy_version=1,
            machine_id=machine_id,
            uid_map={"base": self._uid_base(allocation), "count": 65536},
            root_image={
                "path": f"/var/lib/sandbox/native/instances/{machine_id}/root.img",
                "bytes": resources["disk_bytes"], "inodes": resources["inodes"],
            },
            read_only_mounts=({"source": str(root), "target": "/workspace"},),
            writable_mounts=(),
            network=network_values,
            syscalls={"no_new_privileges": True, "seccomp": "managed-v1"},
            devices=frozenset(),
            resources={key: resources[key] for key in (
                "cpu_percent", "memory_bytes", "pids", "runtime_seconds",
                "disk_bytes", "inodes", "fds", "connections", "io_weight")},
            credentials=database["credential_refs"],
        )
        grant_set = EgressGrantSet(machine_id, policy.digest, tuple(grants))
        package_plan = self.packages.plan(web_server=web_server)
        service_plan = self.services.compile(policy, web_server=web_server)
        database_plan = {**database, "policy_digest": policy.digest}
        record = {
            "owner": owner, "mode": "managed_native", "adapter": "ubuntu-nspawn",
            "backend": service_plan["backend"],
            "machine": {"id": machine_id, "policy_digest": policy.digest},
            "php": {"web": "8.3", "cli": "8.3"},
            "database": {"production": database["production"], "tests": database["tests"],
                         "user": database["user"], "network_exposed": False},
            "files": {"image": canonical_digest(dict(policy.root_image))},
            "health": "ready", "policy_digest": policy.digest,
            "grant_digest": grant_set.digest,
        }
        record["last_applied"] = canonical_digest(record)
        plan = {
            "machine_id": machine_id, "policy_digest": policy.digest,
            "policy": policy, "web_server": web_server,
            "grant_set": grant_set,
            "package_plan": package_plan,
            "apparmor": self.apparmor.plan(policy),
            "image": self.image.plan(policy),
            "machine": self.machine.plan(policy),
            "network": self.network.plan(policy),
            "database": database_plan, "services": service_plan,
            "wordpress": {"machine_id": machine_id, "policy_digest": policy.digest},
            "record": record,
        }
        cleanup_plans = {
            "services": service_plan, "database": database_plan,
            "machine": plan["machine"], "network": plan["network"],
            "mount": plan["image"], "image": plan["image"],
            "policy": {"machine_id": machine_id, "policy_digest": policy.digest},
        }
        plan["cleanup"] = {
            name: {
                "expected": {
                    "machine_id": machine_id, "policy_digest": policy.digest,
                    "resource": name,
                    "resource_digest": (service_plan["digest"]
                                        if name == "services" else policy.digest),
                },
                "plan": value,
            }
            for name, value in cleanup_plans.items()
        }
        return plan
