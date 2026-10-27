"""Controller-local ownership state: identity, private files, journal, retention.

Everything here is non-secret metadata (names, states, ids, digests). The
root is ``$SANDBOX_HOME/runtime/secret-ownership/``: directories 0700, files
0600, owned by the effective uid, never followed through a symlink. Any other
shape is refused rather than repaired.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from pathlib import Path
from typing import Literal

from sandbox.secrets.models import SecretBrokerError
from .models import Destination, NameState, canonical_json

_CONTROLLER_ID = re.compile(r"^[0-9a-f]{32}$")
_QUALIFIED_ID = re.compile(r"^(ap|run)_([0-9a-f]{32})_([0-9a-f]{16})$")
_PROFILE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_STATE_BYTES = 4 * 1024 * 1024
_HEADER_KEYS = frozenset((
    "journal", "journal_id", "controller_id", "profile", "destination_sha256",
    "authority_digest", "created_at",
))
_RECORD_KEYS = frozenset(("seq", "at", "run_id", "approval_id", "name", "state", "reason"))
_STATES = frozenset(state.value for state in NameState)
#: Journals holding any of these states are never removed by retention (FR-052).
RETAINED_STATES = frozenset((
    NameState.ATTEMPTED.value, NameState.ACCEPTANCE_UNKNOWN.value,
    NameState.UNRESOLVED.value, NameState.CONFLICT.value,
))
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


def _unsafe() -> SecretBrokerError:
    return SecretBrokerError("journal_corrupt", "secret ownership state is unsafe")


def _corrupt() -> SecretBrokerError:
    return SecretBrokerError("journal_corrupt", "secret ownership journal is corrupt")


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and missing parents) 0700 and verify it is safe."""
    path = Path(path)
    try:
        # mkdir(parents=True) applies its mode only to the leaf. Create each
        # missing directory explicitly and refuse symlinks in every ancestor.
        for parent in reversed((path, *path.parents)):
            try:
                current = os.lstat(parent)
            except FileNotFoundError:
                try:
                    parent.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                current = os.lstat(parent)
            if not stat.S_ISDIR(current.st_mode):
                raise _unsafe()
        info = os.lstat(path)
    except OSError:
        raise _unsafe() from None
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise _unsafe()
    return path


def _check_fd(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise _unsafe()
    return info


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW | _CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]


def read_private_file(path: Path, *, limit: int = _MAX_STATE_BYTES) -> bytes:
    """Read one owner-only regular file without following links."""
    try:
        ensure_private_dir(Path(path).parent)
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
    except FileNotFoundError:
        raise
    except OSError:
        raise _unsafe() from None
    try:
        info = _check_fd(fd)
        if info.st_size > limit:
            raise _unsafe()
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _unsafe()
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def write_private_file(path: Path, data: bytes, *, exclusive: bool = True) -> None:
    """Durably write an owner-only file; ``exclusive`` refuses an existing one."""
    path = Path(path)
    ensure_private_dir(path.parent)
    if exclusive:
        target = path
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
    else:
        target = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        raise
    except OSError:
        raise _unsafe() from None
    try:
        try:
            _check_fd(fd)
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not exclusive:
            os.replace(target, path)
        _fsync_dir(path.parent)
    except SecretBrokerError:
        raise
    except OSError:
        if not exclusive:
            try:
                os.unlink(target)
            except OSError:
                pass
        raise SecretBrokerError(
            "journal_corrupt", "secret ownership state could not be written durably",
        ) from None


def load_controller_id(root: Path) -> str:
    """Return this controller's random id, creating it once with O_EXCL."""
    root = ensure_private_dir(Path(root))
    path = root / "controller-id"
    for _ in range(2):
        try:
            raw = read_private_file(path, limit=64)
        except FileNotFoundError:
            try:
                write_private_file(path, (secrets.token_hex(16) + "\n").encode("ascii"))
            except FileExistsError:
                pass
            continue
        value = raw.decode("ascii", "replace").strip()
        if not _CONTROLLER_ID.fullmatch(value):
            raise _unsafe()
        return value
    raise _unsafe()


