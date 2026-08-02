"""Bounded new-route and incumbent-baseline HTTP health checks."""

from __future__ import annotations

import ipaddress


def _probe_address(address):
    """Concrete address to connect to for a possibly-wildcard listen scope."""
    try:
        parsed = ipaddress.ip_address(str(address))
    except ValueError:
        return address
    if not parsed.is_unspecified:
        return str(parsed)
    return "::1" if parsed.version == 6 else "127.0.0.1"


class IngressVerifier:
    def __init__(self, *, http, baseline_urls=None):
        self.http = http
        self.baseline_urls = baseline_urls or (lambda _plan: ())

    def baseline(self, plan):
        results = []
        for target in tuple(self.baseline_urls(plan)):
            if not isinstance(target, dict):
                results.append({"target": target, "ok": False})
                continue
            safe = {"address": target.get("address"), "port": target.get("port"),
                    "host": target.get("host", "localhost")}
            probe = getattr(self.http, "probe_route", None)
            results.append({"target": safe, "ok": bool(
                probe(safe["address"], safe["port"], safe["host"], timeout=5)
                if probe else False
            )})
        if plan.get("_baseline_required") and not results:
            return {"ok": False, "samples": [],
                    "reason": "baseline_samples_unavailable"}
        return {"ok": all(item["ok"] for item in results), "samples": results}

    def route(self, plan, observed):
        if observed is None or observed.get("present") is False:
            return {"ok": False, "identity_ok": False, "samples": []}
        identity_keys = (
            "route_id", "hostname", "backend", "content_digest", "block_digest",
            "secure", "wildcard",
        )
        mismatches = [
            key for key in identity_keys
            if key in plan and observed.get(key) != plan.get(key)
        ]
        if mismatches:
            return {"ok": False, "identity_ok": False,
                    "mismatches": mismatches, "samples": []}
        protocols = tuple(plan.get("protocols") or ("http",))
        samples = []
        for protocol in protocols:
            if protocol != "http":
                samples.append({"protocol": protocol, "ok": False})
                continue
            listen = dict(plan.get("listen") or {})
            probe = getattr(self.http, "probe_route", None)
            # A wildcard bind is a listen scope, not a destination: you cannot
            # connect to 0.0.0.0/::. Probe the loopback address that socket
            # serves, which is also the only address the rendered site accepts.
            address = _probe_address(listen.get("address"))
            sample = {"protocol": protocol, "address": address,
                      "port": listen.get("port"), "host": plan["hostname"]}
            samples.append({**sample, "ok": bool(
                probe(sample["address"], sample["port"], sample["host"], timeout=5)
                if probe else False
            )})
        return {"ok": all(item["ok"] for item in samples),
                "identity_ok": True, "samples": samples}
