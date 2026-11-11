"""Root filesystem authority: descriptor-relative records and stable lock anchors."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .binding import InstallationBindingError


def fail(message):
    raise InstallationBindingError('installation_root_authority_invalid', message)


class RootDirectory:
    """Every ancestor and actual opened inode is checked; no path is followed through a link."""
    def __init__(self, path, *, create=False, private=True):
        self.path = Path(path)
        self.private = private
        if not self.path.is_absolute() or '..' in self.path.parts or self.path == Path('/'):
            fail('administrative directory must be a contained absolute root')
        self.fd = self._open(create=create)

    def _open(self, *, create=False):
        descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        current = Path('/')
        try:
            root = os.fstat(descriptor)
            if not stat.S_ISDIR(root.st_mode) or root.st_uid != 0 or root.st_mode & 0o022:
                fail('filesystem root authority is invalid')
            for number, part in enumerate(self.path.parts[1:]):
                current /= part
                final = number == len(self.path.parts[1:]) - 1
                try:
                    before = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    if not create or not final or os.geteuid() != 0:
                        raise
                    os.mkdir(part, 0o700 if self.private else 0o755, dir_fd=descriptor)
                    os.fsync(descriptor)
                    before = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                sticky_system_root = current == Path('/run/lock') and bool(before.st_mode & stat.S_ISVTX)
                forbidden = 0o077 if final and self.private else (0 if sticky_system_root else 0o022)
                if not stat.S_ISDIR(before.st_mode) or before.st_uid != 0 or before.st_mode & forbidden:
                    fail('administrative directory ancestors must be root-owned and protected')
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                actual = os.fstat(child)
                after = os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino) or (
                        after.st_dev, after.st_ino) != (actual.st_dev, actual.st_ino):
                    os.close(child)
                    fail('administrative directory changed during selection')
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def assert_current(self):
        current = self._open()
        try:
            expected, actual = os.fstat(self.fd), os.fstat(current)
            if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
                fail('administrative directory no longer has the selected identity')
        finally:
            os.close(current)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or name in {'.', '..'} or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', name):
            fail('administrative record name is invalid')
        return name

    def _file(self, descriptor, name, *, private=True):
        info = os.fstat(descriptor)
        lookup = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & (0o077 if private else 0o022)
                or (info.st_dev, info.st_ino) != (lookup.st_dev, lookup.st_ino)):
            fail('administrative record authority or anchor identity is invalid')
        return info

    def exists(self, name):
        self.assert_current()
        try:
            os.stat(self._name(name), dir_fd=self.fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def read(self, name, *, private=True):
        self.assert_current()
        name = self._name(name)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            info = self._file(descriptor, name, private=private)
            if info.st_size > 1024 * 1024:
                fail('administrative record is too large')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                value = json.loads(stream.read(1024 * 1024 + 1))
            self._file(descriptor, name, private=private)
            self.assert_current()
            if not isinstance(value, dict):
                fail('administrative record must be an object')
            return value
        except (ValueError, UnicodeError):
            fail('administrative record is invalid')
        finally:
            os.close(descriptor)

    def write(self, name, value, *, mode=0o600):
        if os.geteuid() != 0 or mode not in (0o600, 0o644):
            fail('only root may publish administrative records')
        name = self._name(name)
        self.assert_current()
        if self.exists(name):
            old = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            try:
                self._file(old, name, private=False)
            finally:
                os.close(old)
        temporary = '.pending-' + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             mode, dir_fd=self.fd)
        try:
            with os.fdopen(descriptor, 'w', closefd=False) as stream:
                json.dump(value, stream, sort_keys=True, separators=(',', ':'))
                stream.flush()
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            self.assert_current()
            os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
            self.assert_current()
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    @contextmanager
    def lock(self, name, *, exclusive, create=False):
        name = self._name(name)
        self.assert_current()
        if create and os.geteuid() != 0:
            fail('only root may create administrative lock anchors')
        flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
        descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=self.fd)
        try:
            self._file(descriptor, name, private=False)
            if create:
                os.fchmod(descriptor, 0o644)
                os.fsync(self.fd)
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            self._file(descriptor, name, private=False)
            self.assert_current()
            yield descriptor
            self._file(descriptor, name, private=False)
            self.assert_current()
        finally:
            os.close(descriptor)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
