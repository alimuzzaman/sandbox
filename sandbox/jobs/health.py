"""Evidence-based health classification for durable job observation."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .models import Health, TERMINAL_LIFECYCLES, Lifecycle


def _age(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return None


def classify(snapshot: dict, *, now: datetime | None = None) -> tuple[Health, dict]:
    now = now or datetime.now(timezone.utc)
    lifecycle = Lifecycle(snapshot["lifecycle"])
    if lifecycle in TERMINAL_LIFECYCLES:
        return Health.TERMINAL, {"classified_at": now.isoformat(), "reasons": ["terminal lifecycle"]}
    process = snapshot.get("process") or {}
    heartbeat = snapshot.get("heartbeat") or {}
    child_pid = process.get("child_pid")
    child_alive = False
    if child_pid:
        try:
            os.kill(int(child_pid), 0); child_alive = True
        except OSError:
            pass
    output_age = _age(heartbeat.get("last_output_at"), now)
    supervisor_age = _age(heartbeat.get("supervisor_at"), now)
    stall = int(snapshot.get("stall_seconds") or 300)
    evidence = {"classified_at": now.isoformat(), "child_alive": child_alive,
                "last_output_age_seconds": output_age, "supervisor_heartbeat_age_seconds": supervisor_age,
                "stall_threshold_seconds": stall, "reasons": []}
    if child_pid and not child_alive:
        evidence["reasons"].append("recorded child PID is absent")
        return Health.PROCESS_MISSING, evidence
    if supervisor_age is not None and supervisor_age > stall * 2:
        evidence["reasons"].append("supervisor heartbeat is stale")
        return Health.SUPERVISOR_UNRESPONSIVE, evidence
    if output_age is not None and output_age > stall:
        evidence["reasons"].append("no output exceeded stall threshold")
        return Health.SUSPECTED_STALLED, evidence
    if child_alive:
        evidence["reasons"].append("owned child process is alive")
        return (Health.QUIET if output_age and output_age > 30 else Health.ACTIVE), evidence
    evidence["reasons"].append("waiting for supervisor launch")
    return Health.UNKNOWN, evidence
