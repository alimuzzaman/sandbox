"""Render, install and remove the routine's ``systemd --user`` timer.

One service/timer pair per host.  File writes, the pre-write snapshot and the
rollback reuse the hardened helpers from :mod:`sandbox.resources.schedule`;
the policy here is the routine's own (FR-005 forbids reading the monitor's
``schedule_calendar``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from sandbox.resources import schedule

from .contract import RoutineError, span_seconds

UNIT = "sandbox-cleanup-routine"
SERVICE = f"{UNIT}.service"
TIMER = f"{UNIT}.timer"
UNIT_MODE = 0o644
BACKSTOP_SECONDS = 60
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")
_SYSTEMD_UTC = re.compile(
    r"(?:[A-Z][a-z]{2} )?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC$"
)


def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def unit_paths() -> dict[str, Path]:
    return {name: unit_dir() / name for name in (SERVICE, TIMER)}


def _run(argv: Sequence[str], timeout: float = 15) -> tuple[int, str]:
    """Run one fixed host command, bounded, and return (code, stdout)."""
    environment = dict(os.environ)
    # Timestamps are parsed only in UTC and in the C locale.
    environment.update({"TZ": "UTC", "LC_ALL": "C"})
    try:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout,
            check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""
    return completed.returncode, (completed.stdout or "")[:65536]


def _systemd_utc(text: str) -> str | None:
    match = _SYSTEMD_UTC.search(text.strip())
    if match is None:
        return None
    moment = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    return moment.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def render(config: Mapping[str, Any], *, sb_path: str, sandbox_home: str) -> dict[str, str]:
    """Return the service and timer text for one validated routine config."""
    for value in (sb_path, sandbox_home):
        if not isinstance(value, str) or _SAFE_PATH.fullmatch(value) is None:
            raise RoutineError(
                "routine paths must be absolute and free of spaces or shell characters",
                "routine_install_failed",
            )
    backstop = math.ceil(span_seconds(config["timeout"])) + BACKSTOP_SECONDS
    service = "\n".join((
        "[Unit]",
        "Description=Sandbox scheduled safe cleanup",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "UMask=0077",
        # The run enforces its own bound; this is the init-system backstop.
        f"TimeoutStartSec={backstop}s",
        f"Environment=SANDBOX_HOME={sandbox_home}",
        f"ExecStart={sb_path} resources routine --routine-run --json",
        "",
    ))
    timer = "\n".join((
        "[Unit]",
        "Description=Sandbox scheduled safe cleanup timer",
        "",
        "[Timer]",
        f"OnCalendar={config['cadence']}",
        f"RandomizedDelaySec={config['randomized_delay']}",
        "Persistent=true",
        f"Unit={SERVICE}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ))
    return {SERVICE: service, TIMER: timer}


def check_host() -> None:
    """Refuse before any write on a host without systemd or user lingering."""
    if any(shutil.which(tool) is None
           for tool in ("systemctl", "systemd-analyze", "loginctl")):
        raise RoutineError("the host has no systemd user manager", "systemd_unavailable")
    code, output = _run(
        ["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"],
    )
    if code != 0 or output.strip().lower() != "yes":
        raise RoutineError(
            "user lingering is off; the timer would stop at logout", "linger_disabled",
        )


def validate_cadence(cadence: str) -> str | None:
    """Validate on the host with systemd itself; return the next elapse (UTC)."""
    code, output = _run(["systemd-analyze", "calendar", cadence])
    if code != 0:
        raise RoutineError("cadence is not a valid systemd calendar expression",
                           "invalid_cadence")
    for marker in ("(in UTC):", "Next elapse:"):
        for line in output.splitlines():
            if line.strip().startswith(marker):
                parsed = _systemd_utc(line.split(":", 1)[1])
                if parsed:
                    return parsed
    return None


def _systemctl(*args: str) -> None:
    code, _output = _run(["systemctl", "--user", *args], timeout=30)
    if code != 0:
        raise RoutineError(f"systemctl --user {args[0]} failed", "routine_install_failed")


def install(config: Mapping[str, Any], *, sb_path: str, sandbox_home: str,
            preflight: bool = True) -> dict[str, Any]:
    """Install or replace the routine's units and enable the timer.

    Order: host checks, cadence, render, snapshot, write, reload, enable.  Any
    failure after the first write restores the prior unit files.  A caller
    that already ran :func:`check_host` and :func:`validate_cadence` passes
    ``preflight=False``.
    """
    next_elapse = None
    if preflight:
        check_host()
        next_elapse = validate_cadence(config["cadence"])
    rendered = render(config, sb_path=sb_path, sandbox_home=sandbox_home)
    paths = unit_paths()
    try:
        prior = schedule.snapshot_installation({path: UNIT_MODE for path in paths.values()})
    except schedule.ScheduleError as exc:
        raise RoutineError(str(exc), "routine_install_failed") from None
    try:
        for name, path in paths.items():
            schedule.write_unit(path, rendered[name], UNIT_MODE)
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", TIMER)
    except (RoutineError, schedule.ScheduleError, OSError) as exc:
        try:
            schedule.restore_installation(prior)
        except schedule.ScheduleError:
            pass
        _run(["systemctl", "--user", "daemon-reload"], timeout=30)
        raise RoutineError(
            f"routine timer could not be installed: {exc}", "routine_install_failed",
        ) from None
    return {"next_run": next_elapse, "units": sorted(paths)}


def next_run() -> str | None:
    """Read the installed timer's next elapse, or None when unknown."""
    code, output = _run([
        "systemctl", "--user", "show", TIMER,
        "--property=NextElapseUSecRealtime", "--value",
    ])
    if code != 0:
        return None
    return _systemd_utc(output)


__all__ = [
    "SERVICE", "TIMER", "UNIT", "check_host", "install", "next_run", "render",
    "unit_dir", "unit_paths", "validate_cadence",
]
