"""Coordinate runtime backend, ingress naming offer, DNS, and route activation."""

from __future__ import annotations
from pathlib import Path


class CleanUrlService:
    def __init__(self, *, ingress, domains):
        self.ingress = ingress
        self.domains = domains

    def apply(self, project_dir, *, label="default", backend,
              protocols=("http",), capabilities=(), interactive=False,
              fallback_url):
        selection = self.ingress.select(
            required_protocols=protocols, required_capabilities=capabilities,
        )
        if selection.adapter_id is None:
            return {"ok": False, "state": "fallback", "mutated": False,
                    "fallback_url": fallback_url,
                    "reason": {"code": selection.reason_code}}
        offer = self.ingress.naming_offer(selection, fallback_url=fallback_url)
        authorization = self.ingress.authorize(
            selection, interactive=interactive, fallback_url=fallback_url,
        )
        if not authorization["ok"]:
            return authorization
        naming = self.domains.apply(
            project_dir, label=label, interactive=interactive, offer_override=offer,
        )
        if not naming.ok:
            return {"ok": False, "state": naming.state, "mutated": naming.mutated,
                    "fallback_url": naming.fallback_url, "reason": dict(naming.reason)}
        accepted = set(selection.accepted_addresses)
        if not set(naming.actual_answers).intersection(accepted):
            if naming.mutated:
                self.domains.cleanup(project_dir, label=label, interactive=False)
            return {"ok": False, "state": "fallback", "mutated": naming.mutated,
                    "fallback_url": fallback_url,
                    "reason": {"code": "naming_address_mismatch"}}
        listen = {"address": selection.accepted_addresses[0],
                  "port": 443 if "https" in protocols else 80}
        planned = self.ingress.plan_route(selection, {
            "hostname": naming.hostname,
            "owner": f"{Path(project_dir).expanduser().resolve()}::{label}",
            "wildcard": "wildcard" in capabilities, "listen": listen,
        }, backend)
        result = self.ingress.apply_route(
            planned, interactive=interactive, fallback_url=fallback_url,
        )
        if not result.get("ok") and naming.mutated:
            cleanup = self.domains.cleanup(project_dir, label=label, interactive=False)
            result["naming_cleanup"] = cleanup.to_dict()
        return result
