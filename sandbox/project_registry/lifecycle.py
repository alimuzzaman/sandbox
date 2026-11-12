"""Host-wide instance writer coordination, ordered before project/registry locks."""
from contextlib import contextmanager
from functools import wraps
import fcntl
import threading

_mutex = threading.RLock()
_state = threading.local()


@contextmanager
def lifecycle_writer():
    # flock alone is not reentrant across independently opened descriptors.
    # Threads serialize here; nested native/CLI/registry calls retain one fd.
    with _mutex:
        if getattr(_state, 'held', False):
            yield
            return
        from sandbox.core._paths import RUNTIME_DIR
        directory = RUNTIME_DIR / 'locks'
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'instance-lifecycle.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _state.held = True
            try:
                yield
            finally:
                _state.held = False
                fcntl.flock(handle, fcntl.LOCK_UN)


def serialized_lifecycle(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with lifecycle_writer():
            return function(*args, **kwargs)
    return guarded
