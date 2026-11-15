"""The follower's ``systemd --user`` timer (spec 089, FR-004).

One service/timer pair per host, written with the same hardened unit writer,
snapshot and rollback as the cleanup routine (``sandbox.resources.schedule``).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from sandbox.hosting.follow.spec import FollowError

UNIT = "sandbox-stack-follow"
SERVICE = f"{UNIT}.service"
TIMER = f"{UNIT}.timer"
UNIT_MODE = 0o644
INTERVAL = "5min"
# A poll starts no new follow after this budget; systemd's limit adds room for
# the follow in progress.
TICK_BUDGET_SECONDS = 600
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._/-]+$")


def unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def unit_paths() -> dict[str, Path]:
    return {name: unit_dir() / name for name in (SERVICE, TIMER)}


def _run(argv: list[str], timeout: float = 30) -> int:
    environment = dict(os.environ)
    environment.update({"LC_ALL": "C"})
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False,
                              env=environment, stdin=subprocess.DEVNULL).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 124


def render(*, sb_path: str, sandbox_home: str) -> dict[str, str]:
    for value in (sb_path, sandbox_home):
        if not isinstance(value, str) or _SAFE_PATH.fullmatch(value) is None:
            raise FollowError("follow_timer_failed",
                              "timer paths must be absolute and free of spaces or shell characters")
    service = "\n".join((
        "[Unit]",
        "Description=Sandbox stack branch follower poll",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "UMask=0077",
        f"TimeoutStartSec={TICK_BUDGET_SECONDS + 120}s",
        f"Environment=SANDBOX_HOME={sandbox_home}",
        f"ExecStart={sb_path} host follow tick --here --json",
        "",
    ))
    timer = "\n".join((
        "[Unit]",
        "Description=Sandbox stack branch follower timer",
        "",
        "[Timer]",
        "OnBootSec=2min",
        f"OnUnitActiveSec={INTERVAL}",
        "RandomizedDelaySec=30s",
        "AccuracySec=30s",
        f"Unit={SERVICE}",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ))
    return {SERVICE: service, TIMER: timer}


def check_host() -> None:
    from sandbox.resources.cleanup_routine import units as routine_units
    from sandbox.resources.cleanup_routine.contract import RoutineError
    try:
        routine_units.check_host()
    except RoutineError as exc:
        raise FollowError("follow_timer_unavailable", str(exc)) from None


def installed() -> bool:
    return all(path.exists() for path in unit_paths().values())


def install(*, sb_path: str, sandbox_home: str) -> dict:
    """Write both units and enable the timer; restore the prior files on failure."""
    from sandbox.resources import schedule
    check_host()
    rendered = render(sb_path=sb_path, sandbox_home=sandbox_home)
    paths = unit_paths()
    current = {name: path.read_text() if path.exists() else None for name, path in paths.items()}
    if current == rendered and _run(["systemctl", "--user", "is-enabled", "--quiet", TIMER]) == 0:
        return {"timer": TIMER, "changed": False}
    try:
        prior = schedule.snapshot_installation({path: UNIT_MODE for path in paths.values()})
    except schedule.ScheduleError as exc:
        raise FollowError("follow_timer_failed", str(exc)) from None
    try:
        for name, path in paths.items():
            schedule.write_unit(path, rendered[name], UNIT_MODE)
        for argv in (["systemctl", "--user", "daemon-reload"],
                     ["systemctl", "--user", "enable", "--now", TIMER]):
            if _run(argv) != 0:
                raise FollowError("follow_timer_failed", f"{' '.join(argv[:3])} failed")
    except (FollowError, schedule.ScheduleError, OSError) as exc:
        try:
            schedule.restore_installation(prior)
        except schedule.ScheduleError:
            pass
        _run(["systemctl", "--user", "daemon-reload"])
        raise FollowError("follow_timer_failed", f"the follower timer could not be installed: {exc}") from None
    return {"timer": TIMER, "changed": True}


def remove() -> dict:
    paths = unit_paths()
    if not any(os.path.lexists(path) for path in paths.values()):
        return {"timer": TIMER, "removed": False}
    if _run(["systemctl", "--user", "disable", "--now", TIMER]) != 0:
        raise FollowError("follow_timer_failed", "systemctl --user disable failed; the timer is still installed")
    for path in paths.values():
        path.unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    return {"timer": TIMER, "removed": True}


def start_now() -> bool:
    """Ask systemd to run one poll now, without waiting for it."""
    return _run(["systemctl", "--user", "start", "--no-block", SERVICE]) == 0
