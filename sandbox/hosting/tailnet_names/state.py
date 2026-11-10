"""Controller records for tailnet names (spec 078).

``$SANDBOX_HOME/runtime/tailnet-names/<remote>.json`` (0600) holds what this
controller did per name: never a secret. ``<remote>.lock`` serialises this
controller's runs against one remote without waiting.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sandbox.hosting.tailnet_names.models import (
    BUSY,
    STATE_CORRUPT,
    TailnetNameError,
    TailnetNameRecord,
)

_REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
VERSION = 1


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def default_root() -> Path:
    from sandbox.core._paths import BASE
    return Path(BASE) / "runtime" / "tailnet-names"


class StateStore:
    def __init__(self, root: Path, remote: str) -> None:
        if not _REMOTE.fullmatch(remote or "") or ".." in remote:
            raise ValueError("invalid remote name")
        self.root = Path(root)
        self.path = self.root / f"{remote}.json"
        self.lock_path = self.root / f"{remote}.lock"

    def _ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        # Every write, not only a fresh mkdir: a run that died before this
        # fsync left an entry the retry finds already there.
        _fsync_dir(self.root.parent)

    def load(self) -> dict[str, TailnetNameRecord]:
        """All records; a missing file is empty, an unreadable one is STATE_CORRUPT.

        The file holds DNS ownership, so it is never read as empty by mistake.
        """
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise TailnetNameError(STATE_CORRUPT,
                                   f"cannot read {self.path.name} ({type(exc).__name__})") from None
        try:
            document = json.loads(text)
        except ValueError:
            document = None
        names = document.get("names") if isinstance(document, dict) else None
        if not isinstance(names, dict) or document.get("version") != VERSION:
            raise TailnetNameError(STATE_CORRUPT, f"{self.path.name} is not a version "
                                   f"{VERSION} tailnet-name state file; inspect it before retrying")
        records = {}
        for key, row in names.items():
            record = TailnetNameRecord.parse(row)
            if record.fqdn != key:
                raise TailnetNameError(STATE_CORRUPT, f"{self.path.name} has a mismatched key")
            records[record.fqdn] = record
        return records

    def get(self, fqdn: str) -> TailnetNameRecord | None:
        return self.load().get(fqdn)

    def _write(self, records: dict[str, TailnetNameRecord]) -> None:
        self._ensure_root()
        body = json.dumps({"version": VERSION,
                           "names": {k: v.as_dict() for k, v in sorted(records.items())}},
                          indent=2, sort_keys=True) + "\n"
        fd, staged = tempfile.mkstemp(dir=self.root, prefix=".tailnet-names.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staged, 0o600)
            os.replace(staged, self.path)
        except BaseException:
            if os.path.exists(staged):
                os.unlink(staged)
            raise
        # The rename is durable only once the directory holding it is synced.
        _fsync_dir(self.root)

    def put(self, record: TailnetNameRecord) -> None:
        records = self.load()
        records[record.fqdn] = record
        self._write(records)

    def drop(self, fqdn: str) -> None:
        records = self.load()
        if records.pop(fqdn, None) is not None:
            self._write(records)

    @contextmanager
    def lock(self) -> Iterator[None]:
        self._ensure_root()
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise TailnetNameError(
                        BUSY, "another tailnet-name run on this controller is using this remote"
                    ) from None
                raise
            yield
        finally:
            os.close(fd)
