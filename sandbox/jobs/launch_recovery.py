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


def launch_lost(row: dict, snapshot, grace_seconds: int, *, now: datetime | None = None,
                holds_slot=lambda _job_id: True) -> bool:
    """Whether ``row`` is an accepted local job with no supervisor after ``grace_seconds``.

    ``snapshot(job_id)`` is the repository's snapshot reader; ``holds_slot(job_id)``
    says whether the job already holds a host capacity slot (see ``slot_held``).
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
    if not holds_slot(row["job_id"]) and _waiting_on_own_input(row, now):
        return False
    process = (snapshot(row["job_id"]) or {}).get("process") or {}
    return not process.get("supervisor_pid")


def _waiting_on_own_input(row: dict, now: datetime) -> bool:
    """A server exec holding its own stdin may wait for a slot until its deadline.

    Its admitting process keeps the row accepted, with no supervisor and no
    slot, while it polls for one (JobService._await_input_slot), and fails the
    row itself on timeout or signal. Until ``server_cli.absolute_deadline_at``
    has passed such a row is a live wait, not a lost launch. Callers check that
    it holds no slot: a row that got its slot and then lost its launch falls
    back to the grace rule. A missing or unreadable deadline is not a wait.
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
        return False
    return deadline.tzinfo is not None and now <= deadline


def slot_held(repository, job_id: str) -> bool:
    """Whether ``job_id`` was admitted: it holds a host slot, or runs on its
    parent's (a workspace lease with no slot of its own, sandbox.jobs.scheduler)."""
    connection = repository.connection
    return (connection.execute(
        "SELECT 1 FROM host_capacity_leases WHERE job_id=?", (job_id,)).fetchone() is not None
        or connection.execute(
            "SELECT 1 FROM workspace_leases WHERE job_id=?", (job_id,)).fetchone() is not None)


def lost_launch_result() -> str:
    """The result record of a job failed for a lost launch."""
    return json.dumps({"reconciled": True,
                       "evidence": "accepted with no supervisor within the launch grace"}, sort_keys=True)
