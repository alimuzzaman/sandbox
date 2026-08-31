#!/usr/bin/env python3
"""Live ingress conformance run (037 T044).

Drives the composed clean-URL handshake (ingress capabilities -> resolver naming
-> route activation) against a REAL incumbent and records before/after route
health. Ingress and resolver qualification come only from checked-in source;
the evidence flag labels this live run and cannot widen either path.

Usage:
    python3 tests/live_ingress_acceptance.py --project-dir <dir> [--label L]
                                             [--evidence-id ID] [--consent]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _run(command: tuple[str, ...], timeout: int = 30) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {exc}>"
    return (result.stdout or result.stderr or "").strip()


def _probe(url: str) -> str:
    return _run(("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", "10", url), timeout=15)


def incumbent_state(baseline_urls: tuple[str, ...]) -> dict:
    """Everything adoption must preserve in the incumbent."""
    return {
        "service_active": _run(("systemctl", "is-active", "caddy")),
        "config_valid": _run(("sudo", "-n", "caddy", "validate", "--config",
                              "/etc/caddy/Caddyfile", "--adapter", "caddyfile"))[:400],
        "fragments": _run(("bash", "-lc", "ls -1 /etc/caddy/conf.d/ 2>/dev/null || true")),
        "baseline_routes": {url: _probe(url) for url in baseline_urls},
    }


def _result(value) -> dict:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--label", default="default")
    parser.add_argument("--evidence-id", default="live-ingress-acceptance")
    parser.add_argument("--consent", action="store_true")
    parser.add_argument("--baseline-url", action="append", default=[])
    args = parser.parse_args()

    from sandbox.application.context import clean_url_service, domain_service, ingress_service
    baseline = tuple(args.baseline_url)
    observed: dict[str, object] = {"evidence_id": args.evidence_id,
                                   "before": incumbent_state(baseline)}

    ingress = ingress_service(
        None, consent_decider=lambda _identity: bool(args.consent),
    )
    observed["advertised_from_source"] = {
        item["adapter_id"]: item["adoptable"]
        for item in ingress.support()["adapters"]
    }
    observed["detect"] = ingress.detect()
    selection = ingress.select(required_protocols=("http",))
    observed["selection"] = {
        "adapter_id": selection.adapter_id, "reason": selection.reason_code,
        "accepted_addresses": list(selection.accepted_addresses),
        "pin": selection.pin, "pin_source": selection.pin_source,
    }

    domains = domain_service(
        None, consent_decider=lambda _owner: bool(args.consent),
    )
    service = clean_url_service(None, ingress=ingress, domains=domains)

    if args.consent and selection.adapter_id:
        import sandbox_core as sc

        record = sc.registry_get(str(Path(args.project_dir).expanduser().resolve()),
                                 label=args.label) or {}
        backend = {"address": "127.0.0.1",
                   "port": record.get("wordpress_port") or record.get("http_port")}
        fallback = record.get("url") or ""
        observed["apply_first"] = service.apply(
            args.project_dir, label=args.label, backend=backend,
            protocols=("http",), interactive=True, fallback_url=fallback)
        hostname = (observed["apply_first"] or {}).get("hostname") \
            or _result(domains.status(args.project_dir, label=args.label)).get("hostname")
        observed["hostname"] = hostname
        if hostname:
            _run(("resolvectl", "flush-caches"))
            observed["fresh_lookup"] = _run(("getent", "hosts", str(hostname)))
            observed["http_through_incumbent"] = _probe(f"http://{hostname}/")
        observed["apply_second"] = service.apply(
            args.project_dir, label=args.label, backend=backend,
            protocols=("http",), interactive=True, fallback_url=fallback)
        observed["during"] = incumbent_state(baseline)
        owner = f"{Path(args.project_dir).expanduser().resolve()}::{args.label}"
        observed["ingress_cleanup_first"] = ingress.cleanup_owner(owner)
        observed["ingress_cleanup_second"] = ingress.cleanup_owner(owner)
        observed["domain_cleanup"] = _result(
            domains.cleanup(args.project_dir, label=args.label, interactive=True))

    observed["after"] = incumbent_state(baseline)
    observed["baseline_routes_unchanged"] = (
        observed["before"]["baseline_routes"] == observed["after"]["baseline_routes"]
    )
    observed["incumbent_still_active"] = observed["after"]["service_active"] == "active"
    print(json.dumps(observed, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
