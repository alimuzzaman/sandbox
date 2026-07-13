"""Small deterministic fakes for recovery unit tests."""
from __future__ import annotations


class RecordingClock:
    def __init__(self, now: int = 0) -> None:
        self.now = now

    def time(self) -> int:
        return self.now


class RecordingLock:
    def __init__(self) -> None:
        self.acquired = False

    def acquire(self) -> bool:
        if self.acquired:
            return False
        self.acquired = True
        return True

    def release(self) -> None:
        self.acquired = False


class RecordingDrive:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, payload: bytes) -> None:
        self.objects[key] = payload

    def get(self, key: str) -> bytes:
        return self.objects[key]


class RecordingCrypto:
    def encrypt(self, payload: bytes) -> bytes:
        return b"encrypted:" + payload

    def decrypt(self, payload: bytes) -> bytes:
        assert payload.startswith(b"encrypted:")
        return payload[len(b"encrypted:"):]


class RecordingDatabase:
    def __init__(self, dump: bytes = b"database") -> None: self.dump = dump; self.calls = []
    def capture(self, profile: str) -> bytes: self.calls.append(profile); return self.dump


class RecordingFilesystem:
    def __init__(self, archive: bytes = b"archive") -> None: self.archive = archive; self.calls = []
    def capture(self, profile: str) -> bytes: self.calls.append(profile); return self.archive
