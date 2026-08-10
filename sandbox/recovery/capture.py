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

    @staticmethod
    def _validate_artifacts(artifacts: object) -> dict[str, bytes]:
        if not isinstance(artifacts, dict) or not artifacts:
            raise RecoveryError("recovery set has no artifacts", "empty_set")
        for name, value in artifacts.items():
            if (not isinstance(name, str) or not name or name.startswith("/") or
                    ".." in Path(name).parts or any(ord(char) < 32 or ord(char) == 127 for char in name) or
                    not isinstance(value, bytes) or not value):
                raise RecoveryError("recovery artifact is invalid", "invalid_artifact")
        return artifacts

    def publish(self, set_id: str, artifacts: dict[str, bytes]) -> dict:
        if not _valid_set_id(set_id): raise RecoveryError("recovery set id is invalid", "invalid_set_id")
        artifacts = self._validate_artifacts(artifacts)
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
        if not _valid_set_id(set_id):
            return False
        try:
            from .restore import verify_manifest
            verify_manifest(self.drive, set_id)
        except (KeyError, TypeError, ValueError, RecoveryError):
            return False
        return True


class StagingCaptureCoordinator:
    """Owner-only file staging and manifest-last publication for real adapters.

    It owns only a newly-created staging directory.  Profile adapters must pass
    already validated artifact files, keeping database/filesystem/Git mechanics
    separate from publication ordering.
    """
    def __init__(self, crypto, drive, *, staging_root: str | Path | None = None,
                 pending_root: str | Path | None = None,
                 materialization_root: str | Path | None = None, clock=None) -> None:
        self.crypto, self.drive = crypto, drive
        self.staging_root = Path(staging_root) if staging_root else None
        self.pending_root = Path(pending_root) if pending_root else None
        self.materialization_root = Path(materialization_root).resolve() if materialization_root else None
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat())

    def _stage(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="set-", dir=self.staging_root))
        os.chmod(directory, 0o700)
        return directory

    def publish_files(self, set_id: str, artifacts: dict[str, str | Path], *,
                      profiles: tuple[str, ...], provenance: dict | None = None,
                      profile_bindings: dict | None = None) -> dict:
        if not _valid_set_id(set_id):
            raise RecoveryError("recovery set id is invalid", "invalid_set_id")
        if (not isinstance(artifacts, dict) or not artifacts or
                not isinstance(profiles, tuple) or not profiles or
                not all(isinstance(profile, str) and profile and
                        not any(ord(char) < 32 or ord(char) == 127 for char in profile)
                        for profile in profiles) or len(set(profiles)) != len(profiles)):
            raise RecoveryError("recovery set requires artifacts and profiles", "empty_set")
        if provenance is not None and not isinstance(provenance, dict):
            raise RecoveryError("recovery provenance is invalid", "invalid_provenance")
        if profile_bindings is not None and (not isinstance(profile_bindings, dict) or
                                             set(profile_bindings) != set(profiles)):
            raise RecoveryError("recovery profile bindings are invalid", "invalid_manifest_binding")
        for name, value in artifacts.items():
            if (not isinstance(name, str) or not name or name.startswith("/") or
                    ".." in Path(name).parts or any(ord(char) < 32 or ord(char) == 127 for char in name) or
                    not isinstance(value, (str, Path))):
                raise RecoveryError("recovery artifact name is invalid", "invalid_artifact")
        if not all(self._is_regular_nonempty_file(Path(value)) for value in artifacts.values()):
            raise RecoveryError("recovery artifact is unavailable", "missing_artifact")
        if self.materialization_root is not None:
            for value in artifacts.values():
                try:
                    Path(value).resolve().relative_to(self.materialization_root)
                except ValueError as exc:
                    raise RecoveryError("recovery artifact is outside owned materialization", "invalid_artifact") from exc
        stage = self._stage()
        verified_ciphertext = None
        try:
            archive = stage / "archive.tar"
            records = []
            with tarfile.open(archive, "w") as output:
                for name, source in sorted(artifacts.items()):
                    if (not isinstance(name, str) or not name or name.startswith("/") or
                            ".." in Path(name).parts or
                            any(ord(char) < 32 or ord(char) == 127 for char in name)):
                        raise RecoveryError("recovery artifact name is invalid", "invalid_artifact")
                    source = Path(source)
                    before = self._file_snapshot(source)
                    output.add(source, arcname=name, recursive=False)
                    after = self._file_snapshot(source)
                    if after != before:
                        raise RecoveryError("recovery artifact changed during capture", "source_changed")
                    records.append({"name": name, "sha256": before[4],
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
            if profile_bindings is not None:
                manifest["profile_bindings"] = profile_bindings
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
    def _file_snapshot(cls, path: Path) -> tuple[int, int, int, int, str]:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise RecoveryError("recovery artifact is unavailable", "missing_artifact") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise RecoveryError("recovery artifact is unavailable", "missing_artifact")
        return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns,
                sha256_file(path))
