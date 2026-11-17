"""Recognise a durable job whose supervisor launch was lost.

An accepted job holds a slot until its supervisor records its identity and
moves it to running, which takes seconds. A launch can die with its launcher:
the control service stopped while ``systemd-run --scope`` was still in its
cgroup (an install, sandbox.remote_runtime.pins.INSTALL_GATE), or a crash.
``JobService.reconcile_startup`` fails such a job so it does not stay
accepted, holding a slot, until the queue expires a day later.

Kept outside sandbox/application/job_service.py, whose payload shapes are
protocol-fingerprinted (sandbox/remote_runtime/shapes.py): this reads only
local registry rows and writes only a local result record.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def launch_lost(row: dict, snapshot, grace_seconds: int, *, now: datetime | None = None) -> bool:
    """Whether ``row`` is an accepted local job with no supervisor after ``grace_seconds``.

    ``snapshot(job_id)`` is the repository's snapshot reader.
    """
    if row.get("lifecycle") != "accepted" or row.get("target_kind") != "local":
        return False
    try:
        since = datetime.fromisoformat(str(row.get("updated_at", "")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    now = now or datetime.now(timezone.utc)
    if since.tzinfo is None or now - since <= timedelta(seconds=grace_seconds):
        return False
    if _waiting_on_own_input(row, now):
        return False
    process = (snapshot(row["job_id"]) or {}).get("process") or {}
    return not process.get("supervisor_pid")


def _waiting_on_own_input(row: dict, now: datetime) -> bool:
    """A server exec holding its own stdin may wait for a slot until its deadline.

    Its admitting process keeps the row accepted, with no supervisor, while it
    polls for a slot (JobService._await_input_slot), and fails the row itself on
    timeout or signal. Until ``server_cli.absolute_deadline_at`` has passed the
    row is a live wait, not a lost launch; an unreadable deadline counts as live.
    """
    try:
        provenance = json.loads(row.get("execution_policy_provenance_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(provenance, dict) or "server_cli.stdin_sha256" not in provenance:
        return False
    try:
        deadline = datetime.fromisoformat(
            str(provenance.get("server_cli.absolute_deadline_at", "")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    return deadline.tzinfo is None or now <= deadline


def lost_launch_result() -> str:
    """The result record of a job failed for a lost launch."""
    return json.dumps({"reconciled": True,
                       "evidence": "accepted with no supervisor within the launch grace"}, sort_keys=True)
