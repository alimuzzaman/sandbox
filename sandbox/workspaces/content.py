"""Measured checkout bytes and the copy/admission exclusion they require."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import threading

CONTRACT = 'job.source-content.v1'
_held = threading.local()


def source_content_available():
    """Installed assembly observation; never an execution/actor grant."""
    try:
        from sandbox.application.job_service import SOURCE_CONTENT_VERSION as admission_version
        from sandbox.workspaces.checkout import SOURCE_CONTENT_VERSION as checkout_version
        from sandbox.jobs.models import SourceIdentity
    except ImportError:
        return False
    return (type(admission_version) is int and type(checkout_version) is int
            and admission_version == checkout_version == 1
            and 'content_digest' in SourceIdentity.__dataclass_fields__)


def content_digest(root, expected_commit=None):
    """Hash actual bytes/modes/link targets and HEAD, never a client receipt.

    This function is self-contained so the source lease can send its exact
    trusted implementation to a receiver before that receiver's CLI is updated.
    """
    import hashlib
    import json
    import os
    import stat
    import subprocess
    from pathlib import Path
    root = Path(root)
    digest = hashlib.sha256(b'sandbox-checkout-content-v1\0')
    total = count = 0
    def head():
        if not (root / '.git').exists():
            return b''
        result = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/nonexistent',
                 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_OPTIONAL_LOCKS': '0'},
            capture_output=True, timeout=10, check=True)
        return result.stdout.strip()
    before_head = head()
    if expected_commit is not None and before_head.decode('ascii') != expected_commit:
        raise ValueError('checkout commit changed')
    def walk(directory, relative=''):
        nonlocal total, count
        for name in sorted(os.listdir(directory)):
            if not relative and name in {'.git', '.sandbox-git-admin'}:
                continue
            rel = relative + name
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            count += 1
            if count > 100000:
                raise ValueError('checkout content entry limit exceeded')
            if stat.S_ISLNK(before.st_mode):
                kind, payload = 'link', os.readlink(name, dir_fd=directory).encode('utf-8', 'surrogateescape')
            elif stat.S_ISDIR(before.st_mode):
                kind, payload = 'directory', b''
            elif stat.S_ISREG(before.st_mode):
                kind = 'file'
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    opened = os.fstat(fd)
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise ValueError('checkout content changed')
                    hashed = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = os.read(fd, 1048576)
                        if not chunk:
                            break
                        size += len(chunk)
                        total += len(chunk)
                        if total > 536870912:
                            raise ValueError('checkout content byte limit exceeded')
                        hashed.update(chunk)
                    after = os.fstat(fd)
                    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('checkout content changed')
                    payload = str(size).encode() + b':' + hashed.digest()
                finally:
                    os.close(fd)
            else:
                raise ValueError('unsupported checkout content entry')
            digest.update(json.dumps([rel, kind, before.st_mode & 0o111 if kind == 'file' else 0],
                                     ensure_ascii=True, separators=(',', ':')).encode() + b'\0')
            digest.update(str(len(payload)).encode() + b':' + payload)
            if kind == 'directory':
                nested = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    opened = os.fstat(nested)
                    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                        raise ValueError('checkout directory changed')
                    walk(nested, rel + '/')
                finally:
                    os.close(nested)
            after = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('checkout content changed')
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        root_info = os.fstat(root_fd)
        walk(root_fd)
        if before_head != head():
            raise ValueError('checkout HEAD changed')
        named = root.lstat()
        if (root_info.st_dev, root_info.st_ino, root_info.st_mtime_ns, root_info.st_ctime_ns) != (
                named.st_dev, named.st_ino, named.st_mtime_ns, named.st_ctime_ns):
            raise ValueError('checkout root changed')
    finally:
        os.close(root_fd)
    digest.update(b'\0HEAD\0' + before_head)
    return 'sha256:' + digest.hexdigest()


@contextmanager
def checkout_guard(root):
    """One stable same-anchor lock shared by publication and actual acceptance."""
    path = Path(root).resolve(strict=False)
    key = str(path)
    held = getattr(_held, 'paths', None)
    if held is None:
        held = _held.paths = {}
    if key in held:
        yield
        return
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = '.sandbox-admission-' + hashlib.sha256(key.encode()).hexdigest()[:32] + '.lock'
    fd = -1
    try:
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('checkout admission lock is unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise ValueError('checkout admission lock changed')
        held[key] = fd
        yield
    finally:
        held.pop(key, None)
        if fd >= 0:
            os.close(fd)
        os.close(parent)


def verify_content(root, expected, expected_commit=None):
    try:
        observed = content_digest(root, expected_commit=expected_commit)
    except ValueError:
        raise ValueError('source_content_changed') from None
    if observed != expected:
        raise ValueError('source_content_changed')


def active_checkout_rows(root):
    """Read real original-job rows through the repository's bounded reader."""
    from sandbox.core._paths import RUNTIME_DIR
    from sandbox.jobs.registry import read_checkout_admissions
    return read_checkout_admissions(Path(RUNTIME_DIR) / 'jobs' / 'registry.sqlite3', root)
