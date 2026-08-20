"""Atomic, revision-checked updates for one registered secret assignment."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import stat
import tempfile
from pathlib import Path

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
