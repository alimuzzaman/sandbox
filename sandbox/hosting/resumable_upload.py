"""Private v2 upload worker, sent verbatim over the registered SSH transport.

The engine lease surrounds the caller. The worker fence protects this entire
session even after that lease's controller dies. Neither offsets nor saved
metadata replace hashing actual bytes. All helpers are stdlib-only on the host.
"""
from __future__ import annotations

import inspect


def cleanup_partials(root, current=None):
    """Under the worker fence, delete only safe stale/superseded partial pairs."""
    import json
    import os
    import re
    import stat
    import time

    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return
    try:
        info = os.fstat(directory)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('unsafe partial directory')
        names = []
        with os.scandir(directory) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > 256:
                    raise ValueError('partial cache full')
        for stem in sorted({name.rsplit('.', 1)[0] for name in names
                            if re.fullmatch(r'\.resume-[a-f0-9]{64}\.(json|tar)', name)}):
            pair = []
            for suffix in ('.json', '.tar'):
                name = stem + suffix
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                except FileNotFoundError:
                    continue
                with os.fdopen(fd, 'rb') as handle:
                    info = os.fstat(handle.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                        raise ValueError('unsafe partial entry')
                    context = None
                    if suffix == '.json' and info.st_size <= 4096:
                        try:
                            context = json.loads(handle.read(4097))
                        except (ValueError, UnicodeError):
                            pass
                    pair.append((name, info, context))
            stale = pair and max(item[1].st_mtime for item in pair) < time.time() - 86400
            metadata = next((item[2] for item in pair if item[0].endswith('.json')), None)
            superseded = (current is not None and isinstance(metadata, dict)
                          and all(metadata.get(k) == current[k] for k in ('project', 'environment', 'machine_identity'))
                          and metadata != current)
            if stale or superseded:
                for name, _info, _context in pair:
                    os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)


