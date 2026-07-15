from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
from datetime import datetime, timezone

from .errors import RecoveryError
from .integrity import sha256_file


def _valid_set_id(set_id: str) -> bool:
    return (isinstance(set_id, str) and bool(set_id) and set_id == Path(set_id).name and
            set_id.replace("-", "").replace("_", "").isalnum())


class CaptureCoordinator:
    def __init__(self, crypto, drive) -> None:
        self.crypto, self.drive = crypto, drive

    def publish(self, set_id: str, artifacts: dict[str, bytes]) -> dict:
        if not _valid_set_id(set_id): raise RecoveryError("recovery set id is invalid", "invalid_set_id")
        if not artifacts: raise RecoveryError("recovery set has no artifacts", "empty_set")
        payload = b"".join(name.encode() + b"\0" + value for name, value in sorted(artifacts.items()))
        ciphertext = self.crypto.encrypt(payload); cipher_key = f"sets/{set_id}/archive.bin"
        self.drive.put(cipher_key, ciphertext)
        if self.drive.get(cipher_key) != ciphertext or self.crypto.decrypt(ciphertext) != payload:
            raise RecoveryError("ciphertext verification failed", "ciphertext_verification_failed")
        manifest = {"schema_version": 1, "id": set_id, "status": "complete", "artifacts": sorted(artifacts),
                    "ciphertext_object": cipher_key, "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                    "ciphertext_size": len(ciphertext)}
        self.drive.put(f"sets/{set_id}/manifest.json", json.dumps(manifest, sort_keys=True).encode())
        return manifest

    def verify(self, set_id: str) -> bool:
        manifest = json.loads(self.drive.get(f"sets/{set_id}/manifest.json"))
        cipher = self.drive.get(manifest["ciphertext_object"])
        return (manifest.get("status") == "complete" and hashlib.sha256(cipher).hexdigest() == manifest["ciphertext_sha256"]
                and len(cipher) == manifest["ciphertext_size"])


class StagingCaptureCoordinator:
    """Owner-only file staging and manifest-last publication for real adapters.

    It owns only a newly-created staging directory.  Profile adapters must pass
    already validated artifact files, keeping database/filesystem/Git mechanics
    separate from publication ordering.
    """
    def __init__(self, crypto, drive, *, staging_root: str | Path | None = None,
                 pending_root: str | Path | None = None, clock=None) -> None:
        self.crypto, self.drive = crypto, drive
        self.staging_root = Path(staging_root) if staging_root else None
        self.pending_root = Path(pending_root) if pending_root else None
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def _stage(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="set-", dir=self.staging_root))
        os.chmod(directory, 0o700)
        return directory

    def publish_files(self, set_id: str, artifacts: dict[str, str | Path], *,
                      profiles: tuple[str, ...], provenance: dict | None = None) -> dict:
        if not _valid_set_id(set_id):
            raise RecoveryError("recovery set id is invalid", "invalid_set_id")
        if not artifacts or not profiles:
            raise RecoveryError("recovery set requires artifacts and profiles", "empty_set")
        if not all(self._is_regular_nonempty_file(Path(value)) for value in artifacts.values()):
            raise RecoveryError("recovery artifact is unavailable", "missing_artifact")
        stage = self._stage()
        verified_ciphertext = None
        try:
            archive = stage / "archive.tar"
            records = []
            with tarfile.open(archive, "w") as output:
                for name, source in sorted(artifacts.items()):
                    if (not isinstance(name, str) or not name or name.startswith("/") or
                            ".." in Path(name).parts):
                        raise RecoveryError("recovery artifact name is invalid", "invalid_artifact")
                    source = Path(source)
                    before = self._file_snapshot(source)
                    output.add(source, arcname=name, recursive=False)
                    if self._file_snapshot(source) != before:
                        raise RecoveryError("recovery artifact changed during capture", "source_changed")
                    records.append({"name": name, "sha256": sha256_file(source),
                                    "size": before[2]})
            ciphertext = stage / "archive.tar.gpg"
            self.crypto.encrypt_file(archive, ciphertext)
            plaintext_hash = self.crypto.verify_file(archive, ciphertext)
            verified_ciphertext = ciphertext
            cipher_key = f"sets/{set_id}/archive.tar.gpg"
            self.drive.put_file(cipher_key, ciphertext)
            self.drive.verify_file(cipher_key, ciphertext)
            manifest = {
                "schema_version": 1, "id": set_id, "status": "complete", "created_at": self.clock(),
                "profiles": sorted(profiles), "artifacts": records, "exclusions": [],
                "provenance": provenance or {}, "ciphertext_object": cipher_key,
                "ciphertext_sha256": sha256_file(ciphertext),
                "ciphertext_size": ciphertext.stat().st_size, "plaintext_sha256": plaintext_hash,
                "restore_compatibility": "sandbox-recovery-v1",
            }
            # This write is intentionally last: the manifest is the sole complete-set marker.
            self.drive.put(f"sets/{set_id}/manifest.json", json.dumps(manifest, sort_keys=True).encode())
            return manifest
        except BaseException:
            if verified_ciphertext is not None and self.pending_root is not None:
                self._preserve_pending(set_id, verified_ciphertext)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _preserve_pending(self, set_id: str, ciphertext: Path) -> Path:
        self.pending_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.pending_root, 0o700)
        target = self.pending_root / f"{set_id}.archive.tar.gpg"
        if target.exists():
            raise RecoveryError("pending recovery artifact already exists", "pending_artifact_exists")
        temporary = target.with_name(target.name + ".pending")
        try:
            shutil.copyfile(ciphertext, temporary)
            os.chmod(temporary, 0o600)
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target

    @staticmethod
    def _is_regular_nonempty_file(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            return False
        return stat.S_ISREG(metadata.st_mode) and metadata.st_size > 0

    @classmethod
    def _file_snapshot(cls, path: Path) -> tuple[int, int, int, int]:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RecoveryError("recovery artifact is unavailable", "missing_artifact") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise RecoveryError("recovery artifact is unavailable", "missing_artifact")
        return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
