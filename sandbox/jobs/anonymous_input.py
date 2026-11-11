"""Bounded anonymous stdin handed to a supervisor through its real descriptor 0."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import select
import stat
import tempfile
import time

MAX_INPUT_BYTES = 1 << 20
_ISSUER = object()


class AnonymousInputUnavailable(ValueError):
    code = 'server_cli_stdin_unavailable'


def validate_inherited_input(fd, binding):
    """Metadata alone cannot replace the anonymous file held by the kernel."""
    if (type(fd) is not int or fd < 0 or not isinstance(binding, dict)
            or set(binding) != {'size', 'sha256', 'device', 'inode'}
            or any(type(binding[key]) is not int or binding[key] < 0 for key in ('size', 'device', 'inode'))
            or binding['size'] > MAX_INPUT_BYTES):
        raise AnonymousInputUnavailable('anonymous input binding is invalid')
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != binding['size']
                or info.st_dev != binding['device'] or info.st_ino != binding['inode']):
            raise AnonymousInputUnavailable('anonymous input kernel identity changed')
        digest = hashlib.sha256()
        position = 0
        while position < binding['size']:
            chunk = os.pread(fd, min(65536, binding['size'] - position), position)
            if not chunk:
                raise AnonymousInputUnavailable('anonymous input ended before its bound size')
            digest.update(chunk)
            position += len(chunk)
        if digest.hexdigest() != binding['sha256']:
            raise AnonymousInputUnavailable('anonymous input digest changed')
        after = os.fstat(fd)
        if any(getattr(after, key) != getattr(info, key) for key in
               ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise AnonymousInputUnavailable('anonymous input changed during validation')
    except OSError:
        raise AnonymousInputUnavailable('anonymous input descriptor is unavailable') from None
    return fd


@dataclass(frozen=True)
class AnonymousJobInput:
    _file: object = field(repr=False, compare=False)
    binding: dict = field(repr=False)
    _seal: object = field(repr=False, compare=False)

    def fileno(self):
        if type(self) is not AnonymousJobInput or self._seal is not _ISSUER:
            raise AnonymousInputUnavailable('anonymous input was not issued by intake')
        return validate_inherited_input(self._file.fileno(), self.binding)

    def close(self):
        self._file.close()

    def __enter__(self):
        self.fileno()
        return self

    def __exit__(self, *args):
        self.close()


def capture_input(fd, *, timeout_seconds=30):
    """Read original stdin once. No named payload, argv, logging or replay."""
    import math
    if (type(fd) is not int or fd < 0 or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300):
        raise AnonymousInputUnavailable('anonymous input intake is invalid')
    stream = tempfile.TemporaryFile(mode='w+b')
    digest, size = hashlib.sha256(), 0
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise AnonymousInputUnavailable('anonymous input intake deadline expired')
            chunk = os.read(fd, min(65536, MAX_INPUT_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_INPUT_BYTES:
                raise AnonymousInputUnavailable('original stdin exceeds the bounded input contract')
            digest.update(chunk)
            stream.write(chunk)
        stream.flush()
        stream.seek(0)
        info = os.fstat(stream.fileno())
        issued = AnonymousJobInput(stream, {'size': size, 'sha256': digest.hexdigest(),
            'device': info.st_dev, 'inode': info.st_ino}, _ISSUER)
        issued.fileno()
        return issued
    except OSError:
        stream.close()
        raise AnonymousInputUnavailable('original stdin intake is unavailable') from None
    except BaseException:
        stream.close()
        raise
