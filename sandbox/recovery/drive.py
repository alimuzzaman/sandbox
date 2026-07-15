from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path

from .errors import RecoveryError
from .integrity import sha256_file

_DESTINATION = re.compile(r"^[A-Za-z0-9_.-]+:[^\n\r]*$")


class RcloneDrive:
    """Immutable rclone object store with upload and downloaded-hash verification."""
    def __init__(self, runner, destination: str) -> None:
        if not _DESTINATION.fullmatch(destination):
            raise RecoveryError("rclone destination is invalid", "invalid_destination")
        remote_path = destination.split(":", 1)[1]
        if ".." in Path(remote_path).parts:
            raise RecoveryError("rclone destination path is invalid", "invalid_destination")
        self.runner, self.destination = runner, destination.rstrip("/")

    def _remote(self, key: str) -> str:
        if key == "":
            return self.destination
        if key.startswith("/") or ".." in Path(key).parts:
            raise RecoveryError("recovery object key is invalid", "invalid_object_key")
        return f"{self.destination}/{key}"

    @staticmethod
    def _ok(result, code: str) -> None:
        if result.returncode != 0:
            raise RecoveryError("Drive operation failed", code)

    def put(self, key: str, payload: bytes) -> None:
        remote = self._remote(key)
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-upload-") as directory:
            source = Path(directory) / "object"
            source.write_bytes(payload)
            self.put_file(key, source)

    def put_file(self, key: str, source: str | Path) -> None:
        source = Path(source)
        if not source.is_file() or not source.stat().st_size:
            raise RecoveryError("recovery upload source is unavailable", "invalid_upload_source")
        # copyto does not delete unrelated Drive objects; --immutable refuses overwrite.
        self._ok(self.runner.run(("rclone", "copyto", "--immutable", str(source), self._remote(key)), timeout=3600), "drive_upload_failed")

    def get(self, key: str) -> bytes:
        remote = self._remote(key)
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-download-") as directory:
            target = Path(directory) / "object"
            self._ok(self.runner.run(("rclone", "copyto", remote, str(target)), timeout=3600), "drive_download_failed")
            if not target.exists():
                raise RecoveryError("Drive object was not downloaded", "object_missing")
            return target.read_bytes()

    def verify(self, key: str, payload: bytes) -> None:
        if hashlib.sha256(self.get(key)).digest() != hashlib.sha256(payload).digest():
            raise RecoveryError("Drive object hash does not match", "drive_verification_failed")

    def verify_file(self, key: str, source: str | Path) -> None:
        source = Path(source)
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-verify-") as directory:
            downloaded = Path(directory) / "object"
            self._ok(self.runner.run(("rclone", "copyto", self._remote(key), str(downloaded)), timeout=3600), "drive_download_failed")
            if not downloaded.exists() or sha256_file(source) != sha256_file(downloaded):
                raise RecoveryError("Drive object hash does not match", "drive_verification_failed")

    def list(self, prefix: str = "sets") -> tuple[dict, ...]:
        remote = self._remote(prefix)
        result = self.runner.run(("rclone", "lsjson", "--recursive", remote), timeout=120)
        self._ok(result, "drive_list_failed")
        try:
            items = json.loads(result.stdout or "[]")
        except ValueError as exc:
            raise RecoveryError("Drive listing is invalid", "drive_list_failed") from exc
        return tuple(item for item in items if isinstance(item, dict))


class MemoryDrive:
    """Fixture-only immutable object store used by recovery tests."""
    def __init__(self) -> None: self.objects: dict[str, bytes] = {}
    def put(self, key: str, payload: bytes) -> None:
        if key in self.objects: raise RecoveryError("recovery object already exists", "object_exists")
        self.objects[key] = payload
    def get(self, key: str) -> bytes:
        try: return self.objects[key]
        except KeyError as exc: raise RecoveryError("recovery object is absent", "object_missing") from exc
    def verify(self, key: str, payload: bytes) -> None:
        if self.get(key) != payload: raise RecoveryError("Drive object hash does not match", "drive_verification_failed")
    def put_file(self, key: str, source: str | Path) -> None:
        self.put(key, Path(source).read_bytes())
    def verify_file(self, key: str, source: str | Path) -> None:
        if self.get(key) != Path(source).read_bytes():
            raise RecoveryError("Drive object hash does not match", "drive_verification_failed")
    def list(self, prefix: str = "sets") -> tuple[dict, ...]:
        return tuple({"Path": key, "Size": len(value)} for key, value in sorted(self.objects.items()) if key.startswith(prefix))
