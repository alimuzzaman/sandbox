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
    if since.tzinfo is None or (now or datetime.now(timezone.utc)) - since <= timedelta(seconds=grace_seconds):
        return False
    process = (snapshot(row["job_id"]) or {}).get("process") or {}
    return not process.get("supervisor_pid")


def lost_launch_result() -> str:
    """The result record of a job failed for a lost launch."""
    return json.dumps({"reconciled": True,
                       "evidence": "accepted with no supervisor within the launch grace"}, sort_keys=True)
