"""Atomic, revision-checked updates for one registered secret assignment."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from .ownership.models import SourceContinuity

from .models import SafeSource, SecretBrokerError
from .parser import ParsedDocument, parse_document, render_assignment, replace_assignment


def load_revision_key(path: str | Path) -> bytes:
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if target.parent.stat().st_uid != os.geteuid() or stat.S_IMODE(target.parent.stat().st_mode) & 0o077:
            raise SecretBrokerError("revision_unavailable", "secret revision directory is unsafe")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(target, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
                raise SecretBrokerError("revision_unavailable", "secret revision key is unsafe")
            value = os.read(fd, 33)
            if not value:
                value = os.urandom(32)
                os.write(fd, value)
                os.fsync(fd)
            if len(value) != 32:
                raise SecretBrokerError("revision_unavailable", "secret revision key is invalid")
            return value
        finally:
            os.close(fd)
    except SecretBrokerError:
        raise
    except OSError as exc:
        raise SecretBrokerError("revision_unavailable", "secret revision key is unavailable") from exc


def opaque_revision(key: bytes, content: bytes) -> str:
    return "r1_" + hmac.new(key, content, hashlib.sha256).hexdigest()[:32]


def _render(document: ParsedDocument, key: str, value: str, *, exists: bool) -> bytes:
    if exists:
        return replace_assignment(document, key, value)
    if document.newline_style == "mixed":
        raise SecretBrokerError("syntax_unsupported", "mixed newlines cannot be updated")
    newline = b"\r\n" if document.newline_style == "crlf" else b"\n"
    prefix = b"" if not document.raw_bytes or document.raw_bytes.endswith((b"\n", b"\r\n")) else newline
    return document.raw_bytes + prefix + render_assignment(key, value).encode("utf-8") + newline


def update_source(
    source: SafeSource,
    *,
    key: str,
    value: str,
    revision_key: bytes,
    intent: str = "either",
    expected_revision: str | None = None,
) -> tuple[str, str]:
    """Update one key, returning (created|updated, new opaque revision)."""
    if intent not in {"create", "replace", "either"}:
        raise SecretBrokerError("intent_conflict", "secret update intent is invalid")
    current_revision = opaque_revision(revision_key, source.content)
    if expected_revision is not None and not hmac.compare_digest(expected_revision, current_revision):
        raise SecretBrokerError("revision_conflict", "secret source revision no longer matches")
    try:
        document = parse_document(source.content)
    except Exception as exc:
        if hasattr(exc, "code"):
            raise SecretBrokerError("syntax_unsupported", "secret source syntax cannot be updated") from exc
        raise
    exists = key in document.entries
    if intent == "create" and exists:
        raise SecretBrokerError("intent_conflict", "secret key already exists")
    if intent == "replace" and not exists:
        raise SecretBrokerError("intent_conflict", "secret key does not exist")
    try:
        updated = _render(document, key, value, exists=exists)
    except Exception as exc:
        if hasattr(exc, "code"):
            raise SecretBrokerError("input_invalid", "secret input cannot be represented safely") from exc
        raise

    _atomic_replace(source, updated)
    return ("updated" if exists else "created", opaque_revision(revision_key, updated))


def rewrite_source(source: SafeSource, content: bytes, *, revision_key: bytes,
                   expected_revision: str | None = None) -> str:
    """Replace the whole source with pre-rendered bytes, returning the revision.

    Callers must derive ``content`` from the parsed document so no value is
    reconstructed here.  The same lock, signature, and durability guarantees as
    a targeted update apply.
    """
    if not isinstance(content, bytes):
        raise SecretBrokerError("input_invalid", "secret source content must be bytes")
    current_revision = opaque_revision(revision_key, source.content)
    if expected_revision is not None and not hmac.compare_digest(expected_revision, current_revision):
        raise SecretBrokerError("revision_conflict", "secret source revision no longer matches")
    _atomic_replace(source, content)
    return opaque_revision(revision_key, content)


def _atomic_replace(source: SafeSource, updated: bytes) -> None:
    path = source.policy.path
    safe_name = path.name.lstrip(".")
    lock_path = path.with_name(f".{safe_name}.sb-secrets.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        try:
            lock_info = os.fstat(lock_fd)
            if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.geteuid()
                    or stat.S_IMODE(lock_info.st_mode) & 0o077 or lock_info.st_nlink != 1):
                raise SecretBrokerError("source_unsafe", "secret update lock is unsafe")
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            now = path.stat()
            now_signature = (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns, now.st_ctime_ns)
            if now_signature != source.signature:
                raise SecretBrokerError("revision_conflict", "secret source changed before update")
            temp_fd, temp_name = tempfile.mkstemp(prefix=f".{safe_name}.", dir=path.parent)
            try:
                os.fchmod(temp_fd, stat.S_IMODE(now.st_mode))
                try:
                    os.fchown(temp_fd, now.st_uid, now.st_gid)
                except PermissionError:
                    pass
                view = memoryview(updated)
                while view:
                    written = os.write(temp_fd, view)
                    view = view[written:]
                os.fsync(temp_fd)
                os.close(temp_fd)
                temp_fd = -1
                latest = path.stat()
                latest_signature = (
                    latest.st_dev, latest.st_ino, latest.st_size,
                    latest.st_mtime_ns, latest.st_ctime_ns,
                )
                if latest_signature != source.signature:
                    raise SecretBrokerError("revision_conflict", "secret source changed during update")
                os.replace(temp_name, path)
                temp_name = ""
                dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                if temp_fd >= 0:
                    os.close(temp_fd)
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(lock_fd)
    except SecretBrokerError:
        raise
    except OSError as exc:
        raise SecretBrokerError("update_failed", "secret source update failed") from exc


def remove_keys(path: Path, keys: Sequence[str], *, expected: SourceContinuity,
                before_replace: Callable[[SourceContinuity], None] | None = None,
                deadline: float | None = None) -> SourceContinuity:
    """Remove selected dotenv assignments atomically using metadata continuity.

    No value digest or legacy revision participates. The caller must perform
    its final destination comparison immediately before calling this function.
    """
    import time
    from .models import MAX_SOURCE_BYTES
    from .parser import SecretParseError
    from .ownership.continuity import capture
    from .ownership.models import SourceContinuity

    if (not isinstance(expected, SourceContinuity) or not expected.reliable):
        raise SecretBrokerError("continuity_unreliable", "source continuity is unreliable")
    if (not isinstance(keys, (list, tuple)) or not keys
            or any(not isinstance(key, str) for key in keys) or len(set(keys)) != len(keys)):
        raise SecretBrokerError("selection_invalid", "cleanup key selection is invalid")
    path = Path(path)
    directory_fd = lock_fd = source_fd = temporary_fd = -1
    temporary_name = None
    nofollow = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)

    def changed():
        return SecretBrokerError("source_changed", "source changed before cleanup")

    if deadline is not None and time.time() >= deadline:
        raise SecretBrokerError("approval_expired", "cleanup authority expired")

    try:
        for parent in (path.parent, *path.parent.parents):
            if not stat.S_ISDIR(os.lstat(parent).st_mode):
                raise changed()
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow)
        lock_name = f".{path.name.lstrip('.')}.sb-secrets.lock"
        lock_fd = os.open(lock_name, os.O_RDWR | os.O_CREAT | nofollow, 0o600, dir_fd=directory_fd)
        lock_info = os.fstat(lock_fd)
        if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.geteuid()
                or stat.S_IMODE(lock_info.st_mode) != 0o600 or lock_info.st_nlink != 1):
            raise changed()
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SecretBrokerError("journal_locked", "source update lock is held") from None
        source_fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=directory_fd)
        before = os.fstat(source_fd)
        # Check metadata before a byte read, then full continuity after parsing.
        if any(getattr(before, stat_key) != getattr(expected, field) for field, stat_key in (
                ("dev", "st_dev"), ("ino", "st_ino"), ("size", "st_size"),
                ("mtime_ns", "st_mtime_ns"), ("ctime_ns", "st_ctime_ns"),
                ("nlink", "st_nlink"), ("uid", "st_uid"), ("mode", "st_mode"))):
            raise changed()
        chunks, total = [], 0
        while True:
            chunk = os.read(source_fd, min(65536, MAX_SOURCE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise changed()
        try:
            document = parse_document(b"".join(chunks))
        except SecretParseError:
            raise SecretBrokerError("source_unsupported", "source cannot be safely cleaned") from None
        current = capture(expected.alias, os.fstat(source_fd), document.entries,
                          now_ns=time.time_ns())
        if not current.reliable or not current.same_as(expected):
            raise changed()
        if not set(keys) <= set(document.entries):
            raise SecretBrokerError("cleanup_ineligible", "cleanup key is no longer present")
        selected = set(keys)
        updated = b"".join(record.raw for record in document.records
                           if getattr(record, "key", None) not in selected)
        # mkstemp uses a sibling file, preserving owner-only permissions.
        temporary_fd, temp_path = tempfile.mkstemp(prefix=".sb-cleanup-", dir=path.parent)
        temporary_name = Path(temp_path).name
        os.fchmod(temporary_fd, stat.S_IMODE(before.st_mode))
        view = memoryview(updated)
        while view:
            written = os.write(temporary_fd, view)
            if written <= 0:
                raise changed()
            view = view[written:]
        os.fsync(temporary_fd)
        latest = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        post_latest = capture(expected.alias, latest, document.entries,
                              now_ns=time.time_ns())
        if not post_latest.reliable or not post_latest.same_as(expected):
            raise changed()
        if deadline is not None and time.time() >= deadline:
            raise SecretBrokerError("approval_expired", "cleanup authority expired")
        staged_continuity = capture(expected.alias, os.fstat(temporary_fd),
                                    set(document.entries) - selected,
                                    now_ns=time.time_ns())
        if before_replace is not None:
            before_replace(staged_continuity)
        if deadline is not None and time.time() >= deadline:
            raise SecretBrokerError("approval_expired", "cleanup authority expired")
        final_latest = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        final_check = capture(expected.alias, final_latest, document.entries,
                              now_ns=time.time_ns())
        if not final_check.reliable or not final_check.same_as(expected):
            raise changed()
        if deadline is not None and time.time() >= deadline:
            raise SecretBrokerError("approval_expired", "cleanup authority expired")
        os.replace(temporary_name, path.name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary_name = None
        # Rename may change ctime; all other staged metadata must stay ours.
        from dataclasses import replace
        renamed = capture(expected.alias, os.fstat(temporary_fd),
                          set(document.entries) - selected, now_ns=time.time_ns())
        permitted = replace(staged_continuity, ctime_ns=renamed.ctime_ns)
        if not renamed.reliable or not renamed.same_as(permitted):
            raise changed()
        os.fsync(directory_fd)
        result = capture(expected.alias, os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False),
                         set(document.entries) - selected, now_ns=time.time_ns())
        if not result.reliable:
            raise SecretBrokerError("continuity_unreliable", "cleanup continuity is unreliable")
        if not result.same_as(renamed):
            raise changed()
        return renamed
    except SecretBrokerError:
        raise
    except OSError:
        raise changed() from None
    finally:
        if temporary_name is not None and directory_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        for descriptor in (temporary_fd, source_fd, lock_fd, directory_fd):
            if descriptor >= 0:
                os.close(descriptor)
