"""The remote's follow store: declarations, state, locks and audit (spec 089).

Layout under ``$SANDBOX_HOME/runtime/hosting/follow`` (every directory 0700,
every file 0600)::

    follows/<name>.json   declaration plus state
    audit.jsonl           one line per owner command (refusals too), attempt start and outcome
    keys/github-<hash>    deploy keys, one per repository URL (the private file is never read here)
    known_hosts           the pinned GitHub host key
    work/<name>/repos/    the follow's own clones
    locks/                host, timer units, per-follow state and per-follow apply locks
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Iterator

from sandbox.hosting.follow.spec import FollowError, NAME_RE

SCHEMA = 1
AUDIT_EVENTS = frozenset({
    "add", "replace", "remove", "pause", "resume", "run_now", "key", "status",
    "attempt_started", "attempt_submitted", "attempt_finished", "auto_pause", "refused",
})


def default_root() -> Path:
    from sandbox.core import _paths
    return Path(_paths.RUNTIME_DIR) / "hosting" / "follow"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def empty_state() -> dict:
    return {"paused": False, "pause_reason": None, "applied": None, "failed_heads": None,
            "retry": False, "attempt": None, "last_check": None, "stack_seen": False}


class FollowStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else default_root()

    # ------------------------------------------------------------ layout
    @property
    def follows(self) -> Path:
        return self.root / "follows"

    @property
    def keys(self) -> Path:
        return self.root / "keys"

    @property
    def locks(self) -> Path:
        return self.root / "locks"

    @property
    def known_hosts(self) -> Path:
        return self.root / "known_hosts"

    @property
    def audit_path(self) -> Path:
        return self.root / "audit.jsonl"

    def work_dir(self, name: str) -> Path:
        return self.root / "work" / self._name(name)

    def repo_dir(self, name: str, repo: str) -> Path:
        return self.work_dir(name) / "repos" / repo

    def ensure(self) -> None:
        old = os.umask(0o077)
        try:
            for path in (self.root, self.follows, self.keys, self.locks, self.root / "work"):
                path.mkdir(parents=True, exist_ok=True)
                if path.is_symlink():
                    raise FollowError("follow_store_unsafe", f"{path} is a symlink")
                os.chmod(path, 0o700)
        finally:
            os.umask(old)

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise FollowError("follow_name_invalid", "invalid follow name")
        return name

    # ------------------------------------------------------------- locks
    @contextlib.contextmanager
    def _flock(self, filename: str, *, blocking: bool, timeout: float = 30.0) -> Iterator[bool]:
        self.ensure()
        path = self.locks / filename
        descriptor = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if not blocking or time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
            yield acquired
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @contextlib.contextmanager
    def lock(self, name: str) -> Iterator[None]:
        """Serialize reads-then-writes of one follow's record."""
        with self._flock(f"{self._name(name)}.state", blocking=True) as acquired:
            if not acquired:
                raise FollowError("follow_store_busy", f"follow {name} is locked; try again")
            yield

    def host_lock(self):
        """Non-blocking: one poll at a time on this host."""
        return self._flock("tick", blocking=False)

    @contextlib.contextmanager
    def units_lock(self) -> Iterator[None]:
        """Serialize "is any follow left?" with installing or removing the timer."""
        with self._flock("units", blocking=True, timeout=60) as acquired:
            if not acquired:
                raise FollowError("follow_store_busy", "the follower timer is being changed; try again")
            yield

    def apply_lock(self, name: str, *, wait: float = 0.0):
        """Held by an attempt job for its whole run; a probe holds it for an instant."""
        return self._flock(f"{self._name(name)}.apply", blocking=wait > 0, timeout=wait)

    def apply_running(self, name: str) -> bool:
        with self.apply_lock(name) as acquired:
            return not acquired

    # ----------------------------------------------------------- records
    def _path(self, name: str) -> Path:
        return self.follows / f"{self._name(name)}.json"

    def names(self) -> list[str]:
        if not self.follows.is_dir():
            return []
        return sorted(path.stem for path in self.follows.glob("*.json")
                      if NAME_RE.fullmatch(path.stem))

    def read(self, name: str) -> dict | None:
        path = self._path(name)
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            raise FollowError("follow_record_unreadable", f"follow {name} record is unreadable") from None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA or data.get("name") != name:
            raise FollowError("follow_record_unreadable", f"follow {name} record has an unknown format")
        return data

    def write(self, record: dict) -> None:
        self.ensure()
        record["schema"] = SCHEMA
        record["updated_at"] = utc_now()
        path = self._path(record["name"])
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = os.open(str(temporary), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(descriptor, (json.dumps(record, sort_keys=True, indent=1) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    # ------------------------------------------------------------- audit
    def audit(self, event: str, name: str, *, actor: str, **fields) -> None:
        if event not in AUDIT_EVENTS:
            raise ValueError(f"unknown audit event {event}")
        self.ensure()
        line = {"at": utc_now(), "event": event, "follow": name, "actor": actor, **fields}
        descriptor = os.open(str(self.audit_path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, (json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def audit_tail(self, name: str, limit: int = 10) -> list[dict]:
        try:
            lines = self.audit_path.read_text().splitlines()
        except FileNotFoundError:
            return []
        rows = []
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("follow") == name:
                rows.append(row)
                if len(rows) >= limit:
                    break
        return list(reversed(rows))
