"""Bounded new-route and incumbent-baseline HTTP health checks."""

from __future__ import annotations


class IngressVerifier:
    def __init__(self, *, http, baseline_urls=None):
        self.http = http
        self.baseline_urls = baseline_urls or (lambda _plan: ())

    def baseline(self, plan):
        results = []
        for url in tuple(self.baseline_urls(plan)):
            results.append({"url": url, "ok": bool(self.http.probe(url, timeout=5))})
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
            url = f"{protocol}://{plan['hostname']}/"
            samples.append({"url": url, "ok": bool(self.http.probe(url, timeout=5))})
        return {"ok": all(item["ok"] for item in samples),
                "identity_ok": True, "samples": samples}
