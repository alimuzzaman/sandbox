"""The six readiness rows, each built from an existing probe (research R6).

A row is ``{"aspect", "state"}`` plus ``reason`` (not_ready/not_applicable),
``probe_state`` (unknown) and ``remedy`` (not_ready/unknown). States are
``ready``, ``not_ready``, ``unknown`` and ``not_applicable``; only
``not_ready`` ever refuses a submission. Remedies are CLI commands.
"""
from __future__ import annotations

import re
import subprocess
from typing import Callable

ASPECTS = ("registration", "reachability", "runtime_compatibility", "capacity",
           "ownership_repair", "handoff")
STATES = ("ready", "not_ready", "unknown", "not_applicable")
PROBE_STATES = ("timeout", "partial", "unavailable", "unrecorded")
REACHABILITY_TIMEOUT = 10
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def row(aspect: str, state: str, *, reason: str | None = None,
        probe_state: str | None = None, remedy: str | None = None) -> dict:
    if aspect not in ASPECTS or state not in STATES:
        raise ValueError("unknown readiness aspect or state")
    if probe_state is not None and probe_state not in PROBE_STATES:
        raise ValueError("unknown readiness probe state")
    value = {"aspect": aspect, "state": state}
    if reason is not None:
        value["reason"] = reason if _SAFE_CODE.fullmatch(reason) else "unexpected"
    if probe_state is not None:
        value["probe_state"] = probe_state
    if remedy is not None:
        value["remedy"] = remedy
    return value


def registration(error_code: str | None, name: str | None) -> dict:
    """``error_code`` is the target resolution refusal, or None when resolved."""
    if error_code is None:
        return row("registration", "ready")
    remedies = {
        "remote_not_provisioned": f"./sb remote provision {name}" if name else "./sb remote list",
    }
    return row("registration", "not_ready", reason=error_code,
               remedy=remedies.get(error_code, "./sb remote list"))


def reachability(ssh_run: Callable, remote: dict, name: str) -> dict:
    try:
        result = ssh_run(remote, "true", timeout=REACHABILITY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return row("reachability", "unknown", probe_state="timeout",
                   remedy=f"./sb remote list")
    except Exception:
        return row("reachability", "unknown", probe_state="unavailable",
                   remedy="./sb remote list")
    if getattr(result, "returncode", 1) == 0:
        return row("reachability", "ready")
    # ssh exits 255 for refused connections and failed authentication.
    return row("reachability", "not_ready",
               reason="ssh_refused" if getattr(result, "returncode", 1) == 255 else "ssh_failed",
               remedy="./sb remote list")


def runtime_compatibility(service_status: Callable, remote: dict,
                          name: str) -> tuple[dict, str | None]:
    """The 061 verdict for the installed runtime, plus its revision."""
    from sandbox.remote_runtime.verdict import admitted
    try:
        status = service_status(remote)
    except Exception:
        status = None
    if not isinstance(status, dict):
        return row("runtime_compatibility", "unknown", probe_state="unavailable",
                   remedy=f"./sb remote service status {name}"), None
    revision = status.get("installed_runtime_revision")
    revision = revision if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{24}", revision) \
        else None
    try:
        ok, state = admitted(status)
    except Exception:
        return row("runtime_compatibility", "unknown", probe_state="unavailable",
                   remedy=f"./sb remote service status {name}"), revision
    if ok:
        return row("runtime_compatibility", "ready"), revision
    if state in (None, "unavailable", "unknown"):
        return row("runtime_compatibility", "unknown", probe_state="unavailable",
                   remedy=f"./sb remote service status {name}"), revision
    return row("runtime_compatibility", "not_ready", reason=str(state),
               remedy=f"./sb remote service migrate {name} --confirm"), revision


def capacity(capacity_decision: Callable, remote: dict, name: str) -> dict:
    """Read-only: the admission decision without allocating (runtime allocates)."""
    try:
        decision = capacity_decision(remote, remote_name=name)
    except Exception:
        decision = None
    if not isinstance(decision, dict):
        return row("capacity", "unknown", probe_state="unavailable",
                   remedy=f"./sb remote network-range list {name}")
    if decision.get("ok") is True:
        return row("capacity", "ready")
    evidence = decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    if evidence.get("reason") == "missing_pool_evidence":
        return row("capacity", "not_ready", reason="missing_pool_evidence",
                   remedy=f"./sb remote network-range propose {name}")
    if decision.get("code") == "docker_network_subnet_exhausted":
        return row("capacity", "not_ready", reason="subnet_exhausted",
                   remedy=f"./sb workspace reap --remote {name} --confirm")
    state = evidence.get("status")
    return row("capacity", "unknown",
               probe_state="partial" if state == "partial" else "unavailable",
               remedy=f"./sb remote network-range list {name}")


_NO_INSTANCE = 3


def ownership_repair(ssh_run: Callable, remote: dict, name: str, sb_path: str,
                     *, workspace: str | None = None) -> dict:
    """The repair runs through the remote ``sb``; it must be installed and
    executable. ``not_applicable`` when the project has no workspace there."""
    import shlex
    command = f"test -x {shlex.quote(sb_path)}"
    if workspace:
        command = f"test -d {shlex.quote(workspace)} || exit {_NO_INSTANCE}; {command}"
    try:
        result = ssh_run(remote, command, timeout=REACHABILITY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return row("ownership_repair", "unknown", probe_state="timeout",
                   remedy=f"./sb remote service status {name}")
    except Exception:
        return row("ownership_repair", "unknown", probe_state="unavailable",
                   remedy=f"./sb remote service status {name}")
    if getattr(result, "returncode", 1) == 0:
        return row("ownership_repair", "ready")
    if workspace and getattr(result, "returncode", 1) == _NO_INSTANCE:
        return not_applicable("ownership_repair", "no_instance")
    if getattr(result, "returncode", 1) == 255:
        return row("ownership_repair", "unknown", probe_state="unavailable",
                   remedy="./sb remote list")
    return row("ownership_repair", "not_ready", reason="repair_helper_missing",
               remedy=f"./sb remote service migrate {name} --confirm")


def handoff(record: dict | None, revision: str | None, name: str, project_dir: str) -> dict:
    """A recorded ``ensure --remote`` then ``exec --remote`` at this revision."""
    import shlex
    if isinstance(record, dict) and revision is not None \
            and record.get("installed_runtime_revision") == revision:
        return row("handoff", "ready")
    return row("handoff", "unknown", probe_state="unrecorded",
               remedy=f"./sb ensure --remote {name} --project-dir {shlex.quote(project_dir)}")


def not_applicable(aspect: str, reason: str) -> dict:
    return row(aspect, "not_applicable", reason=reason)
