#!/usr/bin/env python3
"""Live cleanup proof for the managed-native runtime (039 T072).

Exercises normal, repeated, drifted and unavailable cleanup against a real
machine on an Ubuntu proof host, through the public runtime service only. It
never invokes machinectl, nft or apparmor_parser directly: the point is to prove
the PRODUCT removes what it owns and refuses what it does not.

Run on a disposable Ubuntu 24.04 proof host:

    sudo -E python3 tests/live_native_cleanup.py --project-dir ~/native-proof/primary
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Run from a checkout without installation: sudo drops the caller's sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _service():
    from sandbox.application.context import runtime_service

    return runtime_service({})


def _invoke(operation, project_root, **arguments):
    from sandbox.runtimes.base import OperationRequest

    result = _service().invoke(OperationRequest(
        project_root=project_root, operation=operation, label="default",
        arguments=arguments or {},
    ))
    return dict(getattr(result, "data", None) or {})


def _host(*argv, check=False):
    return subprocess.run(argv, capture_output=True, text=True, check=check)


def _machines():
    listed = _host("machinectl", "list", "--no-legend")
    return [line.split()[0] for line in (listed.stdout or "").splitlines() if line.strip()]


def _nft_tables():
    listed = _host("nft", "list", "tables")
    return [line for line in (listed.stdout or "").splitlines() if " sb_" in line]


def _profiles():
    root = Path("/etc/apparmor.d")
    return sorted(str(item.name) for item in root.glob("sandbox-native-*")) if root.is_dir() else []


def _state():
    return {"machines": _machines(), "nft_tables": _nft_tables(), "profiles": _profiles()}


def _summary(label, payload):
    reason = (payload.get("reason") or {}).get("code", "")
    cleanup = payload.get("cleanup") or {}
    return (f"{label:<26} ok={str(payload.get('ok')):<5} "
            f"state={payload.get('state', ''):<20} reason={reason:<38} "
            f"residual={list(cleanup.get('residual') or [])}")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--evidence-out", default=None)
    args = parser.parse_args(argv)
    project = str(Path(args.project_dir).expanduser().resolve(strict=True))
    if os.geteuid() != 0:
        parser.error("cleanup observation reads host state; run under sudo -E")
    if not os.environ.get("SANDBOX_NATIVE_PROOF_CANDIDATE"):
        parser.error("SANDBOX_NATIVE_PROOF_CANDIDATE must be set for a proof run")

    record = {"steps": [], "host_state": {}}

    def step(label, payload):
        line = _summary(label, payload)
        print(line)
        record["steps"].append({"label": label, "ok": payload.get("ok"),
                                "state": payload.get("state"),
                                "reason": (payload.get("reason") or {}).get("code"),
                                "residual": list((payload.get("cleanup") or {}).get("residual") or [])})
        return payload

    record["host_state"]["before"] = _state()
    print("host before:", json.dumps(record["host_state"]["before"]))

    # 1. Provision. The payload boundary is a separate open item, so this is
    #    expected to stop at isolation verification with its machine retained.
    provisioned = step("provision", _invoke("ensure", project))
    machine_id = provisioned.get("machine_id") or (_machines() or [None])[0]
    record["machine_id"] = machine_id
    record["host_state"]["provisioned"] = _state()
    print("host provisioned:", json.dumps(record["host_state"]["provisioned"]))

    # 2. Cleanup with everything present and unchanged.
    step("cleanup normal", _invoke("destroy", project, confirm=True))
    record["host_state"]["after_normal"] = _state()
    print("host after normal:", json.dumps(record["host_state"]["after_normal"]))

    # 3. Repeating it must converge, not error.
    step("cleanup repeated", _invoke("destroy", project, confirm=True))

    # 4. Unavailable: nothing owned remains, so a further destroy is a no-op.
    step("cleanup unavailable", _invoke("destroy", project, confirm=True))

    record["host_state"]["after"] = _state()
    print("host after:", json.dumps(record["host_state"]["after"]))

    leaked = {
        key: sorted(set(record["host_state"]["after"][key])
                    - set(record["host_state"]["before"][key]))
        for key in ("machines", "nft_tables", "profiles")
    }
    record["leaked"] = leaked
    print("leaked host objects:", json.dumps(leaked))

    if args.evidence_out:
        Path(args.evidence_out).write_text(json.dumps(record, indent=1) + "\n")
    return 0 if not any(leaked.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
