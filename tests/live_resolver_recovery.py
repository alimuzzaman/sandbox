#!/usr/bin/env python3
"""Live resolver cleanup recovery cases (038 T050).

Exercises the cases that need a binding Sandbox actually owns:

1. external drift  — the owned resolver fragment is edited outside Sandbox;
   cleanup must leave it untouched and retain a retryable recovery record;
2. authority down  — the scoped answering authority is killed; cleanup must
   still converge or report incomplete truthfully;
3. normal cleanup  — once restored, cleanup completes and repeats safely.

Unrelated resolution is sampled before and after.
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

FRAGMENT = "/etc/systemd/resolved.conf.d/80-sandbox-{suffix}.conf"


def _run(command, timeout=30):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unavailable: {exc}>"
    return (result.stdout or result.stderr or "").strip()


def _result(value):
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--label", default="default")
    parser.add_argument("--suffix", default="test")
    parser.add_argument("--evidence-id", default="live-resolver-recovery")
    args = parser.parse_args()

    from sandbox.application.context import domain_service
    service = domain_service(None, consent_decider=lambda _owner: True)

    fragment = FRAGMENT.format(suffix=args.suffix)
    observed = {"evidence_id": args.evidence_id,
                "unrelated_before": _run(("getent", "hosts", "example.com"))}

    applied = _result(service.apply(args.project_dir, label=args.label, interactive=True))
    observed["apply"] = {"state": applied["state"], "reason": applied["reason"]["code"]}
    if applied["state"] != "ready":
        print(json.dumps(observed, indent=2, default=str))
        return

    # 1. external drift
    _run(("sudo", "-n", "sh", "-c", f"printf '# edited outside sandbox\\n' >> {fragment}"))
    observed["fragment_after_edit"] = _run(("sudo", "-n", "tail", "-1", fragment))
    drift = _result(service.cleanup(args.project_dir, label=args.label, interactive=True))
    observed["cleanup_with_drift"] = {"state": drift["state"],
                                      "reason": drift["reason"]["code"],
                                      "ownership": drift["ownership"]}
    observed["fragment_preserved"] = _run(("sudo", "-n", "tail", "-1", fragment))
    observed["recovery_records"] = [
        {"reason_code": value.get("reason_code"), "status": value.get("status")}
        for value in service.repository.snapshot()["recovery"].values()
    ]

    # restore the fragment so it matches its receipt again
    _run(("sudo", "-n", "sh", "-c", f"sed -i '/# edited outside sandbox/d' {fragment}"))
    _run(("sudo", "-n", "systemctl", "reload-or-restart", "systemd-resolved"))

    # 2. authority stopped
    _run(("pkill", "-f", "network/authority/dnsmasq.conf"))
    observed["authority_running_during"] = bool(
        _run(("pgrep", "-f", "network/authority/dnsmasq.conf")))
    stopped = _result(service.cleanup(args.project_dir, label=args.label, interactive=True))
    observed["cleanup_with_authority_down"] = {"state": stopped["state"],
                                               "reason": stopped["reason"]["code"]}

    # 3. normal + repeat
    final = _result(service.cleanup(args.project_dir, label=args.label, interactive=True))
    again = _result(service.cleanup(args.project_dir, label=args.label, interactive=True))
    observed["cleanup_normal"] = {"state": final["state"], "reason": final["reason"]["code"]}
    observed["cleanup_repeat"] = {"state": again["state"], "reason": again["reason"]["code"]}
    observed["fragment_gone"] = not Path(fragment).exists()
    observed["routing_domains_after"] = _run(("resolvectl", "domain"))[:120]
    observed["unrelated_after"] = _run(("getent", "hosts", "example.com"))
    observed["unrelated_unchanged"] = (
        set(observed["unrelated_before"].split("\n"))
        == set(observed["unrelated_after"].split("\n"))
    )
    print(json.dumps(observed, indent=2, default=str))


if __name__ == "__main__":
    main()
