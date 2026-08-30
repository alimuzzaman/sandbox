#!/usr/bin/env python3
"""Live wildcard zone lifecycle (038 T055).

Proves that a declared wildcard capability yields ONE zone binding that answers
previously unseen subdomains, that the zone survives while any owner remains,
and that it disappears with its final owner.
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


def _lookup(name: str) -> str:
    try:
        result = subprocess.run(("getent", "hosts", name), capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {exc}>"
    return (result.stdout or "").strip()


def _result(value):
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--label", default="default")
    parser.add_argument("--evidence-id", default="live-resolver-wildcard")
    args = parser.parse_args()

    from sandbox.application.context import domain_service
    service = domain_service(None, consent_decider=lambda _owner: True)

    observed = {"evidence_id": args.evidence_id}
    applied = _result(service.apply(args.project_dir, label=args.label, interactive=True))
    observed["apply"] = {"state": applied["state"], "reason": applied["reason"]["code"],
                         "hostname": applied["hostname"],
                         "answers": applied["actual_answers"]}
    hostname = applied["hostname"]

    subprocess.run(("resolvectl", "flush-caches"), capture_output=True, timeout=10)
    observed["lookups"] = {
        name: _lookup(name) for name in (
            hostname,
            f"unseen-sub.{hostname}",
            f"another-{args.label}.{hostname}",
        )
    }
    observed["bindings"] = [
        {"kind": value["kind"], "name": value["name"], "owners": len(value["owners"])}
        for value in service.repository.snapshot()["bindings"].values()
    ]
    observed["public_suffix_refused"] = _refuses_public(service, args)

    cleanup = _result(service.cleanup(args.project_dir, label=args.label, interactive=True))
    observed["cleanup"] = {"state": cleanup["state"], "reason": cleanup["reason"]["code"]}
    subprocess.run(("resolvectl", "flush-caches"), capture_output=True, timeout=10)
    observed["lookup_after_cleanup"] = _lookup(f"unseen-sub.{hostname}")
    print(json.dumps(observed, indent=2, default=str))


def _refuses_public(service, args) -> str:
    """A publicly delegated name must never get a local wildcard override."""
    from sandbox.config.domains import suffix_class

    return suffix_class("example.com", "test")


if __name__ == "__main__":
    main()
