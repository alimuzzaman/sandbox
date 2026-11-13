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
import time
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
    # The commit this apply was asked to deliver. `sb host apply` copies it to
    # the record's ``commit`` once the apply completes (record_delivery); readers
    # also treat it as delivered once the apply's job succeeded (delivered_commit).
    commit: str


class SiteLinkRef(TypedDict, total=False):
    """A WordPress site `sb deploy --stack` made for this stack (spec 076)."""
    hostname: str
    instance: str
    label: str
    commit: str | None
    recorded_at: int


class StackRecord(TypedDict, total=False):
    remote: str
    slug: str
    project: str
    environment: str
    template: str
    checkout: str
    ref: str
    ref_branch: str
    # The last commit an apply proved delivered; None until the first one is.
    # The commit the latest `stack up` asked for is ``last_apply.commit``.
    commit: str | None
    hostnames: list[str]
    uses: list[str]  # absent on older controller records
    ports: dict[str, int]
    manifest: str
    worktrees: list[str]
    created_at: int
    updated_at: int
    last_apply: LastApply
    sites: list[SiteLinkRef]
    # Spec 077: a mirror of the remote's lifetime record for this copy.
    copy_id: str
    owner: dict
    lifetime: str
    lifetime_source: str
    ttl_seconds: int | None
    expires_at: str | None
    repo: str
    dev_branch: str
    canonical: bool


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


def read_exact_record(remote_name: str, slug: str, *, budget) -> dict | None:
    """Bounded recorded-only selector; no manifest or runtime requirement."""
    import stat
    path = registry_path()
    budget.check()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022 or info.st_size > 2 * 1024 * 1024):
            raise ValueError('stack binding unavailable')
        raw = handle.read(2 * 1024 * 1024 + 1)
    budget.check()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('stack binding bounds')
    data = json.loads(raw)
    if (type(data) is not dict or type(data.get('version')) is not int
            or data['version'] != 1 or type(data.get('stacks')) is not dict):
        raise ValueError('stack binding invalid')
    record = data['stacks'].get(record_key(remote_name, slug))
    if record is not None and (type(record) is not dict or record.get('remote') != remote_name or record.get('slug') != slug):
        raise ValueError('stack binding mismatch')
    budget.check()
    return record


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
def locked(path: Path | None = None, *, timeout_seconds: float | None = None) -> Iterator[Registry]:
    path = path or registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / "registry.lock", "a+") as handle:
        if timeout_seconds is None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('stack registry is busy')
                    time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        try:
            yield Registry(read(path), path)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


APPLY_SUCCEEDED = "succeeded"


def delivered_commit(record: Mapping, last_apply_status: str | None) -> str | None:
    """The commit a stack's server is known to run.

    ``last_apply_status`` is the last apply's status as refined from its durable
    job. A succeeded apply's ``last_apply.commit`` counts even when the apply's
    own ``record_delivery`` write was missed, so readers agree with
    ``last_apply``. Otherwise the record's ``commit`` stands. A record written
    before ``last_apply.commit`` existed keeps its ``commit``, which may name a
    commit that was never delivered until the next apply completes.
    """
    requested = (record.get("last_apply") or {}).get("commit")
    if last_apply_status == APPLY_SUCCEEDED and requested:
        return requested
    return record.get("commit")


def record_delivery(remote_name: str, slug: str, *, request_id: str | None, commit: str,
                    now: int | None = None, path: Path | None = None) -> bool:
    """Move a stack's ``commit`` to the commit its apply just delivered.

    `sb host apply` calls this when the apply completes. Only the apply the
    record waits for counts: ``last_apply.request_id`` and ``last_apply.commit``
    must both match, so a manual or older apply of the same manifest never
    moves the record. Returns whether the record changed.
    """
    if not request_id:
        return False
    with locked(path) as registry:
        record = registry.data["stacks"].get(record_key(remote_name, slug))
        last = (record or {}).get("last_apply") or {}
        if (not isinstance(record, dict) or last.get("request_id") != request_id
                or last.get("commit") != commit or record.get("commit") == commit):
            return False
        record["commit"] = commit
        record["updated_at"] = int(time.time()) if now is None else now
        registry.put(record)
        return True


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
