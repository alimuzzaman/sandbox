"""Bounded private snapshots for the two local job diagnostic projections.

SQLite never opens the owner path: even a read-only WAL connection can write
shared-memory read marks. Unsupported or changing owner state is unavailable,
not an invitation to recover it or to fall back to a source connection.
"""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import time


SOURCE_BYTES = 32 * 1024 * 1024
IMAGE_BYTES = 32 * 1024 * 1024
FRAME_LIMIT = 65_536
READ_BYTES = 65_536
_WAL_VERSION = 3_007_000
_NATIVE = '<' if sys.byteorder == 'little' else '>'


class SnapshotUnavailable(ValueError):
    def __init__(self):
        super().__init__('job_evidence_unavailable')


class SnapshotMissing(FileNotFoundError):
    def __init__(self):
        super().__init__('job_missing')


class SnapshotBudget:
    """Standalone legacy reads have a finite budget; traces pass their own."""

    def __init__(self):
        # Admission already owns a five-second publication loop. A legacy
        # read must not add another five seconds to each pass through it.
        self.deadline = time.monotonic() + 0.1

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.monotonic())

    @property
    def expired(self):
        return self.remaining_seconds() <= 0

    def check(self):
        if self.expired:
            raise SnapshotUnavailable()


def _require(condition):
    if not condition:
        raise SnapshotUnavailable()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _stat_at(directory, name):
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _read(descriptor, length, budget, *, offset=0):
    budget.check()
    _require(0 <= length <= SOURCE_BYTES)
    result = bytearray(length)
    budget.check()
    for start in range(0, length, READ_BYTES):
        budget.check()
        chunk = os.pread(descriptor, min(READ_BYTES, length - start), offset + start)
        _require(len(chunk) == min(READ_BYTES, length - start))
        result[start:start + len(chunk)] = chunk
        budget.check()
    return result


def _compare(descriptor, expected, budget):
    for start in range(0, len(expected), READ_BYTES):
        budget.check()
        end = min(start + READ_BYTES, len(expected))
        _require(os.pread(descriptor, end - start, start) == expected[start:end])
        budget.check()


def _resize(image, size, budget):
    while len(image) < size:
        budget.check()
        image.extend(bytes(min(READ_BYTES, size - len(image))))
        budget.check()
    while len(image) > size:
        budget.check()
        del image[max(size, len(image) - READ_BYTES):]
        budget.check()


def _checksum(data, endian, state, budget):
    budget.check()
    _require(len(data) <= READ_BYTES and len(data) % 8 == 0)
    first, second = state
    for left, right in struct.iter_unpack(endian + 'II', data):
        first = (first + left + second) & 0xffffffff
        second = (second + right + first) & 0xffffffff
    budget.check()
    return first, second


def _page_size(value):
    _require(512 <= value <= 65_536 and value & (value - 1) == 0)
    return value


def _database_header(data):
    _require(len(data) >= 100 and data[:16] == b'SQLite format 3\x00')
    value = int.from_bytes(data[16:18], 'big')
    page_size = _page_size(65_536 if value == 1 else value)
    _require(len(data) % page_size == 0 and len(data) <= IMAGE_BYTES)
    _require(data[18:20] in (b'\x01\x01', b'\x02\x02'))
    _require(data[20] <= page_size - 480)
    return page_size


