"""Small bounded process-group cleanup shared by secret child boundaries."""

from __future__ import annotations

import os
import signal
import subprocess
import time


def _group_state(process: subprocess.Popen, pgid: int) -> str:
    process.poll()
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return "gone"
        except PermissionError:
            # Darwin answers EPERM while the leader is exiting or an unreaped
            # zombie. It is still our child: reap it and look again, or leave
            # it to the bounded wait. Only a reaped leader's group is foreign.
            if process.returncode is None:
                if process.poll() is not None:
                    continue
                return "alive"
            return "unreachable"
        return "alive"


def _wait_group(process: subprocess.Popen, pgid: int, seconds: float) -> bool:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if _group_state(process, pgid) == "gone":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.025)


def terminate_process_group(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 0.5,
    kill_wait_seconds: float = 0.5,
) -> bool:
    """End a ``start_new_session`` child group under a short fixed bound."""
    pgid = process.pid
    state = _group_state(process, pgid)
    if state == "gone":
        return True
    if state == "unreachable":
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    if _wait_group(process, pgid, grace_seconds):
        return True
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    gone = _wait_group(process, pgid, kill_wait_seconds)
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        pass
    return gone


__all__ = ["terminate_process_group"]
