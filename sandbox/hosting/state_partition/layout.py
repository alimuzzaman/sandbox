"""Per-target controller hosting state (spec 060 FR-025, R8, R9).

One document per target at ``runtime/host-targets/<sha16(state_key)>.json``:
``{"version": 2, "state_key": key, "record": {...}}``. The record keeps
today's ``hosts.json`` per-target fields unchanged in meaning. A corrupt or
unreadable file affects only its own target: ``read`` raises for that key,
``iterate`` reports it and carries on.

``runtime/hosts-conversion.json`` records, per remote, whether its targets
have moved here (``converted``) or are moving (``in_progress``). Until a remote
is converted its records stay in ``hosts.json``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 2
CONVERSION_VERSION = 1
IN_PROGRESS = "in_progress"
CONVERTED = "converted"
_NAME = re.compile(r"[0-9a-f]{16}\.json")
_REMOTE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class TargetStateError(RuntimeError):
    """One target's state document is unreadable or malformed."""

    def __init__(self, message: str, *, state_key: str | None = None, file: str | None = None):
        super().__init__(message)
        self.state_key = state_key
        self.file = file


def digest16(state_key: str) -> str:
    return hashlib.sha256(state_key.encode("utf-8")).hexdigest()[:16]


def remote_of(state_key: str) -> str:
    return state_key.split("/", 1)[0]


