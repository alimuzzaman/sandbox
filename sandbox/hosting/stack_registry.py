"""Controller-side record of rendered stacks and their named ports (spec 071).

One JSON file under ``$SANDBOX_HOME/runtime/stacks/`` holds every stack, keyed
``<remote>/<slug>``. Writers serialize on an flock; readers that only list do
not take it. Port allocation is pure so it can be tested without a remote.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping, TypedDict

from sandbox.core import _paths
from sandbox.core._hosting import STACK_PORT_END, STACK_PORT_START, HostingRefusal

PORT_INVALID = "stack_port_invalid"
PORT_UNAVAILABLE = "stack_port_unavailable"
REGISTRY_UNREADABLE = "stack_registry_unreadable"
STACK_PORT_RANGE = range(STACK_PORT_START, STACK_PORT_END)
_PIN_MIN, _PIN_MAX = 1024, 65535


class StackRegistryError(HostingRefusal):
    pass


class LastApply(TypedDict, total=False):
    request_id: str
    job_id: str | None
    submitted_at: int
    status: str
    error: str


class StackRecord(TypedDict, total=False):
    remote: str
    slug: str
    project: str
    environment: str
    template: str
    checkout: str
    ref: str
    ref_branch: str
    commit: str
    hostnames: list[str]
    ports: dict[str, int]
    manifest: str
    worktrees: list[str]
    created_at: int
    updated_at: int
    last_apply: LastApply


def stacks_dir() -> Path:
    return _paths.RUNTIME_DIR / "stacks"


def stack_dir(slug: str) -> Path:
    return stacks_dir() / slug


def registry_path() -> Path:
    return stacks_dir() / "registry.json"


def record_key(remote_name: str, slug: str) -> str:
    return f"{remote_name}/{slug}"


def _empty() -> dict:
    return {"version": 1, "stacks": {}}


def read(path: Path | None = None) -> dict:
    path = path or registry_path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StackRegistryError(REGISTRY_UNREADABLE, f"stack registry is unreadable: {exc}") from None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("stacks"), dict):
        raise StackRegistryError(REGISTRY_UNREADABLE, "stack registry has an unknown format")
    return data


def _write(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="registry-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class Registry:
    """A locked, writable view of the registry."""

    def __init__(self, data: dict, path: Path):
        self.data = data
        self._path = path

    def get(self, remote_name: str, slug: str) -> StackRecord | None:
        return self.data["stacks"].get(record_key(remote_name, slug))

    def put(self, record: StackRecord) -> None:
        self.data["stacks"][record_key(record["remote"], record["slug"])] = record
        _write(self.data, self._path)

    def delete(self, remote_name: str, slug: str) -> None:
        self.data["stacks"].pop(record_key(remote_name, slug), None)
        _write(self.data, self._path)


@contextmanager
def locked(path: Path | None = None) -> Iterator[Registry]:
    path = path or registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / "registry.lock", "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield Registry(read(path), path)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def records_for_remote(data: dict, remote_name: str) -> list[StackRecord]:
    return [record for record in data["stacks"].values()
            if isinstance(record, dict) and record.get("remote") == remote_name]


def records_for_slug(data: dict, slug: str) -> list[StackRecord]:
    return [record for record in data["stacks"].values()
            if isinstance(record, dict) and record.get("slug") == slug]


def parse_pins(values: list[str] | None, declared: tuple[str, ...]) -> dict[str, int]:
    """Parse repeated ``--port NAME=N`` values against the template's ports."""
    pins: dict[str, int] = {}
    for value in values or []:
        name, sep, number = str(value).partition("=")
        if not sep or not name or not number.isdigit():
            raise StackRegistryError(PORT_INVALID, f"--port must be NAME=PORT, got {value!r}")
        if name not in declared:
            raise StackRegistryError(
                PORT_INVALID, f"--port {name}: the template's stack.ports does not declare it")
        port = int(number)
        if not _PIN_MIN <= port <= _PIN_MAX:
            raise StackRegistryError(PORT_INVALID, f"--port {name}={port} must be between 1024 and 65535")
        if name in pins:
            raise StackRegistryError(PORT_INVALID, f"--port {name} is given more than once")
        pins[name] = port
    if len(set(pins.values())) != len(pins):
        raise StackRegistryError(PORT_INVALID, "two --port pins name the same port")
    return pins


def allocate_ports(*, remote_name: str, slug: str, names: tuple[str, ...],
                   existing: Mapping[str, int], pins: Mapping[str, int],
                   other_stacks: Mapping[int, str], reserved: set[int],
                   bound: Mapping[int, set[str]], own_compose_project: str) -> dict[str, int]:
    """Choose one port per name.

    ``other_stacks`` maps ports recorded for other stacks on the remote to their
    slug; ``reserved`` holds recorded hosting loopback ports; ``bound`` maps each
    port listening on the remote to the Compose projects publishing it (empty
    for a non-Docker listener). A port bound only by ``own_compose_project`` is
    this stack's own and stays usable.
    """
    def foreign_binding(port: int) -> bool:
        owners = bound.get(port)
        return owners is not None and owners != {own_compose_project}

    def refuse_if_taken(name: str, port: int, how: str) -> None:
        if port in other_stacks:
            raise StackRegistryError(
                PORT_UNAVAILABLE, f"{how} port {name}={port} is owned by stack {other_stacks[port]}")
        if port in reserved:
            raise StackRegistryError(
                PORT_UNAVAILABLE, f"{how} port {name}={port} is a recorded hosting loopback port")
        if foreign_binding(port):
            owners = ", ".join(sorted(bound[port])) or "a non-Docker process"
            raise StackRegistryError(
                PORT_UNAVAILABLE, f"{how} port {name}={port} is already bound on the remote by {owners}")

    chosen: dict[str, int] = {}
    for name in names:
        if name in pins:
            refuse_if_taken(name, pins[name], "pinned")
            chosen[name] = pins[name]
        elif isinstance(existing.get(name), int):
            refuse_if_taken(name, existing[name], "recorded")
            chosen[name] = existing[name]
    for name in names:
        if name in chosen:
            continue
        span = len(STACK_PORT_RANGE)
        offset = int(hashlib.sha256(f"{remote_name}/{slug}/{name}".encode()).hexdigest()[:8], 16) % span
        for index in range(span):
            candidate = STACK_PORT_RANGE.start + (offset + index) % span
            if (candidate in other_stacks or candidate in reserved or foreign_binding(candidate)
                    or candidate in chosen.values()):
                continue
            chosen[name] = candidate
            break
        else:
            raise StackRegistryError(
                PORT_UNAVAILABLE,
                f"no free stack port for {name} in {STACK_PORT_RANGE.start}-{STACK_PORT_RANGE.stop - 1}")
    if len(set(chosen.values())) != len(chosen):
        raise StackRegistryError(PORT_UNAVAILABLE, "two stack ports resolved to the same port")
    return {name: chosen[name] for name in names}


def ports_of_other_stacks(data: dict, remote_name: str, slug: str) -> dict[int, str]:
    ports: dict[int, str] = {}
    for record in records_for_remote(data, remote_name):
        if record.get("slug") == slug:
            continue
        for port in (record.get("ports") or {}).values():
            if isinstance(port, int):
                ports[port] = str(record.get("slug"))
    return ports
