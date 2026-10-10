"""Runtime-side range allocation around a stack's Compose lifecycle.

Spec 063 research R4 (revised): admission before deploy stays count-only; the
host that runs the stack allocates before its first ``up``. The range program
runs locally against this host's ``$SANDBOX_HOME`` (the same program the
controller drives over SSH), allocations are owned by the instance
(owner kind ``instance``, ``instance:<name>``) and attributed to the
workspace the instance's root belongs to, so the exhaustion table names a
``workspace release`` target. A volume-removing ``down`` releases them; reap
(which ``workspace release`` makes eligible) removes the stack networks and
releases by workspace. With no range state on this host nothing runs.
"""
from __future__ import annotations

import fcntl
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
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

    @contextmanager
    def lifecycle(self, instance: str, *, exclusive: bool = False):
        """Serialize one instance's prepare -> Compose -> release.

        Commands that create networks hold the lock shared for their whole run,
        so concurrent ``up``/``run`` calls proceed together; a volume-removing
        ``down`` holds it exclusively across Docker teardown and the release,
        so no ``up`` can reuse a grant that is about to be freed.
        """
        path = self.override_path(instance).with_suffix(".lock")
        self.overrides.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            os.close(descriptor)

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
        # A private temp name per writer: concurrent prepares of one instance
        # (shared lifecycle lock) each publish a complete file atomically.
        descriptor, temp = tempfile.mkstemp(dir=self.overrides, prefix=f".{instance}.",
                                            suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(planned["text"])
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
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


def describe(error: RangeError) -> str:
    """One operator-facing refusal: code, message, and for exhaustion the
    owners holding subnets with a release command each (never a subnet).

    Release only marks the workspace's lease; the reap that follows tears its
    stacks down and frees the subnets.
    """
    lines = [f"{error.code}: {error}"]
    seen = []
    for row in error.data.get("allocation_table") or []:
        workspace = row.get("workspace_id") if isinstance(row, dict) else None
        if isinstance(workspace, str) and _WORKSPACE.fullmatch(workspace) and workspace not in seen:
            seen.append(workspace)
            lines.append(f"  held by workspace {workspace}: ./sb workspace release {workspace}"
                         " && ./sb workspace reap --confirm")
    return "\n".join(lines)


def _remove_network(name: str) -> bool:
    """Remove one stack network; true once Docker no longer has it."""
    try:
        result = subprocess.run(["docker", "network", "rm", name], capture_output=True,
                                text=True, timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 or bool(
        re.search(r"no such network|not found", result.stderr or "", re.IGNORECASE))


def release_workspaces(names, home: Path | None = None, *,
                       remove_network: Callable[[str], bool] = _remove_network) -> int:
    """Free every allocation attributed to reclaimed workspaces (research R5).

    Reap removes deployment roots and their containers directly, without a
    ``compose down``, so the instance hook never runs there and the stack
    networks are left behind. Each granted network is removed first; a
    workspace is released only when all of its networks are gone, since a
    surviving network still occupies its subnet and the allocator would skip
    it as foreign. Instance allocations carry the deployment-root name as
    ``workspace_id``.
    """
    if home is None:
        from sandbox.core._paths import _sandbox_base
        home = _sandbox_base()
    if not state_file(home).is_file():
        return 0
    store = RangeStore({"name": "local"}, _LocalRunner(Path(home)))
    wanted = {name for name in names if isinstance(name, str) and _WORKSPACE.fullmatch(name)}
    networks: dict[str, set[str]] = {name: set() for name in wanted}
    for row in store.list()["allocations"]:
        if row.get("workspace_id") in wanted and isinstance(row.get("network"), str):
            networks[row["workspace_id"]].add(row["network"])
    released = 0
    for name in sorted(wanted):
        if all([remove_network(network) for network in sorted(networks[name])]):
            released += store.release_owner(workspace_id=name)
    return released


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
