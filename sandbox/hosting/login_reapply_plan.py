"""Which hosted stacks' sandbox-login a runtime migrate leaves stale (read-only).

The owner panel gate signs in only when the running sandbox-login container's
command is exactly the one this checkout renders (owner_panel_gate.bind:
``owner_share_upgrade_required``). A migrate that changes
``login_service/runtime.py`` therefore needs every stack with that service
reapplied. ``remote service migrate --plan`` lists them by comparing the
SHA-256 of each running container's runtime argument with this checkout's.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex

# Runs on the remote: docker reads only; prints one JSON line.
_PROGRAM = r'''
import hashlib, json, subprocess
def run(*argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, check=True).stdout
ids = run("docker", "ps", "-q", "--filter", "label=com.docker.compose.service=sandbox-login").split()
rows = []
for item in json.loads(run("docker", "inspect", *ids)) if ids else []:
    labels = (item.get("Config") or {}).get("Labels") or {}
    command = (item.get("Config") or {}).get("Cmd") or []
    runtime = command[3] if len(command) == 4 and command[:2] == ["python3", "-c"] else None
    rows.append({"project": labels.get("com.docker.compose.project"),
                 "runtime_digest": hashlib.sha256(runtime.encode()).hexdigest() if runtime else None})
print(json.dumps(rows))
'''


def local_runtime_digest() -> str:
    from sandbox.hosting import login_service
    return hashlib.sha256(login_service._runtime_source().encode()).hexdigest()


def plan(remote: dict, ssh_run, *, timeout: int = 60) -> dict:
    """``{"state": "complete", "needs_reapply": [...], "current": [...]}`` or unavailable.

    Never raises: the migrate plan stays usable when Docker cannot be read.
    """
    try:
        result = ssh_run(remote, shlex.join(["python3", "-c", _PROGRAM]), timeout=timeout)
        output = result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout
        if result.returncode != 0 or not isinstance(output, str) or len(output) > 65536:
            raise ValueError
        rows = json.loads(output)
        if not isinstance(rows, list):
            raise ValueError
        expected = local_runtime_digest()
        stale, current = set(), set()
        for row in rows:
            project = row.get("project") if isinstance(row, dict) else None
            if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", project):
                raise ValueError
            (current if row.get("runtime_digest") == expected else stale).add(project)
    except Exception:  # noqa: BLE001 - evidence only
        return {"state": "unavailable",
                "note": "could not read running sandbox-login containers; reapply stacks "
                        "that use login_service if owner-panel sign-in is refused"}
    return {"state": "complete", "needs_reapply": sorted(stale), "current": sorted(current - stale),
            "note": ("after this migrate, owner-panel sign-in and sharing refuse "
                     "owner_share_upgrade_required on these stacks until each is reapplied"
                     if stale else "no running sandbox-login needs a reapply")}
