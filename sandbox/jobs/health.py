"""Evidence-based health classification for durable job observation."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .models import Health, TERMINAL_LIFECYCLES, Lifecycle
from .process import ProcessIdentity, capture_process_identity, verify_process_identity


def _age(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
    except (TypeError, ValueError):
        return None


def _observed_identity(process: dict, prefix: str) -> tuple[bool | None, str | None]:
    """Return identity validity and an evidence reason for one recorded process.

    ``None`` means the record does not contain enough identity data to verify that
    process.  A false value means a process was observed but it is not the process
    recorded for this job.  The nonce is never read back from disk; it is carried
    into the observed value only for the comparison performed here.
    """
    pid = process.get(f"{prefix}_pid")
    start = process.get(f"{prefix}_start_identity")
    boot = process.get("host_boot_id")
    nonce = process.get("supervisor_nonce_hash")
    if not pid or not start or not boot or not nonce:
        return None, None
    observed = capture_process_identity(int(pid))
    if observed is None:
        return False, f"recorded {prefix} PID is absent"
    expected = ProcessIdentity(boot, int(pid), start, nonce,
                               process.get(f"{prefix}_pgid") if prefix == "child" else None)
    observed = ProcessIdentity(observed.host_boot_id, observed.pid, observed.start_identity,
                               nonce, observed.process_group_id)
    if verify_process_identity(expected, observed):
        return True, f"recorded {prefix} identity matches"
    return False, f"observed {prefix} identity does not match the recorded process"
def classify(snapshot: dict, *, now: datetime | None = None) -> tuple[Health, dict]:
    now = now or datetime.now(timezone.utc)
    lifecycle = Lifecycle(snapshot["lifecycle"])
    if lifecycle in TERMINAL_LIFECYCLES:
        return Health.TERMINAL, {"classified_at": now.isoformat(), "reasons": ["terminal lifecycle"]}
    if snapshot.get("target_reachable") is False or (snapshot.get("target") or {}).get("reachable") is False:
        return Health.UNREACHABLE, {"classified_at": now.isoformat(),
                                    "reasons": ["selected execution target is unreachable"]}
    process = snapshot.get("process") or {}
    heartbeat = snapshot.get("heartbeat") or {}
    child_pid = process.get("child_pid")
    child_alive = False
    identity_evidence: dict[str, str] = {}
    if child_pid:
        try:
            os.kill(int(child_pid), 0); child_alive = True
        except OSError:
            pass
        child_identity, child_reason = _observed_identity(process, "child")
        if child_reason:
            identity_evidence["child"] = child_reason
        if child_identity is False and child_alive:
            return Health.ORPHANED, {"classified_at": now.isoformat(), "child_alive": True,
                                     "identity": identity_evidence,
                                     "reasons": ["recorded child identity is no longer valid"]}
    output_age = _age(heartbeat.get("last_output_at"), now)
    activity_age = _age(heartbeat.get("last_activity_at"), now)
    progress_age = _age(heartbeat.get("last_progress_at"), now)
    metric_age = _age(heartbeat.get("last_metric_at"), now)
    supervisor_age = _age(heartbeat.get("supervisor_at"), now)
    stall = int(snapshot.get("stall_seconds") or 300)
    evidence = {"classified_at": now.isoformat(), "child_alive": child_alive,
                "last_output_age_seconds": output_age, "supervisor_heartbeat_age_seconds": supervisor_age,
                "last_activity_age_seconds": activity_age, "last_progress_age_seconds": progress_age,
                "last_metric_age_seconds": metric_age, "stall_threshold_seconds": stall,
                "identity": identity_evidence, "reasons": []}
    if (process.get("identity_valid") is False or process.get("orphaned") is True):
        evidence["reasons"].append("job record explicitly reports an invalid ownership identity")
        return Health.ORPHANED, evidence
    supervisor_identity, supervisor_reason = _observed_identity(process, "supervisor")
    if supervisor_reason:
        evidence["identity"]["supervisor"] = supervisor_reason
    if supervisor_identity is False and child_alive:
        evidence["reasons"].append("supervisor identity is absent or no longer matches")
        return Health.SUPERVISOR_UNRESPONSIVE, evidence
    if supervisor_age is not None and supervisor_age > stall * 2:
        evidence["reasons"].append("supervisor heartbeat is stale")
        return Health.SUPERVISOR_UNRESPONSIVE, evidence
    if child_pid and not child_alive:
        if supervisor_identity is True:
            evidence["reasons"].append(
                "recorded child exited while its verified supervisor finalizes the result")
            return Health.ACTIVE, evidence
        evidence["reasons"].append("recorded child PID is absent")
        return Health.PROCESS_MISSING, evidence
    # Recent metrics/progress mean a quiet command is still making observable
    # progress.  Only sustained absence of every signal becomes a stall.
    signal_ages = [age for age in (output_age, activity_age, progress_age, metric_age) if age is not None]
    inactivity_age = max(signal_ages) if signal_ages else None
    metric_evidence = heartbeat.get("health_evidence") or {}
    if metric_evidence.get("progress") or metric_evidence.get("metric_movement"):
        inactivity_age = min(inactivity_age or 0, 0)
    evidence["inactivity_age_seconds"] = inactivity_age
    if inactivity_age is not None and inactivity_age > stall:
        state = metric_evidence.get("state")
        evidence["reasons"].append("no output, activity, progress, or metric movement exceeded stall threshold")
        if inactivity_age > stall * 2 or state in {"D", "Z"}:
            evidence["reasons"].append("stall evidence persisted beyond the second threshold")
            return Health.STUCK, evidence
        return Health.SUSPECTED_STALLED, evidence
    if child_alive:
        if output_age is not None and output_age > 30 and not metric_evidence.get("progress"):
            evidence["reasons"].append("owned child is alive but output is quiet")
            return Health.QUIET, evidence
        evidence["reasons"].append("owned child process is alive and has recent evidence")
        return Health.ACTIVE, evidence
    if inactivity_age is not None and inactivity_age > stall:
        evidence["reasons"].append("observed output is stale while launch identity is incomplete")
        return Health.SUSPECTED_STALLED, evidence
    evidence["reasons"].append("waiting for supervisor launch")
    return Health.UNKNOWN, evidence
