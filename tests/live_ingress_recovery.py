#!/usr/bin/env python3
"""Live cleanup recovery cases for an adopted route (037 T052).

Exercises the two cases that need a route Sandbox actually owns:

1. external drift  — the owned fragment is edited outside Sandbox; cleanup must
   leave it untouched and retain a retryable, non-secret recovery record;
2. incumbent down  — the incumbent is stopped mid-cleanup; cleanup must report
   incomplete rather than claiming success.

The incumbent is restarted immediately after the second case, and the run
verifies its pre-existing routes before and after.
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


def _run(command, timeout=30):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {exc}>"
    return (result.stdout or result.stderr or "").strip()


def _probe(url):
    return _run(("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                 "--max-time", "10", url), timeout=15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--label", default="default")
    parser.add_argument("--baseline-url", default="http://localhost/")
    parser.add_argument("--evidence-id", default="live-ingress-recovery")
    args = parser.parse_args()

    from sandbox.application.context import clean_url_service, domain_service, ingress_service
    from sandbox.ingress.manifest import IngressProofAttestation
    from sandbox.ingress.models import RouteRecord
    import sandbox_core as sc

    owner_root = str(Path(args.project_dir).expanduser().resolve())
    owner = f"{owner_root}::{args.label}"
    record = sc.registry_get(owner_root, label=args.label) or {}
    backend = {"address": "127.0.0.1",
               "port": record.get("wordpress_port") or record.get("http_port")}

    ingress = ingress_service(
        None, proof_attestation=IngressProofAttestation("system-caddy", args.evidence_id),
        consent_decider=lambda _identity: True)
    domains = domain_service(None, consent_decider=lambda _owner: True)
    service = clean_url_service(None, ingress=ingress, domains=domains)

    observed = {"evidence_id": args.evidence_id,
                "baseline_before": _probe(args.baseline_url)}

    applied = service.apply(args.project_dir, label=args.label, backend=backend,
                            protocols=("http",), interactive=True,
                            fallback_url=record.get("url") or "")
    observed["apply"] = {"ok": applied.get("ok"), "state": applied.get("state")}
    if not applied.get("ok"):
        observed["apply"]["error"] = str(applied.get("error"))[-300:]
        print(json.dumps(observed, indent=2, default=str))
        return

    route = next(RouteRecord.from_dict(value)
                 for value in ingress.repository.snapshot()["routes"].values()
                 if value.get("owner") == owner)
    route_id = dict(route.last_applied or {}).get("route_id")
    fragment = f"/etc/caddy/conf.d/90-sandbox-{route_id}.caddy"

    # 1. external drift
    _run(("sudo", "-n", "sh", "-c",
          f"printf '# edited outside sandbox\\n' >> {fragment}"))
    observed["fragment_after_edit"] = _run(("sudo", "-n", "tail", "-1", fragment))
    drift = ingress.cleanup_owner(owner)
    observed["cleanup_with_drift"] = {
        "ok": drift.get("ok"), "state": drift.get("state"),
        "reason": (drift.get("reason") or {}).get("code"),
        "residual": (drift.get("cleanup") or {}).get("residual"),
    }
    observed["fragment_preserved"] = _run(("sudo", "-n", "tail", "-1", fragment))
    observed["recovery_records"] = [
        {"reason_code": value.get("reason_code"), "status": value.get("status")}
        for value in ingress.repository.snapshot()["recovery"].values()
    ]

    # restore the fragment so the owned content matches its receipt again
    _run(("sudo", "-n", "sh", "-c",
          f"sed -i '/# edited outside sandbox/d' {fragment}"))
    _run(("sudo", "-n", "systemctl", "reload", "caddy"))

    # 2. incumbent unavailable
    _run(("sudo", "-n", "systemctl", "stop", "caddy"))
    observed["incumbent_state_during"] = _run(("systemctl", "is-active", "caddy"))
    stopped = ingress.cleanup_owner(owner)
    observed["cleanup_with_incumbent_down"] = {
        "ok": stopped.get("ok"), "state": stopped.get("state"),
        "reason": (stopped.get("reason") or {}).get("code"),
        "residual": (stopped.get("cleanup") or {}).get("residual"),
    }
    _run(("sudo", "-n", "systemctl", "start", "caddy"))
    observed["incumbent_state_restored"] = _run(("systemctl", "is-active", "caddy"))
    observed["baseline_after_restart"] = _probe(args.baseline_url)

    # 3. normal cleanup now that the incumbent is back
    final = ingress.cleanup_owner(owner)
    observed["cleanup_normal"] = {
        "ok": final.get("ok"), "state": final.get("state"),
        "reason": (final.get("reason") or {}).get("code"),
    }
    observed["domain_cleanup"] = (
        domains.cleanup(args.project_dir, label=args.label, interactive=True).to_dict()
    )["reason"]["code"]
    observed["fragment_gone"] = not Path(fragment).exists()
    observed["baseline_after"] = _probe(args.baseline_url)
    print(json.dumps(observed, indent=2, default=str))


if __name__ == "__main__":
    main()
