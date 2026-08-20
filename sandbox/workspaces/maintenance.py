"""Cross-process locks for workspace-index and home-maintenance access."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path


BASE_MAINTENANCE_LOCK = ".migration.lock"
_HELD = threading.local()


class BaseMaintenanceBusy(RuntimeError):
    """A relocation or repository operation owns the base maintenance lock."""


def _held() -> dict[Path, dict[str, object]]:
    state = getattr(_HELD, "locks", None)
    if state is None:
        state = {}
        _HELD.locks = state
    return state


@contextmanager
def base_maintenance_lock(*bases: Path, exclusive: bool, timeout_seconds: float = 0.0):
    """Lock managed bases in a stable order, re-entrantly within one thread.

    Home relocation takes the exclusive form. Repository SQLite access takes
    the shared form, so a WAL checkpoint and copy can never race a repository
    transaction. A caller holding a shared lock cannot upgrade it: failing
    closed avoids lock-order inversions and accidental concurrent relocation.
    """
    if (isinstance(timeout_seconds, bool) or
            not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 0):
        raise ValueError("timeout_seconds must be a non-negative number")
    try:
        import fcntl
    except ImportError as exc:  # Sandbox supports POSIX home migration only.
        raise BaseMaintenanceBusy("base maintenance locking requires POSIX") from exc

    try:
        paths = sorted(
            {Path(base).expanduser().resolve(strict=False) for base in bases}, key=str)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise BaseMaintenanceBusy("base maintenance path is unavailable") from exc
    state = _held()
    acquired: list[Path] = []
    referenced: list[Path] = []
    try:
        for base in paths:
            entry = state.get(base)
            if entry is not None:
                if exclusive and entry["mode"] != "exclusive":
                    raise BaseMaintenanceBusy("cannot upgrade a shared base maintenance lock")
                entry["count"] = int(entry["count"]) + 1
                referenced.append(base)
                continue
            try:
                base.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(base / BASE_MAINTENANCE_LOCK, os.O_CREAT | os.O_RDWR, 0o600)
            except OSError as exc:
                raise BaseMaintenanceBusy("base maintenance lock is unavailable") from exc
            try:
                os.fchmod(descriptor, 0o600)
                flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
                deadline = time.monotonic() + float(timeout_seconds)
                while True:
                    try:
                        fcntl.flock(descriptor, flags)
                        break
                    except BlockingIOError as exc:
                        if time.monotonic() >= deadline:
                            raise BaseMaintenanceBusy(
                                f"base maintenance lock is already held for {base}"
                            ) from exc
                        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            except Exception:
                os.close(descriptor)
                raise
            state[base] = {
                "mode": "exclusive" if exclusive else "shared",
                "count": 1,
                "descriptor": descriptor,
            }
            acquired.append(base)
            referenced.append(base)
        yield
    finally:
        for base in reversed(referenced):
            entry = state[base]
            count = int(entry["count"]) - 1
            if count:
                entry["count"] = count
                continue
            descriptor = int(entry["descriptor"])
            del state[base]
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