def new_qualified_id(prefix: Literal["ap", "run"], controller_id: str) -> str:
    if prefix not in ("ap", "run") or not _CONTROLLER_ID.fullmatch(controller_id or ""):
        raise SecretBrokerError("input_invalid", "qualified id input is invalid")
    return f"{prefix}_{controller_id}_{secrets.token_hex(8)}"


def qualified_controller(value: object) -> str | None:
    """Owner guidance only: the controller prefix of a qualified id."""
    match = _QUALIFIED_ID.fullmatch(value) if isinstance(value, str) else None
    return match.group(2) if match else None


def is_qualified_id(value: object, prefix: str) -> bool:
    match = _QUALIFIED_ID.fullmatch(value) if isinstance(value, str) else None
    return bool(match and match.group(1) == prefix)


def destination_sha256(destination: Destination) -> str:
    """sha256 of the canonical (non-secret) destination descriptor."""
    return hashlib.sha256(canonical_json(destination.as_normalized())).hexdigest()


def journal_dir(root: Path, profile: str, destination_digest: str) -> Path:
    if not _PROFILE.fullmatch(profile or "") or not _SHA256.fullmatch(destination_digest or ""):
        raise SecretBrokerError("input_invalid", "journal selector is invalid")
    return Path(root) / "journals" / profile / destination_digest


def journal_exists(root: Path, profile: str, destination_digest: str) -> bool:
    return (journal_dir(root, profile, destination_digest) / "journal.jsonl").exists()


def _parse_lines(raw: bytes) -> tuple[dict, list[dict], int]:
    """Return (header, records, valid_length). A final fragment lacking a
    newline is torn and excluded; any other damage refuses."""
    end = raw.rfind(b"\n") + 1
    complete = raw[:end]
    lines = complete.split(b"\n")[:-1] if complete else []
    if not lines:
        raise _corrupt()
    try:
        decoded = [json.loads(line.decode("ascii"), object_pairs_hook=_unique_pairs,
                              parse_constant=_reject_constant) for line in lines]
    except (UnicodeDecodeError, ValueError):
        raise _corrupt() from None
    header, records = decoded[0], decoded[1:]
    if (not isinstance(header, dict) or set(header) != _HEADER_KEYS or header.get("journal") != 1
            or not _CONTROLLER_ID.fullmatch(str(header.get("controller_id")))
            or not _CONTROLLER_ID.fullmatch(str(header.get("journal_id")))
            or not _PROFILE.fullmatch(str(header.get("profile")))
            or not _SHA256.fullmatch(str(header.get("destination_sha256")))
            or not re.fullmatch(r"o1-[0-9a-f]{64}", str(header.get("authority_digest")))
            or not _timestamp(header.get("created_at"))):
        raise _corrupt()
    for index, record in enumerate(records, start=1):
        if (not isinstance(record, dict) or set(record) != _RECORD_KEYS
                or type(record.get("seq")) is not int or record.get("seq") != index
                or record.get("state") not in _STATES
                or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", str(record.get("name")))
                or not is_qualified_id(record.get("run_id"), "run")
                or not is_qualified_id(record.get("approval_id"), "ap")
                or qualified_controller(record["run_id"]) != header["controller_id"]
                or qualified_controller(record["approval_id"]) != header["controller_id"]
                or not _timestamp(record.get("at"))
                or (record.get("reason") is not None and not re.fullmatch(
                    r"[a-z][a-z0-9_]{0,63}", str(record["reason"])))):
            raise _corrupt()
    return header, records, end


