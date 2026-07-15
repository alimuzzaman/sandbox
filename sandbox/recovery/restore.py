from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from typing import Mapping

from .errors import RecoveryError
from .filesystem import validate_archive
from .models import RestorePlan


_COMPATIBILITY = "sandbox-recovery-v1"


def _canonical_ciphertext_keys(set_id: str) -> tuple[str, ...]:
    return (f"sets/{set_id}/archive.bin", f"sets/{set_id}/archive.tar.gpg")


def verify_manifest(drive, set_id: str) -> dict:
    try:
        manifest = json.loads(drive.get(f"sets/{set_id}/manifest.json"))
    except (ValueError, RecoveryError) as exc:
        raise RecoveryError("recovery manifest is unavailable or invalid", "invalid_manifest") from exc
    if not isinstance(manifest, dict):
        raise RecoveryError("recovery manifest is unavailable or invalid", "invalid_manifest")
    required = {"schema_version", "id", "status", "ciphertext_object", "ciphertext_sha256", "ciphertext_size"}
    if (manifest.get("schema_version") != 1 or manifest.get("id") != set_id or
            manifest.get("status") != "complete" or not required <= set(manifest)):
        raise RecoveryError("recovery set is not restorable", "invalid_manifest")
    compatibility = manifest.get("restore_compatibility")
    if compatibility not in (None, _COMPATIBILITY):
        raise RecoveryError("recovery set requires an incompatible restore tool", "incompatible_restore")
    digest = manifest["ciphertext_sha256"]
    size = manifest["ciphertext_size"]
    if (not isinstance(manifest["ciphertext_object"], str) or
            not isinstance(digest, str) or len(digest) != 64 or
            any(char not in "0123456789abcdef" for char in digest) or
            isinstance(size, bool) or not isinstance(size, int) or size < 1):
        raise RecoveryError("recovery manifest fields are invalid", "invalid_manifest")
    if manifest["ciphertext_object"] not in _canonical_ciphertext_keys(set_id):
        raise RecoveryError("recovery ciphertext is not bound to its manifest", "invalid_manifest")
    ciphertext = drive.get(manifest["ciphertext_object"])
    if not isinstance(ciphertext, bytes) or (len(ciphertext) != size or
            hashlib.sha256(ciphertext).hexdigest() != digest):
        raise RecoveryError("recovery ciphertext does not match manifest", "ciphertext_verification_failed")
    return manifest


