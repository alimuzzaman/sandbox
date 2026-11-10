"""Stop tests from ending or replacing the unittest runner process.

``os._exit`` and ``os.execv``/``os.execve`` (which every other ``os.exec*``
helper calls) leave the current process without unwinding. When a test reaches
one in-process, ``unittest discover`` stops partway, prints no summary and can
still exit 0, so every later failure is hidden. ``install()`` makes those calls
raise ``RunnerExitError`` in the process that installed the guard, so the test
fails instead. Forked children keep the real calls. Programs that are meant to
exit run in a child through ``tests.subprocess_support.run_test_process``.
"""

from __future__ import annotations

import os

GUARDED = ("_exit", "execv", "execve")
_runner_pid: int | None = None


class RunnerExitError(AssertionError):
    """A test tried to end or replace the test-runner process."""


def _guard(name: str, real):
    def guarded(*args, **kwargs):
        if os.getpid() != _runner_pid:
            return real(*args, **kwargs)
        raise RunnerExitError(
            f"os.{name} was called in the test-runner process; run the "
            "program in a child through tests.subprocess_support.run_test_process")

    guarded.__wrapped__ = real
    guarded.sandbox_runner_guard = True
    return guarded


def install() -> None:
    """Guard the current process. Safe to call more than once."""
    global _runner_pid
    if getattr(os._exit, "sandbox_runner_guard", False):
        return
    _runner_pid = os.getpid()
    for name in GUARDED:
        setattr(os, name, _guard(name, getattr(os, name)))
