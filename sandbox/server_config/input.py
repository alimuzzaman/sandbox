"""Bounded byte input and exact owner-only output for server fragments."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import secrets
from typing import BinaryIO


MAX_FRAGMENT_BYTES = 262_144


def _raise(code: str) -> None:
    raise ValueError(code)


def _file_facts(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def read_fragment_file(path: str | os.PathLike[str], *, maximum: int = MAX_FRAGMENT_BYTES) -> bytes:
    """Read one stable, owner-controlled regular file without following its final symlink."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.EFTYPE, errno.EISDIR, errno.ENOENT}:
            _raise("fragment_source_unsafe")
        _raise("fragment_source_unsafe")

    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
            _raise("fragment_source_unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(maximum + 1)
        after = os.fstat(descriptor)
        if _file_facts(before) != _file_facts(after):
            _raise("fragment_source_changed")
    finally:
        os.close(descriptor)

    if not payload:
        _raise("fragment_source_empty")
    if len(payload) > maximum:
        _raise("fragment_source_too_large")
    return payload


def read_fragment_stdin(
    stream: BinaryIO, *, maximum: int = MAX_FRAGMENT_BYTES, deadline: float | None = None
) -> bytes:
    """Read at most one byte beyond the supported stdin boundary within deadline."""
    import time

    start = time.monotonic()
    if deadline is not None and deadline <= 0:
        _raise("stdin_deadline_exceeded")
    payload = stream.read(maximum + 1)
    if deadline is not None and (time.monotonic() - start) > deadline:
        _raise("stdin_deadline_exceeded")
    if not isinstance(payload, bytes):
        _raise("fragment_source_unsafe")
    if not payload:
        _raise("fragment_source_empty")
    if len(payload) > maximum:
        _raise("fragment_source_too_large")
    return payload


def _fsync_directory(path: Path) -> None:
    descriptor = _open_owner_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _open_owner_directory(path: Path) -> int:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        _raise("content_output_unsafe")
    facts = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(facts.st_mode)
        or facts.st_uid != os.getuid()
        or facts.st_mode & 0o022
    ):
        os.close(descriptor)
        _raise("content_output_unsafe")
    return descriptor


def write_fragment_output(path: str | os.PathLike[str], payload: bytes) -> dict[str, object]:
    """Atomically export exact bytes to one regular owner-only file."""
    if not isinstance(payload, bytes):
        _raise("content_output_unsafe")
    destination = Path(path)
    parent = destination.parent
    parent_descriptor = _open_owner_directory(parent)
    try:
        current = os.stat(destination.name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        current = None
    if current is not None and (
        not stat.S_ISREG(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o077
    ):
        os.close(parent_descriptor)
        _raise("content_output_unsafe")

    temporary_name: str | None = None
    try:
        for _attempt in range(32):
            temporary_name = ".server-config-" + secrets.token_hex(16)
            try:
                descriptor = os.open(
                    temporary_name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                temporary_name = None
                continue
        else:
            _raise("content_output_unsafe")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(
            temporary_name,
            destination.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        os.fsync(parent_descriptor)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
        os.close(parent_descriptor)
    return {"written": True, "basename": destination.name}
