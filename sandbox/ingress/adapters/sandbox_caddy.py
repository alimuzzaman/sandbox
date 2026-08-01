"""Transactional adapter for the aggregate Sandbox-owned Caddyfile."""

from __future__ import annotations

import hashlib
import re

from sandbox.config.domains import normalize_hostname


class SandboxCaddyAdapter:
    def __init__(self, *, read_current, validate, activate, block_renderer):
        self.read_current = read_current
        self.validate = validate
        self.activate_config = activate
        self.block_renderer = block_renderer

    @staticmethod
    def _markers(route_id):
        return (f"# sandbox-ingress-route begin {route_id}",
                f"# sandbox-ingress-route end {route_id}")

    @classmethod
    def _extract(cls, text, route_id):
        begin, end = cls._markers(route_id)
        pattern = re.compile(rf"(?ms)^{re.escape(begin)}\n.*?^{re.escape(end)}\n?")
        match = pattern.search(text)
        return match.group(0) if match else None

    @staticmethod
    def _foreign_hostname(text, hostname):
        inside_owned = False
        for line in text.splitlines():
            if line.startswith("# sandbox-ingress-route begin "):
                inside_owned = True
                continue
            if line.startswith("# sandbox-ingress-route end "):
                inside_owned = False
                continue
            if inside_owned or not line.rstrip().endswith("{"):
                continue
            addresses = line[:-1].strip().split(",")
            for address in addresses:
                host = address.strip().split("://", 1)[-1].split(":", 1)[0]
                if host in {hostname, f"*.{hostname}"}:
                    return True
        return False

    def plan_route(self, selection, naming, backend, prior=None):
        hostname = normalize_hostname(naming["hostname"])
        port = int(backend["port"])
        if not 1 <= port <= 65535:
            raise ValueError("Sandbox Caddy backend port is invalid")
        route_id = hashlib.sha256(
            f"sandbox-caddy\0{naming['owner']}\0{hostname}".encode(),
        ).hexdigest()
        current = self.read_current()
        owned = self._extract(current, route_id)
        if owned is None and self._foreign_hostname(current, hostname):
            raise ValueError("foreign hostname route collision")
        begin, end = self._markers(route_id)
        block = (f"{begin}\n" + self.block_renderer(
            hostname, port, bool(naming.get("wildcard")),
        ).rstrip() + f"\n{end}\n")
        candidate = current.replace(owned, block) if owned else current.rstrip() + "\n\n" + block
        return {"adapter_id": "sandbox-caddy", "route_id": route_id,
                "hostname": hostname, "backend": {"port": port},
                "wildcard": bool(naming.get("wildcard")), "prior": current,
                "candidate": candidate, "block": block,
                "block_digest": hashlib.sha256(block.encode()).hexdigest()}

    def validate_current(self, plan): return self.validate(self.read_current())
    def capture_prior(self, plan): return {"content": self.read_current()}
    def stage_candidate(self, plan): return dict(plan)
    def validate_candidate(self, stage): return self.validate(stage["candidate"])
    def activate(self, stage): return self.activate_config(stage["candidate"])

    def observe_route(self, plan):
        block = self._extract(self.read_current(), plan["route_id"])
        return {"route_id": plan["route_id"],
                "block_digest": hashlib.sha256(block.encode()).hexdigest() if block else None,
                "hostname": plan["hostname"], "backend": plan["backend"],
                "wildcard": plan["wildcard"]}

    def rollback(self, stage, prior):
        return self.activate_config(prior["content"])

    def cleanup(self, route):
        current = self.read_current()
        block = self._extract(current, route.route_id)
        expected = dict(route.last_applied or {}).get("block_digest")
        actual = hashlib.sha256(block.encode()).hexdigest() if block else None
        if actual != expected:
            return {"ok": False, "mutated": False,
                    "error": "Sandbox Caddy route drifted"}
        candidate = current.replace(block, "")
        result = self.activate_config(candidate)
        return {"ok": bool(result.get("ok")), "mutated": bool(result.get("ok")),
                "error": result.get("error", "")}
