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
        if self.staging_root is not None:
            if self.staging_root.is_symlink():
                raise RecoveryError("recovery staging root is invalid", "invalid_staging_root")
            self.staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = self.staging_root.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise RecoveryError("recovery staging root is not owner-controlled", "invalid_staging_root")
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                os.chmod(self.staging_root, 0o700)
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
        manifest = None
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
            # The manifest is computed before any upload so a preserved pending
            # ciphertext can carry it as a sidecar (spec 058 R11).
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
            self.drive.put_file(cipher_key, ciphertext)
            self.drive.verify_file(cipher_key, ciphertext)
            # This write is intentionally last: the manifest is the sole complete-set marker.
            self.drive.put(f"sets/{set_id}/manifest.json", json.dumps(manifest, sort_keys=True).encode())
            return manifest
        except BaseException:
            if verified_ciphertext is not None and self.pending_root is not None:
                self._preserve_pending(set_id, verified_ciphertext, manifest)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _pending_paths(self, set_id: str) -> tuple[Path, Path]:
        return (self.pending_root / f"{set_id}.archive.tar.gpg",
                self.pending_root / f"{set_id}.manifest.json")

    @staticmethod
    def _copy_private(source: Path | None, target: Path, payload: bytes | None = None) -> None:
        temporary = target.with_name(target.name + ".pending")
        temporary.unlink(missing_ok=True)
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                if payload is not None:
                    stream.write(payload)
                else:
                    with open(source, "rb") as reader:
                        shutil.copyfileobj(reader, stream, 1024 * 1024)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _preserve_pending(self, set_id: str, ciphertext: Path, manifest: dict | None = None) -> Path:
        self.pending_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.pending_root, 0o700)
        target, sidecar = self._pending_paths(set_id)
        if target.exists():
            raise RecoveryError("pending recovery artifact already exists", "pending_artifact_exists")
        self._copy_private(ciphertext, target)
        if manifest is not None:
            try:
                self._copy_private(None, sidecar, json.dumps(manifest, sort_keys=True).encode())
            except OSError:
                # The ciphertext alone is still recoverable (derived manifest path).
                sidecar.unlink(missing_ok=True)
        return target

    @staticmethod
    def _owned_file(path: Path) -> bool:
        try:
            metadata = path.lstat()
        except OSError:
            return False
        return (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid()
                and metadata.st_size > 0)

    def publish_pending(self, set_id: str, *, bindings_for=None) -> dict:
        """Finish a locally pending verified ciphertext (spec 058 FR-029, R11).

        With a sidecar the ciphertext must match its recorded hash and size; a
        decryption proves the current passphrase.  Without one the manifest is
        derived from the decrypted members and marked ``pending_recovered``.
        Upload tolerates an identical object already present, the manifest is
        written last, and both pending files are removed only after success.
        """
        if not _valid_set_id(set_id):
            raise RecoveryError("recovery set id is invalid", "invalid_set_id")
        if self.pending_root is None:
            raise RecoveryError("recovery pending root is not configured", "recovery_not_configured")
        ciphertext, sidecar = self._pending_paths(set_id)
        if not self._owned_file(ciphertext):
            raise RecoveryError("pending recovery artifact is invalid", "pending_artifact_invalid")
        manifest = None
        if sidecar.exists() or sidecar.is_symlink():
            try:
                if not self._owned_file(sidecar) or sidecar.lstat().st_size > 1024 * 1024:
                    raise ValueError
                manifest = json.loads(sidecar.read_text())
            except (OSError, ValueError) as exc:
                raise RecoveryError("pending recovery manifest is invalid",
                                    "pending_artifact_invalid") from exc
            if (not isinstance(manifest, dict) or manifest.get("id") != set_id
                    or manifest.get("ciphertext_object") != f"sets/{set_id}/archive.tar.gpg"
                    or manifest.get("ciphertext_sha256") != sha256_file(ciphertext)
                    or manifest.get("ciphertext_size") != ciphertext.stat().st_size):
                raise RecoveryError("pending ciphertext does not match its manifest",
                                    "pending_artifact_invalid")
        stage = self._stage()
        try:
            plaintext = stage / "archive.tar"
            try:
                self.crypto.decrypt_file(ciphertext, plaintext)
            except RecoveryError as exc:
                raise RecoveryError("pending ciphertext does not decrypt with the current passphrase",
                                    "passphrase_not_current") from exc
            plaintext_hash = sha256_file(plaintext)
            if manifest is not None:
                if manifest.get("plaintext_sha256") != plaintext_hash:
                    raise RecoveryError("pending ciphertext does not match its manifest",
                                        "pending_artifact_invalid")
            else:
                manifest = self._derived_manifest(set_id, ciphertext, plaintext, plaintext_hash,
                                                  stage, bindings_for)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        cipher_key = manifest["ciphertext_object"]
        try:
            self.drive.put_file(cipher_key, ciphertext)
        except RecoveryError as exc:
            # rclone --immutable accepts an identical object; verification decides.
            if exc.code != "object_exists":
                raise
        self.drive.verify_file(cipher_key, ciphertext)
        self.drive.put(f"sets/{set_id}/manifest.json", json.dumps(manifest, sort_keys=True).encode())
        ciphertext.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        return manifest

    def _derived_manifest(self, set_id: str, ciphertext: Path, plaintext: Path,
                          plaintext_hash: str, stage: Path, bindings_for) -> dict:
        records, profiles = [], set()
        try:
            with tarfile.open(plaintext, "r:") as archive:
                for member in archive:
                    name = member.name
                    if (not member.isfile() or not name or name.startswith("/")
                            or ".." in Path(name).parts or len(Path(name).parts) < 2):
                        raise RecoveryError("pending archive member is invalid",
                                            "pending_artifact_invalid")
                    digest, size = hashlib.sha256(), 0
                    stream = archive.extractfile(member)
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                        size += len(block)
                    records.append({"name": name, "sha256": digest.hexdigest(), "size": size})
                    profiles.add(Path(name).parts[0])
        except tarfile.TarError as exc:
            raise RecoveryError("pending archive is invalid", "pending_artifact_invalid") from exc
        if not records:
            raise RecoveryError("pending archive is empty", "pending_artifact_invalid")
        profiles = tuple(sorted(profiles))
        manifest = {
            "schema_version": 1, "id": set_id, "status": "complete", "created_at": self.clock(),
            "profiles": list(profiles), "artifacts": sorted(records, key=lambda item: item["name"]),
            "exclusions": [], "provenance": {"pending_recovered": True},
            "ciphertext_object": f"sets/{set_id}/archive.tar.gpg",
            "ciphertext_sha256": sha256_file(ciphertext),
            "ciphertext_size": ciphertext.stat().st_size, "plaintext_sha256": plaintext_hash,
            "restore_compatibility": "sandbox-recovery-v1",
        }
        if bindings_for is not None:
            manifest["profile_bindings"] = bindings_for(profiles)
        return manifest

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
