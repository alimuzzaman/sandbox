"""Run a job supervisor in its own transient ``systemd --user`` scope.

A supervisor launched by the remote control service would otherwise sit in
``sandbox-mcp-remote.service``'s cgroup, and a service restart (every install)
stops that whole cgroup, killing the job. A scope is its own cgroup under the
user manager and ends only when its last process exits, so the job outlives
any restart of whatever launched it. Same pattern as the follow attempt
(``sandbox.hosting.follow.tick.scoped_argv``).

``systemd-run --scope`` execs the command in place: the supervisor keeps the
launcher's PID, umask, cwd, environment and file descriptors (anonymous stdin).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

_USER_MANAGER_KEYS = ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
_probe_ok = False


class ScopeUnavailable(RuntimeError):
    """A supervisor would die with its launcher's service and no scope is available."""


def _own_cgroup() -> str | None:
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="ascii").splitlines():
            hierarchy, _controllers, path = line.split(":", 2)
            if hierarchy == "0":
                return path
    except (OSError, UnicodeError, ValueError):
        return None
    return None


def in_service_cgroup(cgroup: str | None = None) -> bool:
    """Whether this process sits in a systemd service unit's own cgroup."""
    path = cgroup if cgroup is not None else _own_cgroup()
    if not path:
        return False
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    return leaf.endswith(".service") and not leaf.startswith("user@")


def _manager_environment() -> tuple[dict, list[str]]:
    """The environment systemd-run needs, and the keys added to reach it."""
    environment = dict(os.environ)
    added = []
    runtime = f"/run/user/{os.getuid()}"
    if not environment.get("XDG_RUNTIME_DIR") and os.path.isdir(runtime):
        environment["XDG_RUNTIME_DIR"] = runtime
        added.append("XDG_RUNTIME_DIR")
    return environment, added


def _probe(environment: dict) -> bool:
    global _probe_ok
    if _probe_ok:
        return True
    try:
        _probe_ok = subprocess.run(
            ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--", "true"],
            capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL,
            env=environment).returncode == 0
    except Exception:  # noqa: BLE001 - any probe failure means no scope
        return False
    return _probe_ok


def scoped_launch(argv: list[str], *, job_id: str) -> tuple[list[str], dict | None]:
    """``(argv, env)`` for one supervisor launch; ``env`` is always None (inherit).

    Off Linux, or without a working user manager, the plain argv is returned,
    except inside a service unit's cgroup, where the job would die with the
    service: that refuses with ScopeUnavailable rather than lose the job.
    """
    if not sys.platform.startswith("linux"):
        return list(argv), None
    environment, added = _manager_environment()
    if shutil.which("systemd-run") is None or not _probe(environment):
        if in_service_cgroup():
            raise ScopeUnavailable("systemd-run could not start a user scope for the job supervisor")
        return list(argv), None
    label = job_id[:16] if re.fullmatch(r"[0-9a-f]{32}", job_id or "") else "job"
    # A variable added only to reach the user manager is set for systemd-run
    # alone and removed again before the supervisor runs, so the launch never
    # passes a whole environment.
    give = [f"{key}={environment[key]}" for key in added]
    strip = [part for key in added for part in ("-u", key)]
    return [*(["env", *give] if give else []),
            "systemd-run", "--user", "--scope", "--quiet", "--collect",
            f"--unit=sandbox-job-{label}-{secrets.token_hex(3)}",
            "--description=Sandbox job supervisor", "--",
            *(["env", *strip] if strip else []), *argv], None


def admitted_release(descriptor_path, own_root: Path, interpreter: str) -> tuple[Path, str]:
    """``(release_root, interpreter)`` for the release that admitted a job.

    ``release.json`` beside the descriptor (written at admission) wins when it is
    another complete release directory; the interpreter maps to the same
    relative path inside it when it lives in this release (a venv), else it
    is kept (the system Python the ``sb`` launcher picked).
    """
    try:
        named = json.loads((Path(descriptor_path).parent / "release.json").read_text())
    except (OSError, ValueError):
        return own_root, interpreter
    if not isinstance(named, str) or not named.startswith("/"):
        return own_root, interpreter
    root = Path(named)
    if root == own_root or not (root / "sandbox" / "jobs" / "supervisor.py").is_file():
        return own_root, interpreter
    own = str(own_root).rstrip("/") + "/"
    if interpreter.startswith(own):
        mapped = str(root) + "/" + interpreter[len(own):]
        if os.access(mapped, os.X_OK):
            interpreter = mapped
    return root, interpreter
