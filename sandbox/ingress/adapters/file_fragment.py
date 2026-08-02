"""Shared fixed-helper boundary for attributable incumbent fragments."""

from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path

from sandbox.config.domains import normalize_hostname


class FileFragmentAdapter:
    adapter_id = ""
    extension = "conf"
    requires_baseline_samples = True

    def __init__(self, *, helper, process, network_root, render):
        self.helper = helper
        self.process = process
        self.network_root = Path(network_root).expanduser().resolve()
        self.staging_root = self.network_root / "ingress" / "candidates"
        self.render = render

    def plan_route(self, selection, naming, backend, prior=None):
        hostname = normalize_hostname(naming["hostname"])
        address = str(ipaddress.ip_address(backend["address"]))
        if not ipaddress.ip_address(address).is_loopback:
            raise ValueError("ingress backend must be loopback")
        port = int(backend["port"])
        if not 1 <= port <= 65535:
            raise ValueError("ingress backend port is invalid")
        protocols = tuple(sorted(selection.get("protocols") or ("http",)))
        if protocols != ("http",):
            raise ValueError("only exact HTTP ingress is proven")
        route_id = hashlib.sha256(
            f"{self.adapter_id}\0{naming['owner']}\0{hostname}".encode(),
        ).hexdigest()
        listen = dict(selection["listen"])
        listen_address = ipaddress.ip_address(listen["address"])
        if not listen_address.is_loopback or int(listen["port"]) != 80:
            raise ValueError("ingress listen endpoint must be exact loopback HTTP")
        listen = {"address": str(listen_address), "port": 80}
        content = self.render(route_id, hostname, address, port, listen,
                              bool(naming.get("wildcard")))
        authority = dict(selection.get("authority") or {})
        if self.adapter_id == "system-caddy" and not authority:
            raise ValueError("system Caddy socket authority is required")
        return {"adapter_id": self.adapter_id, "route_id": route_id,
                "owner": naming["owner"],
                "hostname": hostname, "backend": {"address": address, "port": port},
                "protocols": protocols,
                "listen": listen, "content": content,
                "content_digest": hashlib.sha256(content.encode()).hexdigest(),
                "authority": authority,
                "prior": prior}

    def _run(self, verb, *arguments, timeout=30):
        return self.process.run(("sudo", "-n", self.helper, verb, *map(str, arguments)),
                                timeout=timeout)

    def _plan_arguments(self, plan):
        listen = plan["listen"]
        authority = plan["authority"]
        return (
            self.network_root, self.adapter_id, plan["route_id"], plan["owner"],
            plan["hostname"], plan["backend"]["address"], plan["backend"]["port"],
            listen["address"], listen["port"], plan["content_digest"],
            authority["pid"], authority["start"], authority["executable_digest"],
            ",".join(authority["socket_ids"]),
            authority["observation_fingerprint"],
        )

    def authorize_plan(self, plan, *, interactive=False):
        arguments = self._plan_arguments(plan)
        status = self._run("authorization-status", *arguments, timeout=10)
        if status.returncode == 0:
            return {"ok": True, "state": "ready", "mutated": False}
        if not interactive:
            return {"ok": False, "state": "pending_privilege", "mutated": False,
                    "error": "root ingress authorization is required"}
        result = self.process.run(
            ("sudo", self.helper, "authorize", *map(str, arguments)), timeout=60,
        )
        return {"ok": result.returncode == 0,
                "state": "ready" if result.returncode == 0 else "pending_privilege",
                "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}

    def ready(self):
        result = self._run("preflight", self.network_root, self.adapter_id, timeout=10)
        return result.returncode == 0

    def baseline_urls(self, plan):
        # Baseline only the explicitly selected instance backend.  Probing
        # incumbent hostnames could make Caddy reach public, metadata, or a
        # sibling backend through ambient configuration.
        return ({"address": plan["backend"]["address"],
                 "port": plan["backend"]["port"], "host": "localhost"},)

    def validate_current(self, plan):
        result = self._run("validate-current", self.network_root, self.adapter_id, timeout=10)
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def capture_prior(self, plan):
        return {"route_id": plan["route_id"]}

    def stage_candidate(self, plan):
        # Root renders from the authorized scalar plan.  No user-writable
        # candidate crosses the privileged boundary, eliminating path/swap TOCTOU.
        return dict(plan)

    def validate_candidate(self, stage):
        result = self._run("prepare", *self._plan_arguments(stage))
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def activate(self, stage):
        result = self._run("activate", self.network_root, self.adapter_id,
                           stage["route_id"])
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def observe_route(self, plan):
        result = self._run("observe", self.network_root, self.adapter_id,
                           plan["route_id"], timeout=10)
        return {"route_id": plan["route_id"],
                "content_digest": (result.stdout or "").strip(),
                "hostname": plan["hostname"], "backend": plan["backend"]}

    def rollback(self, stage, prior):
        result = self._run("rollback", self.network_root, self.adapter_id,
                           stage["route_id"])
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def cleanup(self, route):
        applied = dict(route.last_applied or {})
        result = self._run("cleanup", self.network_root, self.adapter_id,
                           route.route_id, applied.get("content_digest", ""))
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}