def _ordered_profiles(selected: tuple[str, ...], dependencies: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    ordered, visiting, visited = [], set(), set()
    def visit(profile: str) -> None:
        if profile in visiting:
            raise RecoveryError("restore profile dependency cycle", "invalid_restore_dependencies")
        if profile in visited:
            return
        visiting.add(profile)
        for dependency in dependencies.get(profile, ()):
            if dependency not in selected:
                raise RecoveryError("selected restore omits a required dependency", "missing_restore_dependency")
            visit(dependency)
        visiting.remove(profile); visited.add(profile); ordered.append(profile)
    for profile in selected:
        visit(profile)
    return tuple(ordered)


def build_restore_plan(drive, set_id: str, profiles: tuple[str, ...] = (), *,
                       dependencies: Mapping[str, tuple[str, ...]] | None = None,
                       target_root: str | Path | None = None, required_bytes: int = 0) -> RestorePlan:
    manifest = verify_manifest(drive, set_id)
    available = tuple(manifest.get("profiles") or profiles)
    selected = profiles or available
    if set(selected) - set(available):
        raise RecoveryError("restore profile is not in the recovery set", "unknown_profile")
    ordered = _ordered_profiles(tuple(selected), dependencies or {})
    if target_root is not None and shutil.disk_usage(target_root).free < required_bytes:
        raise RecoveryError("restore target has insufficient free space", "insufficient_free_space")
    actions = tuple(f"restore:{profile}" for profile in ordered)
    return RestorePlan(set_id, ordered, actions, ("verify-ciphertext",),
                       tuple(f"checkpoint:{profile}" for profile in ordered),
                       tuple(f"rollback:{profile}" for profile in reversed(ordered)), ())


def apply_restore(plan: RestorePlan, adapters: Mapping[str, object], *, confirm: bool = False) -> dict:
    """Apply a fixture/disposable restore with ordered checkpoints and rollback.

    Production callers must supply dedicated per-profile adapters.  This generic
    coordinator never guesses a target and refuses to mutate until confirmation.
    """
    if not confirm:
        raise RecoveryError("restore apply requires explicit confirmation", "confirmation_required")
    touched: list[str] = []
    events: list[str] = []
    try:
        for profile in plan.profiles:
            adapter = adapters.get(profile)
            if adapter is None:
                raise RecoveryError("restore adapter is unavailable", "missing_restore_adapter")
            touched.append(profile)
            for operation in ("checkpoint", "quiesce", "stage", "swap", "import", "verify", "resume"):
                getattr(adapter, operation)()
                events.append(f"{operation}:{profile}")
    except Exception as exc:
        for profile in reversed(touched):
            adapter = adapters[profile]
            try:
                adapter.rollback(); events.append(f"rollback:{profile}")
                adapter.resume(); events.append(f"resume:{profile}")
            except Exception:
                events.append(f"manual-intervention:{profile}")
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("restore apply failed and rollback was attempted", "restore_apply_failed") from exc
    return {"status": "complete", "events": tuple(events)}


class FilesystemRestoreAdapter:
    """Checkpointed filesystem restore adapter for an explicitly supplied target.

    The adapter is injectable and target-explicit: it never discovers a target from a
    profile or remote inventory. Callers must provide the decryption adapter, ciphertext
    file, and destination directory.
    """

    def __init__(self, crypto, ciphertext: str | Path, target: str | Path) -> None:
        self.crypto = crypto
        self.ciphertext = Path(ciphertext)
        self.target = Path(target)
        self._workspace: Path | None = None
        self._stage: Path | None = None
        self._previous: Path | None = None
        self._members: tuple[str, ...] = ()
        self._swapped = False

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    def checkpoint(self) -> None:
        if not self.ciphertext.is_file():
            raise RecoveryError("restore ciphertext is unavailable", "missing_ciphertext")
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self._workspace = Path(tempfile.mkdtemp(prefix=f".{self.target.name}.recovery-",
                                                 dir=self.target.parent))
        os.chmod(self._workspace, 0o700)
        checkpoint = self._workspace / "checkpoint"
        if self.target.is_dir():
            shutil.copytree(self.target, checkpoint, symlinks=True)
        elif self.target.exists() or self.target.is_symlink():
            shutil.copy2(self.target, checkpoint, follow_symlinks=False)

    def quiesce(self) -> None:
        return None

    def stage(self) -> None:
        if self._workspace is None:
            raise RecoveryError("restore checkpoint is unavailable", "restore_not_checkpointed")
        plaintext = self._workspace / "archive.tar"
        self.crypto.decrypt_file(self.ciphertext, plaintext)
        self._members = validate_archive(plaintext)
        extracted = self._workspace / "extracted"
        extracted.mkdir()
        with tarfile.open(plaintext, "r") as archive:
            if sys.version_info >= (3, 12):
                archive.extractall(extracted, filter="data")
            else:
                archive.extractall(extracted)
        self._stage = extracted

    def swap(self) -> None:
        if self._workspace is None or self._stage is None:
            raise RecoveryError("restore stage is unavailable", "restore_not_staged")
        self._previous = self._workspace / "previous"
        if self.target.exists() or self.target.is_symlink():
            self.target.replace(self._previous)
        self._stage.replace(self.target)
        self._swapped = True

    def import_(self) -> None:
        return None

    def verify(self) -> None:
        if not self.target.is_dir():
            raise RecoveryError("restored filesystem target is not a directory", "restore_verification_failed")
        for member in self._members:
            if not os.path.lexists(self.target / member):
                raise RecoveryError("restored archive member is missing", "restore_verification_failed")

    def resume(self) -> None:
        if self._workspace is not None:
            shutil.rmtree(self._workspace, ignore_errors=True)
        self._workspace = None

    def rollback(self) -> None:
        if self._swapped:
            self._remove(self.target)
            if self._previous is not None and (self._previous.exists() or self._previous.is_symlink()):
                self._previous.replace(self.target)
        if self._workspace is not None:
            shutil.rmtree(self._workspace, ignore_errors=True)
        self._workspace = None

    def __getattr__(self, name: str):
        if name == "import":
            return self.import_
        raise AttributeError(name)
