"""Documented Herd/Valet proxy lifecycle; runtime creation remains runtime-owned."""

from __future__ import annotations

import hashlib
import ipaddress

from sandbox.config.domains import normalize_hostname


class HerdValetAdapter:
    def __init__(self, *, product, executable, process):
        if product not in {"herd", "valet"}:
            raise ValueError("unsupported Herd/Valet product")
        self.product = product
        self.executable = executable
        self.process = process

    @staticmethod
    def _site(hostname):
        hostname = normalize_hostname(hostname)
        if not hostname.endswith(".test"):
            raise ValueError("Herd/Valet proxy adoption is limited to .test identities")
        return hostname.removesuffix(".test")

    def plan_route(self, selection, naming, backend, prior=None):
        hostname = normalize_hostname(naming["hostname"])
        site = self._site(hostname)
        address = str(ipaddress.ip_address(backend["address"]))
        if not ipaddress.ip_address(address).is_loopback:
            raise ValueError("Herd/Valet backend must be loopback")
        port = int(backend["port"])
        route_id = hashlib.sha256(
            f"{self.product}\0{naming['owner']}\0{hostname}".encode(),
        ).hexdigest()
        return {"adapter_id": "herd-valet", "product": self.product,
                "route_id": route_id, "hostname": hostname, "site": site,
                "backend": f"http://{address}:{port}",
                "protocols": tuple(sorted(selection.get("protocols") or ("http",))),
                "secure": "https" in set(selection.get("protocols") or ()),
                "prior": prior}

    def _run(self, *args, timeout=20):
        return self.process.run((self.executable, *args), timeout=timeout)

    def validate_current(self, plan):
        result = self._run("proxies", timeout=10)
        return {"ok": result.returncode == 0, "snapshot": (result.stdout or "")[:5000]}

    def capture_prior(self, plan):
        current = self.validate_current(plan)
        line = next((line.strip() for line in current.get("snapshot", "").splitlines()
                     if plan["site"] in line or plan["hostname"] in line), None)
        return {"line": line}

    def stage_candidate(self, plan):
        return dict(plan)

    def validate_candidate(self, stage):
        return {"ok": True}

    def activate(self, stage):
        result = self._run("proxy", stage["site"], stage["backend"])
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or "proxy failed")[:1000]}
        if stage["secure"]:
            secured = self._run("secure", stage["site"])
            if secured.returncode != 0:
                self._run("unproxy", stage["site"])
                return {"ok": False, "error": (secured.stderr or "secure failed")[:1000]}
        return {"ok": True}

    def observe_route(self, plan):
        result = self._run("proxies", timeout=10)
        present = result.returncode == 0 and (
            plan["site"] in (result.stdout or "") or plan["hostname"] in (result.stdout or "")
        ) and plan["backend"] in (result.stdout or "")
        return {"route_id": plan["route_id"], "hostname": plan["hostname"],
                "backend": plan["backend"], "secure": plan["secure"],
                "present": present}

    def rollback(self, stage, prior):
        if stage.get("secure"):
            self._run("unsecure", stage["site"])
        result = self._run("unproxy", stage["site"])
        # Existing foreign/prior proxies are rejected during service planning;
        # this adapter never guesses a backend from display prose.
        if prior and prior.get("line"):
            return {"ok": False, "error": "prior proxy requires conservative recovery"}
        return {"ok": result.returncode == 0}

    def cleanup(self, route):
        desired = dict(route.desired)
        observed = self.observe_route(desired)
        applied = dict(route.last_applied or {})
        if observed != applied:
            return {"ok": False, "mutated": False, "error": "Herd/Valet route drifted"}
        if desired.get("secure"):
            self._run("unsecure", desired["site"])
        result = self._run("unproxy", desired["site"])
        return {"ok": result.returncode == 0, "mutated": result.returncode == 0,
                "error": (result.stderr or "")[:1000]}
