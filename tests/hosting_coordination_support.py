"""Test harness for spec 060 hosting coordination.

``LocalRemote`` stands in for ``ssh_run``: it runs the command with ``sh -c``
against one temporary "remote" Sandbox home, so the coordination program runs
for real. ``TwoControllers`` gives two controller homes that share that remote
home, and ``run_parallel`` runs callables in separate processes so admission
contention goes through the remote flock rather than a Python lock.
"""
from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.subprocess_support import synthetic_environment


class LocalRemote:
    """``ssh_run`` stand-in bound to one remote home."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.calls = 0
        self.fail = False
        self.commands: list[str] = []

    def __call__(self, _entry, command, timeout=30):
        self.calls += 1
        self.commands.append(command)
        if self.fail:
            raise OSError("ssh: connect to host 192.0.2.1 token=secret")
        env = synthetic_environment({
            "SANDBOX_HOME": str(self.home), "HOME": str(self.home),
            "PATH": f"{os.path.dirname(sys.executable)}:/usr/bin:/bin"})
        return subprocess.run(["sh", "-c", command], env=env, capture_output=True,
                              text=True, timeout=timeout)


class TwoControllers:
    """Two controller homes and one shared remote home under one temp dir."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.remote_home = root / "remote-home"
        self.remote_home.mkdir()
        self.homes = []
        for name in ("controller-a", "controller-b"):
            home = root / name
            (home / "runtime").mkdir(parents=True)
            self.homes.append(home)
        self.remote = LocalRemote(self.remote_home)

    def cleanup(self):
        self._tmp.cleanup()


def _child(queue, func, args):
    try:
        queue.put(("ok", func(*args)))
    except BaseException as exc:  # noqa: BLE001 - reported to the parent
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def run_parallel(calls, timeout=60):
    """Run ``[(func, args), ...]`` in separate processes; return results in order.

    ``func`` must be importable (module level). A child exception is re-raised
    in the parent as ``AssertionError`` naming it.
    """
    context = multiprocessing.get_context("spawn")
    queues, processes = [], []
    for func, args in calls:
        queue = context.Queue()
        process = context.Process(target=_child, args=(queue, func, args))
        process.start()
        queues.append(queue)
        processes.append(process)
    results = []
    for queue, process in zip(queues, processes):
        status, value = queue.get(timeout=timeout)
        process.join(timeout)
        if status != "ok":
            raise AssertionError(value)
        results.append(value)
    return results
