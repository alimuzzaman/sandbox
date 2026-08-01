"""Shared fixed-helper boundary for attributable incumbent fragments."""

from __future__ import annotations

import hashlib
import ipaddress
import os
from pathlib import Path
import tempfile

from sandbox.config.domains import normalize_hostname


class FileFragmentAdapter:
    adapter_id = ""
    extension = "conf"

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
        route_id = hashlib.sha256(
            f"{self.adapter_id}\0{naming['owner']}\0{hostname}".encode(),
        ).hexdigest()
        listen = dict(selection["listen"])
        content = self.render(route_id, hostname, address, port, listen,
                              bool(naming.get("wildcard")))
        candidate = self.staging_root / self.adapter_id / f"{route_id}.{self.extension}"
        return {"adapter_id": self.adapter_id, "route_id": route_id,
                "hostname": hostname, "backend": {"address": address, "port": port},
                "protocols": tuple(sorted(selection.get("protocols") or ("http",))),
                "listen": listen, "candidate": str(candidate), "content": content,
                "content_digest": hashlib.sha256(content.encode()).hexdigest(),
                "prior": prior}

    def _run(self, verb, *arguments, timeout=30):
        return self.process.run(("sudo", "-n", self.helper, verb, *map(str, arguments)),
                                timeout=timeout)

    @staticmethod
    def _atomic_write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def validate_current(self, plan):
        result = self._run("validate-current", self.adapter_id, timeout=10)
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def capture_prior(self, plan):
        return {"route_id": plan["route_id"]}

    def stage_candidate(self, plan):
        candidate = Path(plan["candidate"])
        self._atomic_write(candidate, plan["content"])
        return {**plan, "candidate": str(candidate)}

    def validate_candidate(self, stage):
        result = self._run("prepare", self.network_root, stage["candidate"],
                           self.adapter_id, stage["route_id"])
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def activate(self, stage):
        result = self._run("activate", self.network_root, self.adapter_id,
                           stage["route_id"])
        return {"ok": result.returncode == 0, "error": (result.stderr or "")[:1000]}

    def observe_route(self, plan):
        result = self._run("observe", self.adapter_id, plan["route_id"], timeout=10)
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
