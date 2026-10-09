"""Runtime-side range allocation around a stack's Compose lifecycle.

Spec 063 research R4 (revised): admission before deploy stays count-only; the
host that runs the stack allocates before its first ``up``. The range program
runs locally against this host's ``$SANDBOX_HOME`` (the same program the
controller drives over SSH), allocations are owned by the instance
(owner kind ``instance``, ``instance:<name>``) and attributed to the
workspace the instance's root belongs to, so the exhaustion table names a
``workspace release`` target. A volume-removing ``down`` releases them, which
also covers workspace release, reap and retention since all destroy their
instances. With no range state on this host nothing runs.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from sandbox.remote_network import override
from sandbox.remote_network.ranges import RangeError
from sandbox.remote_network.store import TIMEOUT_SECONDS, RangeStore

EXHAUSTED = "docker_network_subnet_exhausted"
_INSTANCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,118}")
_WORKSPACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def state_file(home: Path) -> Path:
    return Path(home) / "runtime" / "network-ranges" / "state.json"


def owner_id(instance: str) -> str:
    if not isinstance(instance, str) or not _INSTANCE.fullmatch(instance):
        raise RangeError("range_request_invalid", "instance name cannot own a range allocation")
    return f"instance:{instance}"


class _LocalRunner:
    """Runs the range program on this host, in place of ``ssh_run``."""

    def __init__(self, home: Path):
        self.home = Path(home)

    def __call__(self, _entry, command, timeout=TIMEOUT_SECONDS):
        env = dict(os.environ, SANDBOX_HOME=str(self.home))
        return subprocess.run(["sh", "-c", command], env=env, capture_output=True,
                              text=True, timeout=timeout)


class RangeRuntime:
    def __init__(self, home: Path, *, store: RangeStore, overrides: Path,
                 compose_config: Callable[[str], dict],
                 workspace_of: Callable[[str], str | None] = lambda _instance: None):
        self.home = Path(home)
        self.store = store
        self.overrides = Path(overrides)
        self.compose_config = compose_config
        self.workspace_of = workspace_of

    def active(self) -> bool:
        return state_file(self.home).is_file()

    def override_path(self, instance: str) -> Path:
        owner_id(instance)
        return self.overrides / f"{instance}.yml"

    def compose_args(self, instance: str) -> list[str]:
        path = self.override_path(instance)
        return ["-f", str(path)] if path.is_file() else []

    def prepare(self, instance: str) -> dict | None:
        """Allocate the stack's created networks and write its override.

        Returns secret-free evidence (allocation ids, covered and outside-range
        network keys), or ``None`` when this host has no ranges. Exhaustion
        refuses before any override is written, so no network is created.
        """
        if not self.active():
            return None
        owner = owner_id(instance)
        path = self.override_path(instance)
        config = self.compose_config(instance)
        created, outside = override.compose_networks(config)
        if not created:
            path.unlink(missing_ok=True)
            return {"covered": [], "outside_range": outside, "granted": []}
        names = override.docker_names(config, created)
        workspace = self.workspace_of(instance)
        if not isinstance(workspace, str) or not _WORKSPACE.fullmatch(workspace):
            workspace = owner
        result = self.store.allocate(owner_kind="instance", owner_id=owner, workspace_id=workspace,
                                     networks=[names[key] for key in created])
        if result["no_range"]:
            path.unlink(missing_ok=True)
            return None
        if result["exhausted"] or not result["granted"]:
            raise RangeError(EXHAUSTED, "no free subnet remains in the development ranges; "
                             "release a workspace or assign another range",
                             allocation_table=result["table"])
        by_name = {grant["network"]: grant for grant in result["granted"]}
        planned = override.plan(config, {key: by_name.get(names[key], {}).get("subnet")
                                         for key in created if names[key] in by_name})
        self.overrides.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(planned["text"])
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        return {"covered": planned["covered"], "outside_range": planned["outside_range"],
                "granted": [by_name[names[key]]["allocation_id"] for key in created]}

    def release(self, instance: str) -> int:
        """Free the instance's allocations after its networks are gone."""
        path = self.override_path(instance)
        if not self.active():
            path.unlink(missing_ok=True)
            return 0
        released = self.store.release_owner(owner_id=owner_id(instance))
        path.unlink(missing_ok=True)
        return released


def release_workspaces(names, home: Path | None = None) -> int:
    """Free every allocation attributed to reclaimed workspaces (research R5).

    Reap removes deployment roots and their containers directly, without a
    ``compose down``, so the instance hook never runs there. Instance
    allocations carry the deployment-root name as ``workspace_id``.
    """
    if home is None:
        from sandbox.core._paths import _sandbox_base
        home = _sandbox_base()
    if not state_file(home).is_file():
        return 0
    store = RangeStore({"name": "local"}, _LocalRunner(Path(home)))
    return sum(store.release_owner(workspace_id=name) for name in sorted(set(names))
               if isinstance(name, str) and _WORKSPACE.fullmatch(name))


def default_runtime(*, compose_config: Callable[[str], dict],
                    workspace_of: Callable[[str], str | None] = lambda _instance: None,
                    ) -> RangeRuntime | None:
    """This host's range runtime, or ``None`` when it holds no range state."""
    from sandbox.core._paths import _sandbox_base
    from sandbox.remote_runtime.protocol import local_protocol

    home = _sandbox_base()
    if not state_file(home).is_file():
        return None
    store = RangeStore({"name": "local"}, _LocalRunner(home), installed_protocol=local_protocol())
    return RangeRuntime(home, store=store,
                        overrides=home / "runtime" / "network-ranges" / "overrides",
                        compose_config=compose_config, workspace_of=workspace_of)
