"""Timed phase markers for `sb host stack up` and `sb host apply`.

A ``PhaseClock`` is a lap timer: ``clock.lap("edge_dns")`` finishes the phase
that was running and starts the named one, so a call site is one line and a
long block needs no re-indenting. Each phase prints a ``started`` line and a
``finished`` line with its elapsed seconds, in the same shape as the Compose
markers in apply.log::

    [Sandbox] apply phase=edge_dns event=started at=2026-11-22T06:08:10Z
    [Sandbox] apply phase=edge_dns event=finished at=2026-11-22T06:08:21Z elapsed=10.9s

A phase that raises keeps only its ``started`` line, which names where a failed
or hung run stopped. Phase names are fixed identifiers, never data, so a marker
can carry no secret.
"""
from __future__ import annotations

import datetime
import re
import sys
import time
from typing import Callable

_PHASE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SCOPE = re.compile(r"[a-z][a-z0-9-]{0,31}")


def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PhaseClock:
    """Lap timer that reports each phase through ``emit`` (a no-op when None)."""

    def __init__(self, emit: Callable[[str], None] | None, *, scope: str = "apply",
                 monotonic: Callable[[], float] = time.monotonic,
                 stamp: Callable[[], str] = _stamp) -> None:
        if not _SCOPE.fullmatch(scope):
            raise ValueError("invalid phase scope")
        self._emit = emit
        self._scope = scope
        self._monotonic = monotonic
        self._stampf = stamp
        self._current: tuple[str, float] | None = None

    def lap(self, phase: str) -> None:
        """Finish the running phase, if any, and start ``phase``."""
        if not _PHASE.fullmatch(phase):
            raise ValueError("invalid phase name")
        self.finish()
        self._current = (phase, self._monotonic())
        self._say(f"phase={phase} event=started at={self._stampf()}")

    def finish(self) -> None:
        """Finish the running phase; a no-op when none is running."""
        if self._current is None:
            return
        phase, started = self._current
        self._current = None
        elapsed = max(0.0, self._monotonic() - started)
        self._say(f"phase={phase} event=finished at={self._stampf()} elapsed={elapsed:.1f}s")

    def _say(self, text: str) -> None:
        if self._emit is None:
            return
        try:
            self._emit(f"[Sandbox] {self._scope} {text}")
        except (OSError, ValueError):
            # Timing is evidence only; a closed stream never fails the command.
            pass


def stderr_emit(text: str) -> None:
    print(text, file=sys.stderr, flush=True)