def _index_header(descriptor, budget):
    first = _read(descriptor, 96, budget)
    _require(first == _read(descriptor, 96, budget))
    _require(first[:48] == first[48:96])
    header = first[:48]
    (version, unused, change, initialized, big_endian, page_size,
     frames, pages, checksum_a, checksum_b) = struct.unpack('=IIIBBHIIII', header[:32])
    _require(version == _WAL_VERSION and initialized == 1 and big_endian in (0, 1))
    _require(_checksum(header[:40], _NATIVE, (0, 0), budget) ==
             struct.unpack('=II', header[40:48]))
    page_size = _page_size(65_536 if page_size == 1 else page_size)
    _require(frames <= FRAME_LIMIT and pages <= IMAGE_BYTES // page_size)
    return {'raw': bytes(first), 'page_size': page_size, 'frames': frames,
            'pages': pages, 'big_endian': big_endian, 'salt': bytes(header[32:40]),
            'checksum': (checksum_a, checksum_b)}


def _wal_header(data, head, budget):
    _require(len(data) >= 32)
    magic, version, page_size, sequence = struct.unpack('>IIII', data[:16])
    _require(magic in (0x377f0682, 0x377f0683) and version == _WAL_VERSION)
    _require(_page_size(page_size) == head['page_size'])
    _require(magic & 1 == head['big_endian'] and data[16:24] == head['salt'])
    endian = '>' if magic & 1 else '<'
    checksum = _checksum(data[:24], endian, (0, 0), budget)
    _require(checksum == struct.unpack('>II', data[24:32]))
    return endian, checksum


def _capture(path, budget):
    """One optimistic capture; no lock, SQLite connection, or retry on source."""
    budget.check()
    _require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY') and
             hasattr(os, 'pread') and hasattr(os, 'geteuid'))
    database = Path(path).expanduser().absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
    directory = None
    descriptors = {}
    sources = {}
    try:
        try:
            directory = os.open(database.parent, flags | os.O_DIRECTORY)
        except FileNotFoundError:
            raise SnapshotMissing() from None
        directory_info = os.fstat(directory)
        _require(stat.S_ISDIR(directory_info.st_mode) and directory_info.st_uid == os.geteuid())
        for suffix in ('', '-wal', '-shm', '-journal'):
            budget.check()
            name = database.name + suffix
            info = _stat_at(directory, name)
            if info is None:
                if not suffix:
                    raise SnapshotMissing()
                sources[suffix] = (name, None)
                continue
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1)
            descriptor = os.open(name, flags, dir_fd=directory)
            descriptors[suffix] = descriptor
            _require(_identity(info) == _identity(os.fstat(descriptor)))
            sources[suffix] = (name, info)
        _require(sources['-journal'][1] is None or sources['-journal'][1].st_size == 0)
        database_size = sources[''][1].st_size
        wal_size = sources['-wal'][1].st_size if sources['-wal'][1] is not None else 0
        _require(0 <= database_size <= IMAGE_BYTES and database_size + wal_size <= SOURCE_BYTES)
        head = _index_header(descriptors['-shm'], budget) if '-shm' in descriptors else None
        _require(wal_size == 0 or head is not None)
        _require(wal_size != 0 or head is None or head['frames'] == 0)
        before_wal_header = _read(descriptors['-wal'], min(32, wal_size), budget) if wal_size else bytearray()

        def stable():
            budget.check()
            _require(_identity(os.fstat(directory)) == _identity(directory_info))
            _require(_identity(os.stat(database.parent, follow_symlinks=False)) == _identity(directory_info))
            for suffix, (name, info) in sources.items():
                current = _stat_at(directory, name)
                if info is None:
                    _require(current is None)
                else:
                    _require(current is not None and _identity(current) == _identity(info))
                    _require(_identity(os.fstat(descriptors[suffix])) == _identity(info))
            if head is not None:
                _require(_index_header(descriptors['-shm'], budget)['raw'] == head['raw'])
            if wal_size:
                _require(_read(descriptors['-wal'], min(32, wal_size), budget) == before_wal_header)
            budget.check()

        base = _read(descriptors[''], database_size, budget)
        wal = _read(descriptors['-wal'], wal_size, budget) if wal_size else bytearray()
        stable()
        prefix = 0 if head is None else 32 + head['frames'] * (24 + head['page_size'])
        if wal_size:
            _require(prefix <= wal_size)
        _compare(descriptors[''], base, budget)
        if wal_size:
            _compare(descriptors['-wal'], memoryview(wal)[:prefix], budget)
        stable()
        return base, wal, head
    finally:
        for descriptor in descriptors.values():
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def _image(base, wal, head, budget):
    budget.check()
    page_size = _database_header(base)
    budget.check()
    if head is not None:
        _require(head['page_size'] == page_size)
    endian, checksum = _wal_header(wal, head, budget) if wal else (None, None)
    if head is None or head['frames'] == 0:
        _require(head is None or head['pages'] in (0, len(base) // page_size))
        image = base
    else:
        _require(base[18:20] == b'\x02\x02' and bool(wal))
        image = base
        covered = len(base) // page_size
        beyond = set()
        frame_size = 24 + page_size
        committed_pages = 0
        for number in range(head['frames']):
            budget.check()
            start = 32 + number * frame_size
            frame = memoryview(wal)[start:start + frame_size]
            _require(len(frame) == frame_size)
            page, committed_pages = struct.unpack('>II', frame[:8])
            _require(0 < page <= IMAGE_BYTES // page_size and
                     committed_pages <= IMAGE_BYTES // page_size)
            _require(frame[8:16] == head['salt'])
            checksum = _checksum(frame[:8], endian, checksum, budget)
            checksum = _checksum(frame[24:], endian, checksum, budget)
            _require(checksum == struct.unpack('>II', frame[16:24]))
            end = page * page_size
            if end > len(image):
                _resize(image, end, budget)
            budget.check()
            image[end - page_size:end] = frame[24:]
            budget.check()
            if page > covered:
                beyond.add(page)
                while covered + 1 in beyond:
                    covered += 1
                    beyond.remove(covered)
                    if covered % 512 == 0:
                        budget.check()
            if committed_pages:
                # The base may already include a later checkpoint truncation,
                # so only the final view must have every page. Still discard
                # pages removed by each commit before their numbers are reused.
                if len(image) > committed_pages * page_size:
                    _resize(image, committed_pages * page_size, budget)
                covered = min(covered, committed_pages)
                # The tuple is capped by FRAME_LIMIT; checks bound the walk.
                budget.check()
                for index, pending_page in enumerate(tuple(beyond)):
                    if index % 512 == 0:
                        budget.check()
                    if pending_page > committed_pages:
                        beyond.remove(pending_page)
            budget.check()
        _require(committed_pages > 0 and committed_pages == head['pages'] and
                 committed_pages <= covered and checksum == head['checksum'])
    _require(_database_header(image) == page_size)
    budget.check()
    declared_pages = int.from_bytes(image[28:32], 'big')
    if declared_pages and image[24:28] == image[92:96]:
        _require(declared_pages * page_size == len(image))
    image[18:20] = b'\x01\x01'
    budget.check()
    return image


def read_connection(path, *, budget):
    """Return a private query-only connection, or a closed unavailable result."""
    connection = None
    try:
        budget.check()
        _require(hasattr(sqlite3.Connection, 'deserialize'))
        base, wal, head = _capture(path, budget)
        image = _image(base, wal, head, budget)
        connection = sqlite3.connect(':memory:', timeout=min(2, budget.remaining_seconds()))
        connection.set_progress_handler(lambda: int(budget.expired), 1000)
        connection.execute('PRAGMA temp_store=MEMORY')
        budget.check()
        connection.deserialize(image)
        budget.check()
        connection.execute('PRAGMA query_only=ON')
        connection.row_factory = sqlite3.Row
        budget.check()
        return connection
    except MemoryError:
        if connection is not None:
            connection.close()
        raise SnapshotUnavailable() from None
    except BaseException:
        if connection is not None:
            connection.close()
        raise
