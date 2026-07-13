"""Non-overlapping recovery schedule planning; installation is deliberately separate."""
from __future__ import annotations

from .errors import RecoveryError
from .models import SchedulePolicy


def build_schedule_policy(policy_id: str, profiles: tuple[str, ...], calendar: str, *,
                          randomized_delay: str = "15m", timeout: str = "6h") -> SchedulePolicy:
    if not policy_id or not profiles or not calendar or not randomized_delay or not timeout:
        raise ValueError("schedule policy requires id, profiles, calendar, delay, and timeout")
    return SchedulePolicy(policy_id, profiles, calendar, enabled=False)


def render_systemd_units(policy: SchedulePolicy, command: str = "sb recovery create") -> dict[str, str]:
    if "\n" in command or not command.startswith("sb recovery create"):
        raise RecoveryError("recovery schedule command is invalid", "invalid_schedule_command")
    name = f"sandbox-recovery-{policy.policy_id}"
    service = "\n".join(("[Unit]", "Description=Sandbox scoped recovery capture", "",
                           "[Service]", "Type=oneshot", "UMask=0077",
                           f"ExecStart=/usr/bin/flock -n %t/{name}.lock {command}", ""))
    timer = "\n".join(("[Unit]", "Description=Schedule scoped recovery capture", "",
                         "[Timer]", f"OnCalendar={policy.calendar}", "RandomizedDelaySec=15m",
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
        action()
        return {"status": "complete"}
    finally:
        lock.release()
