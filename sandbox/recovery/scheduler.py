from __future__ import annotations

from .models import SchedulePolicy


def build_schedule_policy(policy_id: str, profiles: tuple[str, ...], calendar: str) -> SchedulePolicy:
    if not policy_id or not profiles or not calendar:
        raise ValueError("schedule policy requires an id, profiles, and calendar")
    return SchedulePolicy(policy_id, profiles, calendar, enabled=False)


def render_systemd_timer(policy: SchedulePolicy, command: str = "sb recovery create") -> str:
    return "\n".join(("[Timer]", f"OnCalendar={policy.calendar}", "RandomizedDelaySec=15m",
                        f"ExecStart={command}", "", "[Install]", "WantedBy=timers.target", ""))
