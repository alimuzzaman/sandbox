#!/usr/bin/env python3
"""Live resolver conformance run (038 T034).

Drives the composed domain service against a REAL host resolver and records
before/after state. The typed proof attestation is constructed here, in the
harness, and only for this invocation: no CLI flag, environment variable, or
configuration value can promote an adapter, and running this script does not
change what `./sb domains support` advertises to anyone else.

Usage:
    python3 tests/live_resolver_acceptance.py --project-dir <dir> [--label L]
                                              [--evidence-id ID] [--consent]

`--consent` records that a human operator approved the first mutation of this
machine's resolver; without it the run stops at plan.
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

SAMPLE_NAMES = ("example.com", "github.com")


def _run(command: tuple[str, ...], timeout: int = 20) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {exc}>"
    return (result.stdout or result.stderr or "").strip()


def host_state() -> dict:
    """Everything adoption must leave alone, plus the resolver's own shape."""
    resolv = Path("/etc/resolv.conf")
    try:
        target = str(resolv.readlink()) if resolv.is_symlink() else "<regular file>"
    except OSError as exc:
        target = f"<unreadable: {exc}>"
    return {
        "resolv_conf": {"symlink_target": target,
                        "first_lines": _run(("head", "-5", "/etc/resolv.conf"))},
        "resolvectl_status": _run(("resolvectl", "status"))[:2000],
        "dns_listeners": _run(("bash", "-lc", "ss -lntu 2>/dev/null | grep ':53 ' || true")),
        "unrelated_answers": {name: _run(("getent", "hosts", name))
                              for name in SAMPLE_NAMES},
    }


def _result(value) -> dict:
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--label", default="default")
    parser.add_argument("--evidence-id", default="live-resolver-acceptance")
    parser.add_argument("--consent", action="store_true")
    args = parser.parse_args()

    from sandbox.application.context import domain_service
    from sandbox.network.manifest import ResolverProofAttestation

    attestation = ResolverProofAttestation("systemd-resolved", args.evidence_id)
    observed: dict[str, object] = {"evidence_id": args.evidence_id,
                                   "before": host_state()}

    unproven = domain_service(None)
    observed["advertised_without_attestation"] = {
        item["adapter_id"]: item["adoptable"]
        for item in unproven.support()["adapters"]
    }

    service = domain_service(
        None, proof_attestation=attestation,
        consent_decider=lambda _owner: bool(args.consent),
    )
    observed["advertised_with_attestation"] = {
        item["adapter_id"]: item["adoptable"]
        for item in service.support()["adapters"]
    }

    project, label = args.project_dir, args.label
    observed["status_before"] = _result(service.status(project, label=label))
    observed["plan"] = _result(service.plan(project, label=label))

    if args.consent:
        observed["apply_first"] = _result(
            service.apply(project, label=label, interactive=True))
        observed["status_after_apply"] = _result(service.status(project, label=label))
        hostname = observed["status_after_apply"].get("hostname")
        if hostname:
            _run(("resolvectl", "flush-caches"))
            observed["fresh_lookup"] = _run(("getent", "hosts", str(hostname)))
            observed["http_probe"] = _run((
                "curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                "--max-time", "10", f"http://{hostname}/"))
        observed["apply_second"] = _result(
            service.apply(project, label=label, interactive=True))
        observed["cleanup_first"] = _result(
            service.cleanup(project, label=label, interactive=True))
        observed["cleanup_second"] = _result(
            service.cleanup(project, label=label, interactive=True))

    observed["after"] = host_state()
    def _answer_set(answers: dict) -> dict:
        # Round-robin records come back in a different order on every query, so
        # compare the SET of answers; an order change is not a resolution change.
        return {name: frozenset(value.split("\n")) for name, value in answers.items()}

    observed["unrelated_answers_unchanged"] = (
        _answer_set(observed["before"]["unrelated_answers"])
        == _answer_set(observed["after"]["unrelated_answers"])
    )
    observed["resolv_conf_unchanged"] = (
        observed["before"]["resolv_conf"] == observed["after"]["resolv_conf"]
    )
    print(json.dumps(observed, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