def _timestamp(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("invalid constant")


def _states_from(records: list[dict]) -> dict[str, NameState]:
    states: dict[str, NameState] = {}
    for record in records:
        states[record["name"]] = NameState(record["state"])
    # A surviving attempt from any earlier process is unknown, never pending.
    return {
        name: NameState.ACCEPTANCE_UNKNOWN if state is NameState.ATTEMPTED else state
        for name, state in sorted(states.items())
    }


class Journal:
    """One profile/destination journal, exclusively locked for its lifetime."""

    def __init__(self, directory: Path, lock_fd: int, journal_fd: int, header: dict,
                 records: list[dict]) -> None:
        self.directory = directory
        self._lock_fd = lock_fd
        self._fd = journal_fd
        self.header = header
        self._records = records
        self.fresh = not records

    @classmethod
    def open(cls, root: Path, profile: str, destination_sha256: str, *, controller_id: str,
             authority_digest: str) -> "Journal":
        if (not _CONTROLLER_ID.fullmatch(controller_id or "")
                or not re.fullmatch(r"o1-[0-9a-f]{64}", authority_digest or "")):
            raise _corrupt()
        directory = journal_dir(root, profile, destination_sha256)
        for part in (Path(root), Path(root) / "journals", directory.parent, directory):
            ensure_private_dir(part)
        lock_fd = journal_fd = -1
        try:
            lock_fd = os.open(directory / "journal.lock",
                              os.O_RDWR | os.O_CREAT | _NOFOLLOW | _CLOEXEC, 0o600)
            _check_fd(lock_fd)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise SecretBrokerError(
                    "journal_locked", "another local run holds this ownership journal",
                ) from None
            path = directory / "journal.jsonl"
            try:
                journal_fd = os.open(path, os.O_RDWR | os.O_APPEND | _NOFOLLOW | _CLOEXEC)
                created = False
            except FileNotFoundError:
                journal_fd = os.open(
                    path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                    0o600,
                )
                created = True
            info = _check_fd(journal_fd)
            if created:
                header = {
                    "journal": 1, "journal_id": secrets.token_hex(16),
                    "controller_id": controller_id, "profile": profile,
                    "destination_sha256": destination_sha256,
                    "authority_digest": authority_digest, "created_at": time.time(),
                }
                _write_all(journal_fd, json.dumps(header, sort_keys=True).encode("ascii") + b"\n")
                os.fsync(journal_fd)
                _fsync_dir(directory)
                records: list[dict] = []
            else:
                if info.st_size > _MAX_STATE_BYTES:
                    raise _corrupt()
                raw = os.pread(journal_fd, info.st_size, 0)
                header, records, valid = _parse_lines(raw)
                if header["controller_id"] != controller_id:
                    raise SecretBrokerError(
                        "journal_foreign_controller",
                        "this ownership journal belongs to another controller",
                        remedy=f"resume from controller {header['controller_id']}",
                    )
                if header["profile"] != profile or header["destination_sha256"] != destination_sha256:
                    raise _corrupt()
                if header["authority_digest"] != authority_digest:
                    raise SecretBrokerError(
                        "authority_mismatch", "ownership authority does not match this journal",
                    )
                if valid != len(raw):
                    # Ownership is checked before altering even a torn tail.
                    os.ftruncate(journal_fd, valid)
                    os.fsync(journal_fd)
            journal = cls(directory, lock_fd, journal_fd, header, records)
            lock_fd = journal_fd = -1
            return journal
        except SecretBrokerError:
            raise
        except OSError:
            raise _unsafe() from None
        finally:
            if journal_fd >= 0:
                os.close(journal_fd)
            if lock_fd >= 0:
                os.close(lock_fd)

    def states(self) -> dict[str, NameState]:
        return _states_from(self._records)

    def records(self) -> list[dict]:
        return [dict(record) for record in self._records]

    def append(self, run_id: str, approval_id: str, name: str, state: NameState,
               reason: str | None = None) -> None:
        """Write one full record and fsync it before returning (the create
        request is released only after this returns)."""
        if self._fd < 0:
            raise _corrupt()
        if (not is_qualified_id(run_id, "run") or not is_qualified_id(approval_id, "ap")
                or qualified_controller(run_id) != self.header["controller_id"]
                or qualified_controller(approval_id) != self.header["controller_id"]
                or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
                or state.value not in _STATES
                or (reason is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason))):
            raise _corrupt()
        record = {
            "seq": len(self._records) + 1, "at": time.time(),
            "run_id": run_id, "approval_id": approval_id,
            "name": name, "state": state.value, "reason": reason,
        }
        _write_all(self._fd, json.dumps(record, sort_keys=True).encode("ascii") + b"\n")
        os.fsync(self._fd)
        _fsync_dir(self.directory)
        self._records.append(record)

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1
        if self._lock_fd >= 0:
            try:
                os.close(self._lock_fd)
            finally:
                self._lock_fd = -1

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_journal_states(root: Path, profile: str, destination_digest: str) -> dict[str, NameState] | None:
    """Read settled names without locking; returns None if the journal is absent."""
    path = journal_dir(root, profile, destination_digest) / "journal.jsonl"
    try:
        raw = read_private_file(path)
    except FileNotFoundError:
        return None
    return _states_from(_parse_lines(raw)[1])