def resumable_stage():
    """Offer prefix → confirm/reset → acknowledge offset → suffix → full load."""
    import fcntl
    import hashlib
    import json
    import os
    import re
    import stat
    import subprocess
    import sys

    root, raw, reservation, timeout = sys.argv[1:]
    def closed(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate upload key')
            if key in {'schema', 'offset', 'archive_bytes'} and type(value) is not int:
                raise ValueError('invalid upload number')
            result[key] = value
        return result
    context = json.loads(raw, object_pairs_hook=closed)
    expected = {'schema', 'project', 'environment', 'machine_identity', 'registered_host_digest',
                'receipt_digest', 'bundle_digest', 'archive_bytes'}
    if (not isinstance(context, dict) or set(context) != expected or type(context['schema']) is not int or context['schema'] != 1
            or type(context['archive_bytes']) is not int or not 0 < context['archive_bytes'] <= 64 * 1024**3
            or not re.fullmatch(r'[a-f0-9]{32}', reservation)
            or not root.startswith('/') or not 1 <= int(timeout) <= 100000):
        sys.exit(31)
    for key in ('registered_host_digest', 'receipt_digest', 'bundle_digest'):
        if not isinstance(context[key], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', context[key]):
            sys.exit(31)
    for key in ('project', 'environment', 'machine_identity'):
        if not isinstance(context[key], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', context[key]):
            sys.exit(31)
    identity = hashlib.sha256(json.dumps(context, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    parent = os.path.dirname(root)
    quarantine = parent + '/load-unverified'
    fence = directory = archive = -1
    load_may_run = admitted = False
    def private(fd, *, directory=False):
        info = os.fstat(fd)
        if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
            raise ValueError('unsafe partial entry')
        return info
    def emit(value):
        print(json.dumps(value, separators=(',', ':')), flush=True)
    def write_all(fd, data):
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError('short upload write')
            view = view[written:]
    try:
        if os.path.lexists(quarantine):
            sys.exit(75)
        fence = os.open(parent + '/load-fence.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        private(fence)
        try:
            fcntl.flock(fence, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit(75)
        if os.path.lexists(quarantine):
            sys.exit(75)
        os.makedirs(root, mode=0o700, exist_ok=True)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        private(directory, directory=True)
        cleanup_partials(root, context)
        # A full operation reservation is required even for a retained prefix.
        release_storage_reservation(os.path.dirname(parent) + '/storage', reservation, verify_only=True)
        admitted = True
        meta = '.resume-' + identity + '.json'
        name = '.resume-' + identity + '.tar'
        # Check capacity before creating either member of a new pair.
        names = os.listdir(directory)
        additions = int(meta not in names) + int(name not in names)
        sizes = [os.stat(item, dir_fd=directory, follow_symlinks=False).st_size for item in names
                 if re.fullmatch(r'\.resume-[a-f0-9]{64}\.tar', item) and item != name]
        if len(names) + additions > 128 or sum(sizes) + context['archive_bytes'] > 128 * 1024**3:
            sys.exit(36)
        try:
            descriptor = os.open(meta, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            # A crash before the metadata commit cannot authorize archive reuse.
            try:
                orphan = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                pass
            else:
                try:
                    private(orphan)
                finally:
                    os.close(orphan)
                os.unlink(name, dir_fd=directory)
            descriptor = os.open(meta, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                private(descriptor)
                write_all(descriptor, json.dumps(context, sort_keys=True, separators=(',', ':')).encode())
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(directory)
        else:
            with os.fdopen(descriptor, 'rb') as handle:
                info = private(handle.fileno())
                try:
                    matches = info.st_size <= 4096 and json.loads(handle.read(4097), object_pairs_hook=closed) == context
                except (ValueError, UnicodeError):
                    matches = False
            if not matches:
                # Never append under mismatched/corrupt metadata. A later retry
                # recreates the pair; it does not trust the orphaned archive.
                os.unlink(meta, dir_fd=directory)
                os.fsync(directory)
                sys.exit(37)
        archive = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        info = private(archive)
        size = context['archive_bytes']
        if info.st_size > size:
            os.ftruncate(archive, 0)
            os.fsync(archive)
        digest = hashlib.sha256()
        count = 0
        while True:
            chunk = os.read(archive, 1048576)
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
        os.fsync(directory)
        prefix = 'sha256:' + digest.hexdigest()
        emit({'schema': 2, 'state': 'resume', 'reservation_id': reservation, 'upload_id': identity,
              'size': size, 'offset': count, 'prefix_sha256': prefix})
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096 or not raw.endswith(b'\n'):
            sys.exit(35)
        confirmation = json.loads(raw, object_pairs_hook=closed)
        if confirmation == {'schema': 2, 'action': 'restart'}:
            os.ftruncate(archive, 0)
            os.lseek(archive, 0, os.SEEK_SET)
            os.fsync(archive)
            digest = hashlib.sha256()
            count = 0
        elif confirmation != {'schema': 2, 'action': 'resume', 'offset': count, 'prefix_sha256': prefix}:
            sys.exit(35)
        offset = count
        emit({'schema': 2, 'state': 'ready', 'reservation_id': reservation, 'upload_id': identity, 'offset': offset})
        # Use the same buffered stdin as readline: it may already contain bytes.
        while count <= size:
            chunk = sys.stdin.buffer.read(min(1048576, size + 1 - count))
            if not chunk:
                break
            write_all(archive, chunk)
            digest.update(chunk)
            count += len(chunk)
        os.fsync(archive)
        if count != size or 'sha256:' + digest.hexdigest() != context['bundle_digest']:
            sys.exit(32)
        marker = os.open(quarantine, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            write_all(marker, json.dumps({'reservation_id': reservation}).encode())
            os.fsync(marker)
        finally:
            os.close(marker)
        os.fchmod(fence, 0o400)
        os.fsync(fence)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        load_may_run = True
        try:
            loaded = subprocess.run(['docker', 'image', 'load', '-i', root + '/' + name], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=int(timeout), check=False, pass_fds=(fence,))
        except (subprocess.SubprocessError, OSError):
            sys.exit(34)
        if loaded.returncode:
            sys.exit(34)
        release_storage_reservation(os.path.dirname(parent) + '/storage', reservation)
        os.unlink(meta, dir_fd=directory)
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)
        os.fchmod(fence, 0o600)
        os.fsync(fence)
        os.unlink(quarantine)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        emit({'schema': 1, 'state': 'loaded', 'bytes': count})
    finally:
        if archive >= 0:
            os.fsync(archive)
            os.close(archive)
        # Disconnect before load preserves bytes, but not a live reservation.
        # If killed before finally, existing fenced reconciliation releases it.
        if admitted and not load_may_run:
            release_storage_reservation(os.path.dirname(parent) + '/storage', reservation)
        if directory >= 0:
            os.close(directory)
        if fence >= 0:
            os.close(fence)


UPLOAD_HELPERS_PROGRAM = inspect.getsource(cleanup_partials)
STAGE_PROGRAM = UPLOAD_HELPERS_PROGRAM + '\n' + inspect.getsource(resumable_stage) + '\nresumable_stage()\n'
