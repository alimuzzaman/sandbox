"""Instance writer coordination.

Lock order: a per-project writer lock (held across one project's lifecycle
operation, including Compose up/down) before the host-wide writer lock, which is
a leaf held only around canonical registry read-compare-write steps.  Different
projects therefore run their lifecycle operations in parallel.
"""
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import fcntl
import hashlib
import os
import sys
import threading
import time

_mutex = threading.RLock()
_state = threading.local()
_roots = threading.local()

WAIT_MESSAGE = 'waiting for another instance operation'
DEFAULT_WAIT_SECONDS = 120.0


class LifecycleLockTimeout(TimeoutError):
    """The host-wide registry writer lock stayed busy past its bound."""


def _wait_seconds() -> float:
    try:
        value = float(os.environ.get('SANDBOX_LIFECYCLE_LOCK_TIMEOUT', DEFAULT_WAIT_SECONDS))
    except ValueError:
        return DEFAULT_WAIT_SECONDS
    return value if value > 0 else DEFAULT_WAIT_SECONDS


def _timeout(waited: float) -> LifecycleLockTimeout:
    return LifecycleLockTimeout(
        f'instance registry lock still busy after {waited:.0f}s; '
        'another instance operation holds runtime/locks/instance-lifecycle.lock')


@contextmanager
def lifecycle_writer():
    # flock alone is not reentrant across independently opened descriptors.
    # Threads serialize here; nested registry calls retain one fd.
    bound = _wait_seconds()
    deadline = time.monotonic() + bound
    if not _mutex.acquire(blocking=False):
        print(WAIT_MESSAGE, file=sys.stderr, flush=True)
        if not _mutex.acquire(timeout=bound):
            raise _timeout(bound)
        announced = True
    else:
        announced = False
    try:
        if getattr(_state, 'held', False):
            yield
            return
        from sandbox.core._paths import RUNTIME_DIR
        directory = RUNTIME_DIR / 'locks'
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'instance-lifecycle.lock').open('a') as handle:
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not announced:
                        print(WAIT_MESSAGE, file=sys.stderr, flush=True)
                        announced = True
                    if time.monotonic() >= deadline:
                        raise _timeout(bound)
                    time.sleep(0.05)
            _state.held = True
            try:
                yield
            finally:
                _state.held = False
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        _mutex.release()


def serialized_lifecycle(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with lifecycle_writer():
            return function(*args, **kwargs)
    return guarded


@contextmanager
def project_writer(root, directory=None):
    """Per-project writer lock, reentrant within one thread.

    Every operation that can change one project's instance incarnation or
    receipts holds this from its record read through its final registry write,
    so a stale record captured by one operation never overwrites a newer one.
    """
    canonical = str(Path(root).expanduser().resolve())
    held = _roots.__dict__.setdefault('held', set())
    if canonical in held:
        yield
        return
    if directory is None:
        from sandbox.core._paths import RUNTIME_DIR
        directory = RUNTIME_DIR / 'locks'
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(canonical.encode()).hexdigest()[:16]
    with (directory / f'{digest}.lock').open('w') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        held.add(canonical)
        try:
            yield
        finally:
            held.discard(canonical)
            fcntl.flock(handle, fcntl.LOCK_UN)