def retention(root: Path, profile: str, *, confirm: bool) -> dict:
    """Remove only settled metadata. Stable lock files remain to avoid lock races."""
    if not _PROFILE.fullmatch(profile or ""):
        raise SecretBrokerError("input_invalid", "profile is invalid")
    root = Path(root)
    base, runs = root / "journals" / profile, root / "runs"
    removable, kept, removed, skipped, run_ids = [], [], [], [], []
    safe_states = {NameState.VERIFIED, NameState.RECONCILED}
    completed = []
    if runs.exists():
        ensure_private_dir(runs)
        for path in sorted(runs.glob("run_*.json")):
            try:
                data = json.loads(read_private_file(path).decode("ascii"),
                                  object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
                names = data.get("names")
                if (data.get("profile") == profile and data.get("end_reason") == "completed"
                        and data.get("revocation") in ("revoked", "not_applicable")
                        and is_qualified_id(data.get("run_id"), "run")
                        and path.name == data["run_id"] + ".json"
                        and qualified_controller(data["run_id"]) == load_controller_id(root)
                        and isinstance(names, dict) and names
                        and set(names) <= {"verified", "reconciled", "removed"}
                        and all(isinstance(items, list) and all(isinstance(name, str)
                            and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) for name in items)
                            for items in names.values())):
                    completed.append((path, data))
            except (OSError, ValueError, AttributeError, SecretBrokerError):
                continue  # Corrupt/uncertain records are retained.
    if base.exists():
        ensure_private_dir(base)
        for directory in sorted(base.iterdir()):
            if not _SHA256.fullmatch(directory.name) or directory.is_symlink():
                continue
            lock_fd = -1
            try:
                ensure_private_dir(directory)
                lock_fd = os.open(directory / "journal.lock",
                                  os.O_RDWR | os.O_CREAT | _NOFOLLOW | _CLOEXEC, 0o600)
                _check_fd(lock_fd)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raw = read_private_file(directory / "journal.jsonl")
                header, records, valid = _parse_lines(raw)
                states = _states_from(records)
                # Cleanup progress is recovery evidence independent of the
                # migration states. Preserve it, including uncertain removals,
                # until a retention policy can prove cleanup itself complete.
                try:
                    os.lstat(directory / "cleanup.json")
                except FileNotFoundError:
                    pass
                else:
                    kept.append(directory.name)
                    continue
                completed_ids = {data["run_id"] for _, data in completed}
                if (valid != len(raw) or not states or not set(states.values()) <= safe_states
                        or not records or records[-1]["run_id"] not in completed_ids
                        or header["controller_id"] != load_controller_id(root)
                        or header["profile"] != profile
                        or header["destination_sha256"] != directory.name):
                    kept.append(directory.name)
                    continue
                removable.append(directory.name)
                if confirm:
                    (directory / "journal.jsonl").unlink()
                    _fsync_dir(directory)
                    removed.append(directory.name)
                # Keep the directory and lock inode. Concurrent openers must
                # always contend on the same lock, including after retention.
            except (OSError, SecretBrokerError):
                kept.append(directory.name)
                skipped.append(directory.name)
            finally:
                if lock_fd >= 0:
                    os.close(lock_fd)
    # If any journal is uncertain, preserve every run record for this profile.
    if not kept:
        for path, data in completed:
            run_ids.append(data["run_id"])
            if confirm:
                try:
                    path.unlink()
                    _fsync_dir(runs)
                except OSError:
                    skipped.append(data["run_id"])
    return {
        "ok": True, "code": "retention_applied" if confirm else "retention_plan",
        "profile": profile, "confirm": bool(confirm),
        "journals": {"removable": removable, "kept": kept,
                     "removed": removed, "skipped": skipped},
        "runs": {"removable": run_ids},
    }


__all__ = [
    "RETAINED_STATES", "Journal", "destination_sha256", "ensure_private_dir",
    "is_qualified_id", "journal_dir", "journal_exists", "load_controller_id",
    "new_qualified_id", "qualified_controller", "read_journal_states",
    "read_private_file", "retention", "write_private_file",
]
