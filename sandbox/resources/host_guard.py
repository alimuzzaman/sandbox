"""Shared host reclaim guard and a read-only host-apply lock probe (spec 057).

Every reclaiming path on a host ends in the shipped probe's ``reclaim``
action, which takes this guard before its first manifest write.  The probe
program is self-contained, so it carries its own copy of the flock code and of
the paths below; ``tests/test_host_reclaim_guard.py`` keeps them in lockstep
with this module and with the apply transactions that own the lock files.

The apply locks are only probed, never taken: host apply is not changed.
"""

from __future__ import annotations

from contextlib import contextmanager
import errno
import fcntl
import os
from pathlib import Path
import stat
from typing import Iterator, Sequence

# Literal copies of the lock files held by host apply transactions: Caddy
# hosting (sandbox/commands/hosting.py), the nginx edge
# (sandbox/hosting/front_door/nginx.py) and the Docker address pool
# (sandbox/core/_remote.py).  Duplicated, not imported, because those modules
# are owned elsewhere and the probe program cannot import them.
APPLY_TRANSACTION_LOCKS = (
    "/run/lock/sandbox-hosting-caddy.lock",
    "/run/lock/sandbox-edge-nginx.lock",
    "/run/lock/sandbox-docker-pool.lock",
)
GUARD_NAME = "reclaim-host.lock"


def host_runtime() -> Path:
    """Return the runtime root exactly as the shipped probe computes it."""
    home = os.environ.get("SANDBOX_HOME") or str(Path.home() / "sandbox")
    return Path(home).resolve() / "runtime"


def reclaim_guard_path(runtime: Path | None = None) -> Path:
    return Path(runtime or host_runtime()) / "resources" / GUARD_NAME


@contextmanager
def try_reclaim_guard(*, runtime: Path | None = None) -> Iterator[bool]:
    """Take the guard without blocking; yield whether it was acquired.

    A guard file that cannot be opened safely (a symlink, another owner, not a
    regular file) yields ``False``: the caller treats it as busy and removes
    nothing.
    """
    path = reclaim_guard_path(runtime)
    descriptor = -1
    acquired = False
    try:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600,
            )
            state = os.fstat(descriptor)
            if (stat.S_ISREG(state.st_mode) and state.st_uid == os.getuid()
                    and not stat.S_IMODE(state.st_mode) & 0o077):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
        except OSError:
            acquired = False
        yield acquired
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def apply_transaction_active(paths: Sequence[str | os.PathLike] = APPLY_TRANSACTION_LOCKS) -> bool:
    """Return True when any host-apply transaction lock is currently held.

    Probed with ``LOCK_SH | LOCK_NB`` and released immediately.  A missing
    file means no transaction has run since boot.  A file this user cannot
    open cannot be probed and is reported as not held; apply transactions
    create these files world-readable.
    """
    for path in paths:
        try:
            descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            continue
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EWOULDBLOCK, errno.EAGAIN}:
                return True
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return False


__all__ = [
    "APPLY_TRANSACTION_LOCKS",
    "GUARD_NAME",
    "apply_transaction_active",
    "host_runtime",
    "reclaim_guard_path",
    "try_reclaim_guard",
]