class PartitionStore:
    """Per-target documents and the conversion record under one runtime dir."""

    def __init__(self, runtime_dir: str | os.PathLike | None = None):
        if runtime_dir is None:
            from sandbox.core._paths import RUNTIME_DIR
            runtime_dir = RUNTIME_DIR
        self.runtime_dir = Path(runtime_dir)
        self.root = self.runtime_dir / "host-targets"
        self.conversion_path = self.runtime_dir / "hosts-conversion.json"

    # -- files --------------------------------------------------------------

    def path_for(self, state_key: str) -> Path:
        return self.root / f"{digest16(state_key)}.json"

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = os.lstat(self.root)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise TargetStateError("per-target state directory is not owned by this user")
        os.chmod(self.root, 0o700)

    @staticmethod
    def _atomic_write(path: Path, value: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(value, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @contextmanager
    def target_write_lock(self, state_key: str, *, timeout_seconds: float = 30):
        """A short per-target flock around one read-modify-write of its file."""
        self._ensure_root()
        path = self.root / f".{digest16(state_key)}.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("operation_busy") from None
                    time.sleep(0.02)
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _parse(path: Path) -> dict:
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise TargetStateError("per-target state file is not a regular owned file",
                                       file=path.name)
            value = json.loads(path.read_text())
        except TargetStateError:
            raise
        except (OSError, ValueError) as exc:
            raise TargetStateError(f"per-target state is unreadable: {type(exc).__name__}",
                                   file=path.name) from None
        if not isinstance(value, dict) or value.get("version") != VERSION \
                or not isinstance(value.get("state_key"), str) \
                or not isinstance(value.get("record"), dict) \
                or path.name != f"{digest16(value['state_key'])}.json":
            raise TargetStateError("per-target state format is invalid", file=path.name)
        return value

    def read(self, state_key: str) -> dict | None:
        """The target's record, None when absent; raises only for this key."""
        path = self.path_for(state_key)
        if not path.exists() and not path.is_symlink():
            return None
        try:
            value = self._parse(path)
        except TargetStateError as exc:
            exc.state_key = state_key
            raise
        if value["state_key"] != state_key:
            raise TargetStateError("per-target state names another target",
                                   state_key=state_key, file=path.name)
        return value["record"]

    def write(self, state_key: str, record: dict) -> None:
        if not isinstance(record, dict):
            raise TargetStateError("per-target record must be a mapping", state_key=state_key)
        self._ensure_root()
        self._atomic_write(self.path_for(state_key),
                           {"version": VERSION, "state_key": state_key, "record": record})

    def delete(self, state_key: str) -> bool:
        try:
            os.unlink(self.path_for(state_key))
        except FileNotFoundError:
            return False
        return True

    def iterate(self) -> list[tuple[str | None, dict | None, TargetStateError | None]]:
        """Every target document: ``(key, record, None)`` or ``(None, None, error)``."""
        if not self.root.is_dir():
            return []
        rows = []
        for name in sorted(os.listdir(self.root)):
            if not _NAME.fullmatch(name):
                continue
            try:
                value = self._parse(self.root / name)
            except TargetStateError as exc:
                rows.append((None, None, exc))
                continue
            rows.append((value["state_key"], value["record"], None))
        return rows

    # -- conversion -----------------------------------------------------------

    def conversion(self) -> dict:
        path = self.conversion_path
        if not path.exists():
            return {"version": CONVERSION_VERSION, "remotes": {}}
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            raise TargetStateError("hosting conversion record is unreadable") from None
        if not isinstance(value, dict) or value.get("version") != CONVERSION_VERSION \
                or not isinstance(value.get("remotes"), dict):
            raise TargetStateError("hosting conversion record format is invalid")
        return value

    def remote_state(self, remote: str) -> str | None:
        entry = self.conversion()["remotes"].get(remote)
        state = entry.get("state") if isinstance(entry, dict) else None
        return state if state in (IN_PROGRESS, CONVERTED) else None

    def converted(self, remote: str) -> bool:
        return self.remote_state(remote) == CONVERTED

    def mark(self, remote: str, state: str, *, step: str | None = None) -> dict:
        if not _REMOTE.fullmatch(remote or "") or state not in (IN_PROGRESS, CONVERTED):
            raise TargetStateError("conversion mark is invalid")
        value = self.conversion()
        entry = value["remotes"].setdefault(remote, {"state": state, "steps_done": [],
                                                     "started_at": int(time.time()),
                                                     "finished_at": None})
        entry["state"] = state
        if step and step not in entry["steps_done"]:
            entry["steps_done"].append(step)
        if state == CONVERTED:
            entry["finished_at"] = int(time.time())
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.conversion_path, value)
        return entry


# -- the composite view the hosting callers load and save ---------------------


class HostState(dict):
    """``{"version", "hosts": {key: record}}`` as callers know it, plus what
    the partition needs to write back only what changed.

    ``partition_snapshot`` maps each converted target loaded from its own file
    to its canonical JSON at load time; ``unreadable`` holds the digests of
    target files that could not be read, which ``persist`` will never
    overwrite from a view that could not see them.
    """

    partition_snapshot: dict
    unreadable: frozenset

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.partition_snapshot = {}
        self.unreadable = frozenset()


def _canonical(record) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _converted(store: PartitionStore) -> set[str]:
    return {remote for remote, entry in store.conversion()["remotes"].items()
            if isinstance(entry, dict) and entry.get("state") == CONVERTED}


def compose(legacy: dict, store: PartitionStore) -> HostState:
    """The legacy document with every converted remote's targets read from
    their own files. With no converted remote this is ``legacy`` unchanged."""
    converted = _converted(store)
    state = HostState(legacy)
    if not converted:
        return state
    hosts = {key: record for key, record in (legacy.get("hosts") or {}).items()
             if remote_of(key) not in converted}
    snapshot, unreadable = {}, set()
    for key, record, error in store.iterate():
        if error is not None:
            unreadable.add((error.file or "")[:16])
        elif remote_of(key) in converted:
            hosts[key] = record
            snapshot[key] = _canonical(record)
    state["hosts"] = hosts
    state.partition_snapshot = snapshot
    state.unreadable = frozenset(unreadable)
    return state


def target_unreadable(state: dict, state_key: str) -> bool:
    return isinstance(state, HostState) and digest16(state_key) in state.unreadable


def persist(state: dict, store: PartitionStore, write_legacy) -> None:
    """Write ``state`` back: unconverted remotes to the legacy document through
    ``write_legacy``, converted targets to their own files (changed ones only
    when ``state`` came from ``compose``), removed converted targets deleted."""
    converted = _converted(store)
    hosts = state.get("hosts") or {}
    legacy = dict(state)
    legacy["hosts"] = {key: record for key, record in hosts.items()
                       if remote_of(key) not in converted}
    snapshot = getattr(state, "partition_snapshot", {})
    for key, record in hosts.items():
        if remote_of(key) not in converted:
            continue
        if target_unreadable(state, key):
            raise TargetStateError("per-target state is unreadable; refusing to replace it",
                                   state_key=key)
        if snapshot.get(key) != _canonical(record):
            with store.target_write_lock(key):
                store.write(key, record)
    for key in snapshot:
        if key not in hosts:
            with store.target_write_lock(key):
                store.delete(key)
    write_legacy(legacy)
