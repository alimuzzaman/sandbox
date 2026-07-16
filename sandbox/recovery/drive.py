from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import shutil
from pathlib import Path

from .errors import RecoveryError
from .integrity import sha256_file

_DESTINATION = re.compile(r"^[A-Za-z0-9_.-]+:[^\n\r]*$")


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


class RcloneDrive:
    """Immutable rclone object store with upload and downloaded-hash verification."""
    def __init__(self, runner, destination: str) -> None:
        if (not isinstance(destination, str) or _has_control(destination) or
                not _DESTINATION.fullmatch(destination)):
            raise RecoveryError("rclone destination is invalid", "invalid_destination")
        remote_path = destination.split(":", 1)[1]
        if ".." in Path(remote_path).parts:
            raise RecoveryError("rclone destination path is invalid", "invalid_destination")
        self.runner, self.destination = runner, destination.rstrip("/")

    def _remote(self, key: str) -> str:
        if not isinstance(key, str) or _has_control(key):
            raise RecoveryError("recovery object key is invalid", "invalid_object_key")
        if key == "":
            return self.destination
        if key.startswith("/") or ".." in Path(key).parts:
            raise RecoveryError("recovery object key is invalid", "invalid_object_key")
        return f"{self.destination}/{key}"

    @staticmethod
    def _ok(result, code: str) -> None:
        if result.returncode != 0:
            raise RecoveryError("Drive operation failed", code)

    @staticmethod
    def _downloaded_file(path: Path) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RecoveryError("Drive object was not downloaded", "object_missing") from exc
        if not stat.S_ISREG(metadata.st_mode) or not metadata.st_size:
            raise RecoveryError("Drive object download is invalid", "invalid_download")

    def put(self, key: str, payload: bytes) -> None:
        remote = self._remote(key)
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-upload-") as directory:
            source = Path(directory) / "object"
            source.write_bytes(payload)
            self.put_file(key, source)

    def put_file(self, key: str, source: str | Path) -> None:
        source = Path(source)
        try:
            metadata = source.lstat()
        except OSError:
            metadata = None
        if metadata is None or not stat.S_ISREG(metadata.st_mode) or not metadata.st_size:
            raise RecoveryError("recovery upload source is unavailable", "invalid_upload_source")
        # copyto does not delete unrelated Drive objects; --immutable refuses overwrite.
        self._ok(self.runner.run(("rclone", "copyto", "--immutable", str(source), self._remote(key)), timeout=3600), "drive_upload_failed")

    def get(self, key: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-download-") as directory:
            target = Path(directory) / "object"
            self.get_file(key, target)
            return target.read_bytes()

    def get_file(self, key: str, target: str | Path) -> Path:
        remote = self._remote(key)
        target = Path(target)
        if target.is_symlink() or (target.exists() and not stat.S_ISREG(target.lstat().st_mode)):
            raise RecoveryError("Drive download target is invalid", "invalid_download_target")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_directory = Path(tempfile.mkdtemp(prefix="sandbox-recovery-download-", dir=target.parent))
        os.chmod(temporary_directory, 0o700)
        temporary = temporary_directory / "object"
        try:
            self._ok(self.runner.run(("rclone", "copyto", remote, str(temporary)), timeout=3600), "drive_download_failed")
            self._downloaded_file(temporary)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)
        return target

    def verify(self, key: str, payload: bytes) -> None:
        if hashlib.sha256(self.get(key)).digest() != hashlib.sha256(payload).digest():
            raise RecoveryError("Drive object hash does not match", "drive_verification_failed")

    def verify_file(self, key: str, source: str | Path) -> None:
        source = Path(source)
        try:
            metadata = source.lstat()
        except OSError:
            metadata = None
        if metadata is None or not stat.S_ISREG(metadata.st_mode) or not metadata.st_size:
            raise RecoveryError("recovery verification source is unavailable", "invalid_verification_source")
        with tempfile.TemporaryDirectory(prefix="sandbox-recovery-verify-") as directory:
            downloaded = Path(directory) / "object"
            self._ok(self.runner.run(("rclone", "copyto", self._remote(key), str(downloaded)), timeout=3600), "drive_download_failed")
            self._downloaded_file(downloaded)
            if sha256_file(source) != sha256_file(downloaded):
                raise RecoveryError("Drive object hash does not match", "drive_verification_failed")

    def list(self, prefix: str = "sets") -> tuple[dict, ...]:
        remote = self._remote(prefix)
        result = self.runner.run(("rclone", "lsjson", "--recursive", remote), timeout=120)
        self._ok(result, "drive_list_failed")
        try:
            items = json.loads(result.stdout or "[]")
        except ValueError as exc:
            raise RecoveryError("Drive listing is invalid", "drive_list_failed") from exc
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RecoveryError("Drive listing is invalid", "drive_list_failed")
        for item in items:
            path = item.get("Path")
            size = item.get("Size")
            if (not isinstance(path, str) or not path or _has_control(path) or path.startswith("/") or
                    ".." in Path(path).parts or
                    (size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0))):
                raise RecoveryError("Drive listing is invalid", "drive_list_failed")
        return tuple(items)


class MemoryDrive:
    """Fixture-only immutable object store used by recovery tests."""
    def __init__(self) -> None: self.objects: dict[str, bytes] = {}
    def put(self, key: str, payload: bytes) -> None:
        if key in self.objects: raise RecoveryError("recovery object already exists", "object_exists")
        self.objects[key] = payload
    def get(self, key: str) -> bytes:
        try: return self.objects[key]
        except KeyError as exc: raise RecoveryError("recovery object is absent", "object_missing") from exc
    def get_file(self, key: str, target: str | Path) -> Path:
        target = Path(target)
        if target.is_symlink() or (target.exists() and not stat.S_ISREG(target.lstat().st_mode)):
            raise RecoveryError("Drive download target is invalid", "invalid_download_target")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = self.objects[key]
        except KeyError as exc:
            raise RecoveryError("recovery object is absent", "object_missing") from exc
        temporary = target.with_name(target.name + ".pending")
        if temporary.exists() or temporary.is_symlink():
            raise RecoveryError("Drive download target is invalid", "invalid_download_target")
        temporary.write_bytes(payload)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        return target
    def verify(self, key: str, payload: bytes) -> None:
        if self.get(key) != payload: raise RecoveryError("Drive object hash does not match", "drive_verification_failed")
    def put_file(self, key: str, source: str | Path) -> None:
        self.put(key, Path(source).read_bytes())
    def verify_file(self, key: str, source: str | Path) -> None:
        if self.get(key) != Path(source).read_bytes():
            raise RecoveryError("Drive object hash does not match", "drive_verification_failed")
    def list(self, prefix: str = "sets") -> tuple[dict, ...]:
        return tuple({"Path": key, "Size": len(value)} for key, value in sorted(self.objects.items()) if key.startswith(prefix))
