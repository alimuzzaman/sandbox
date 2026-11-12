"""Owner-panel operation journal and the actual cross-controller apply lease.

Only public identities, original operation state and grant ids enter this store.
Bearer URLs, page payloads and WordPress keys are never persisted here.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

_MAX_OPERATIONS = 256
# One list or session-revoke operation can run when retained operations fill the journal.
_MAX_ROWS = _MAX_OPERATIONS + 1


def _directory(path: Path, *, private=False) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("owner share directory is linked")
    info = path.stat()
    if info.st_uid != os.geteuid() or (private and stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("owner share directory is not private")


def _file(path: Path, *, private=True) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or (stat.S_IMODE(info.st_mode) != 0o600 if private else info.st_mode & 0o022):
        os.close(descriptor)
        raise ValueError("owner share file is not private")
    return descriptor


class Store:
    def __init__(self, home: str):
        self.home = home
        self.root = Path(home) / "runtime" / "share-links" / "owner-panel"
        self.db = None

    def __enter__(self):
        _directory(self.root, private=True)
        self.lock = _file(self.root / "operations.lock")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.root / "operations.sqlite"
            descriptor = _file(path)
            os.close(descriptor)
            self.db = sqlite3.connect(str(path), timeout=0)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
            self.db.commit()
            self.prune()
            return self
        except BaseException:
            if self.db is not None:
                self.db.close()
            os.close(self.lock)
            raise

    def __exit__(self, *args):
        self.db.close()
        os.close(self.lock)

    def rows(self, remote, stack):
        rows = self.db.execute("SELECT record FROM operations LIMIT ?", (_MAX_ROWS + 1,)).fetchall()
        if len(rows) > _MAX_ROWS:
            raise ValueError("owner share journal is full")
        result = []
        for (text,) in rows:
            if len(text) > 262144:
                raise ValueError("owner share operation exceeds bound")
            row = json.loads(text)
            if row["remote"] == remote and row["stack"] == stack:
                result.append(row)
        return result

    def save(self, row):
        def public(value):
            if isinstance(value, dict):
                if set(value) & {"url", "token", "key", "page", "payload"}:
                    raise ValueError("private share material is not journal data")
                for child in value.values():
                    public(child)
            elif isinstance(value, list):
                for child in value:
                    public(child)
        public(row)
        text = json.dumps(row, sort_keys=True, separators=(",", ":"))
        limit = _MAX_ROWS if row.get("kind") in ("observation", "session-revoke") else _MAX_OPERATIONS
        if len(text) > 262144 or (self.db.execute("SELECT count(*) FROM operations").fetchone()[0] >= limit
                and not self.db.execute("SELECT 1 FROM operations WHERE id=?", (row["id"],)).fetchone()):
            raise ValueError("owner share journal is full")
        self.db.execute("INSERT OR REPLACE INTO operations VALUES (?, ?)", (row["id"], text))
        self.db.commit()

    def prune(self):
        """Discard only closed records whose access or cleanup obligations ended."""
        now = time.time()
        rows = self.db.execute("SELECT id, record FROM operations LIMIT ?", (_MAX_ROWS + 1,)).fetchall()
        if len(rows) > _MAX_ROWS:
            raise ValueError("owner share journal is full")
        for identity, text in rows:
            if len(text) > 262144:
                raise ValueError("owner share operation exceeds bound")
            row = json.loads(text)
            if (row.get("operation") or {}).get("state") not in ("completed", "settled"):
                continue
            grants = row.get("grants")
            closed = False
            if row.get("phase") == "revoked" and isinstance(grants, list) and grants:
                closed = all(item.get("status") == "revoked" for item in grants)
            elif row.get("phase") == "active" and isinstance(grants, list) and grants:
                # Gate sessions, pending sign-ins and WP signing keys all stop
                # authorizing access at their parent grant's recorded expiry.
                closed = all(item.get("status") in ("live", "revoked")
                    and type(item.get("expires_at")) is int and item["expires_at"] <= now
                    for item in grants)
            elif row.get("phase") in ("admin-completed", "admin-unreported") and grants == []:
                closed = type(row.get("expires_at")) is int and row["expires_at"] <= now
            elif (row.get("phase") == "observed" and grants == []
                    and row.get("kind") in ("observation", "session-revoke")):
                # A crash between lease release and final deletion must not
                # permanently consume the reserved management slot.
                closed = True
            if closed and self._leases_released(row):
                self.db.execute("DELETE FROM operations WHERE id=?", (identity,))
        self.db.commit()

    def _leases_released(self, row):
        # A crash after the final journal write can precede lease release. Keep
        # its original bindings available for recovery even after access expires.
        from sandbox.hosting.target_lease import lease_path
        bindings = row.get("bindings")
        if not isinstance(bindings, list) or not bindings:
            return False
        for binding in bindings:
            snapshot = binding.get("snapshot") or {}
            if not snapshot.get("project") or not snapshot.get("environment"):
                return False
            path = Path(lease_path(self.home, snapshot["project"], snapshot["environment"]))
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                return False
            return False
        return True

    def finish_observation(self, row):
        if (row["phase"] != "observed" or row["grants"]
                or row.get("kind") not in ("observation", "session-revoke")
                or row["operation"]["state"] not in ("completed", "settled")
                or not self._leases_released(row)):
            raise ValueError("original observation is not completed")
        self.db.execute("DELETE FROM operations WHERE id=?", (row["id"],))
        self.db.commit()


@contextmanager
def apply_lease(home: str, project: str, environment: str, operation_id: str, *, resume=False):
    """Use target_lease's actual file/lock protocol, including other controllers.

    Unknown effects retain the lease. A later request cannot silently adopt it.
    Cleanup after uncertainty requires the original in-process handle to settle.
    """
    from sandbox.hosting.target_lease import lease_path
    path = Path(lease_path(home, project, environment))
    _directory(path.parent)
    lock = _file(Path(str(path) + ".lock"), private=False)
    token, owner = secrets.token_hex(16), operation_id.removeprefix("a-")
    retained = {"unknown": False}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.is_symlink():
            raise ValueError("owner target lease is linked")
        if path.exists() and path.stat().st_size:
            descriptor = _file(path)
            with os.fdopen(descriptor) as handle:
                previous = handle.read(256).split()
            if not resume or len(previous) != 3 or previous[1] != owner:
                raise ValueError("another operation holds the target apply lease")
            token = previous[0]
        descriptor = _file(path)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(f"{token} {owner} {int(time.time())}\n")
            handle.flush()
            os.fsync(handle.fileno())
        fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            yield retained
        finally:
            if not retained["unknown"]:
                fcntl.flock(lock, fcntl.LOCK_EX)
                # Read only this mechanism's own lease through its checked fd.
                descriptor = _file(path)
                with os.fdopen(descriptor) as handle:
                    current = handle.read(256).split()
                if current[:2] == [token, owner]:
                    path.unlink()
    finally:
        os.close(lock)
