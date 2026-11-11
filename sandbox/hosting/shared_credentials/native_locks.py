"""One scoped hold of existing native repository/key locks, not effect authority.

The generic factory acquires this scope before canonical lease/gate acquisition.
Credential execution may nest the EXACT same set without a second flock.
Registered resolution supplies actual repositories; no path/key comes from wire.
Canonical host/gate and credential authority remain independently mandatory.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import math
import os
from pathlib import Path
import threading
import time

from sandbox.hosting.recovery.repository import RecoveryRepository


class NativeLockError(ValueError):
    def __init__(self, code="credential_target_lock_unavailable"):
        self.code = code
        super().__init__(code)


_LOCAL = threading.local()


def _ordered(bindings):
    if type(bindings) is not tuple or not 1 <= len(bindings) <= 64:
        raise NativeLockError()
    selected = {}
    for item in bindings:
        if (type(item) is not tuple or len(item) != 2
                or type(item[0]) is not RecoveryRepository or not isinstance(item[1], str)
                or not 1 <= len(item[1]) <= 512 or len(item[1].split("/")) != 3
                or any(not part or part in (".", "..") for part in item[1].split("/"))
                or any(ord(c) < 33 or ord(c) > 126 for c in item[1])):
            raise NativeLockError()
        repository, key = item
        identity = (str(Path(repository.state_path).resolve()),
                    str(Path(repository.lock_dir).resolve()), key)
        if identity in selected:
            raise NativeLockError("credential_target_lock_duplicate")
        selected[identity] = item
    return tuple(sorted(selected)), tuple(selected[key] for key in sorted(selected))


class _NativeHold:
    def __init__(self):
        raise TypeError("native lock scopes are privately held")

    def __reduce__(self):
        raise TypeError("native lock scopes cannot be serialized")

    def require(self, identity):
        if (getattr(_LOCAL, "hold", None) is not self or not self.live
                or self.owner != (os.getpid(), threading.get_ident())
                or self.identity != identity or time.monotonic() >= self.deadline):
            raise NativeLockError()


def require_native_mutations(bindings):
    identity, _ = _ordered(bindings)
    hold = getattr(_LOCAL, "hold", None)
    if type(hold) is not _NativeHold:
        raise NativeLockError()
    hold.require(identity)


@contextmanager
def hold_native_mutations(bindings, *, timeout_seconds=30):
    identity, ordered = _ordered(bindings)
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 86400):
        raise NativeLockError()
    current = getattr(_LOCAL, "hold", None)
    if current is not None:
        # No subset, rebound set, different target or second descriptor lock.
        current.require(identity)
        yield
        current.require(identity)
        return
    deadline = time.monotonic() + timeout_seconds
    failed = False
    with ExitStack() as locks:
        try:
            for repository, key in ordered:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeLockError()
                locks.enter_context(repository.target_mutation_port("shared_credentials",
                    timeout_seconds=remaining).target_mutation_transaction(key))
        except Exception:
            failed = True
        if failed:
            raise NativeLockError() from None
        hold = object.__new__(_NativeHold)
        hold.identity, hold.owner = identity, (os.getpid(), threading.get_ident())
        hold.deadline, hold.live = deadline, True
        _LOCAL.hold = hold
        try:
            hold.require(identity)
            yield
            hold.require(identity)
        finally:
            hold.live = False
            _LOCAL.hold = None


__all__ = ["NativeLockError", "hold_native_mutations", "require_native_mutations"]
