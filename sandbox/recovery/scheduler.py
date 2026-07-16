"""Non-overlapping recovery schedule planning; installation is deliberately separate."""
from __future__ import annotations

import re
import shlex

from .errors import RecoveryError
from .models import SchedulePolicy

_POLICY_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_TIME_SPAN = re.compile(
    r"^[0-9]+(?:us|ms|s|min|h|d|w|m)(?:[ \t]+[0-9]+(?:us|ms|s|min|h|d|w|m))*$"
)


def _validate_unit_value(value: str, field: str, *, time_span: bool = False) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "\r\n\0"):
        raise ValueError(f"schedule {field} contains unsafe unit text")
    if time_span and not _TIME_SPAN.fullmatch(value):
        raise ValueError(f"schedule {field} is not a valid systemd time span")
    return value


def build_schedule_policy(policy_id: str, profiles: tuple[str, ...], calendar: str, *,
                          randomized_delay: str = "15m", timeout: str = "6h",
                          remote: str | None = None) -> SchedulePolicy:
    if (not isinstance(policy_id, str) or not isinstance(profiles, tuple) or
            not isinstance(calendar, str) or not isinstance(randomized_delay, str) or
            not isinstance(timeout, str) or not policy_id or not profiles or not calendar or
            not randomized_delay or not timeout):
        raise ValueError("schedule policy requires id, profiles, calendar, delay, and timeout")
    if not _POLICY_ID.fullmatch(policy_id):
        raise ValueError("schedule policy id must be a lowercase slug")
    if any(not isinstance(profile, str) or not _POLICY_ID.fullmatch(profile) for profile in profiles):
        raise ValueError("schedule profile ids must be lowercase slugs")
    if remote is not None and (not isinstance(remote, str) or not _POLICY_ID.fullmatch(remote)):
        raise ValueError("schedule remote must be a lowercase slug")
    _validate_unit_value(calendar, "calendar")
    _validate_unit_value(randomized_delay, "randomized delay", time_span=True)
    _validate_unit_value(timeout, "timeout", time_span=True)
    return SchedulePolicy(policy_id, profiles, calendar, enabled=False,
                          randomized_delay=randomized_delay, timeout=timeout, remote=remote)


def render_systemd_units(policy: SchedulePolicy, command: str = "sb recovery create") -> dict[str, str]:
    if any(char in command for char in "\r\n\0") or command != "sb recovery create":
        raise RecoveryError("recovery schedule command is invalid", "invalid_schedule_command")
    arguments = ["sb", "recovery", "create", "--confirm"]
    for profile in policy.profiles:
        arguments.extend(("--profile", profile))
    if policy.remote is not None:
        arguments.extend(("--remote", policy.remote))
    scheduled_command = shlex.join(arguments)
    name = f"sandbox-recovery-{policy.policy_id}"
    service = "\n".join(("[Unit]", "Description=Sandbox scoped recovery capture", "",
                           "[Service]", "Type=oneshot", "UMask=0077",
                           f"TimeoutStartSec={policy.timeout}",
                           f"ExecStart=/usr/bin/flock -n %t/{name}.lock {scheduled_command}", ""))
    timer = "\n".join(("[Unit]", "Description=Schedule scoped recovery capture", "",
                         "[Timer]", f"OnCalendar={policy.calendar}",
                         f"RandomizedDelaySec={policy.randomized_delay}",
                         "Persistent=true", "", "[Install]", "WantedBy=timers.target", ""))
    return {"service": service, "timer": timer, "enabled": "false"}


def render_systemd_timer(policy: SchedulePolicy, command: str = "sb recovery create") -> str:
    """Compatibility helper: render the timer only; ExecStart belongs in the service."""
    return render_systemd_units(policy, command)["timer"]


def run_with_lock(lock, action, *, resource_ok=lambda: True) -> dict:
    if not lock.acquire():
        return {"status": "skipped", "reason": "lock_held"}
    try:
        if not resource_ok():
            return {"status": "skipped", "reason": "resource_gate"}
        outcome = action()
        if isinstance(outcome, dict):
            error = outcome.get("error") if isinstance(outcome.get("error"), dict) else {}
            if outcome.get("ok") is False or outcome.get("status") == "failed":
                return {"status": "failed", "reason": str(
                    error.get("code") or outcome.get("reason") or "action_failed"),
                        "result": outcome}
            if outcome.get("status") == "skipped":
                return {"status": "skipped", "reason": str(
                    outcome.get("reason") or "action_skipped"), "result": outcome}
        return {"status": "complete"}
    finally:
        lock.release()
