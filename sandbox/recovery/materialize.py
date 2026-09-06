"""Controller-owned capture contract for symbolic recovery profiles.

Adapters resolve declarations and validate native capture formats. The coordinator
owns the private output directory and requires complete per-profile coverage and a
stable source binding before handing files to publication.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Protocol

from .errors import RecoveryError
from .models import ArtifactPlan, RecoveryPlan


@dataclass(frozen=True)
class SourceBinding:
    remote: str
    machine_identity: str
    revision: str
    source_digest: str

    def validate(self, remote: str) -> None:
        if self.remote != remote or not all(
            isinstance(value, str) and value and
            not any(ord(char) < 32 or ord(char) == 127 for char in value)
            for value in (self.remote, self.machine_identity, self.revision, self.source_digest)
        ):
            raise RecoveryError("materialization source binding is invalid", "invalid_source_binding")


class MaterializationAdapter(Protocol):
    def observe(self, remote: str, plan: RecoveryPlan) -> SourceBinding: ...

    def capture(self, remote: str, artifact: ArtifactPlan, destination: Path,
                binding: SourceBinding) -> tuple[Path, ...]:
        """Return native-format-validated files covering every declared source."""
        ...


class ScopedMaterializer:
    def __init__(self, root: str | Path, adapter: MaterializationAdapter) -> None:
        self.root = Path(root)
        self.adapter = adapter

    def publish(self, remote: str, plan: RecoveryPlan, capture, set_id: str,
                profile_bindings: dict) -> dict:
        # Observe validates adapter capabilities and all declarations before capture.
        binding = self.adapter.observe(remote, plan)
        if not isinstance(binding, SourceBinding):
            raise RecoveryError("materialization source binding is invalid", "invalid_source_binding")
        binding.validate(remote)
        if self.root.is_symlink():
            raise RecoveryError("materialization root is invalid", "invalid_artifact")
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise RecoveryError("materialization root is unavailable", "invalid_artifact") from exc
        try:
            root_info = self.root.lstat()
        except OSError as exc:
            raise RecoveryError("materialization root is unavailable", "invalid_artifact") from exc
        if (not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.geteuid()):
            raise RecoveryError("materialization root is not owner-controlled", "invalid_artifact")
        # The root can predate this coordinator. Tighten its mode before any
        # controller output is created so a stale 0755 directory cannot expose
        # plaintext capture artifacts.
        if stat.S_IMODE(root_info.st_mode) & 0o077:
            os.chmod(self.root, 0o700)
        stage = Path(tempfile.mkdtemp(prefix="capture-", dir=self.root))
        os.chmod(stage, 0o700)
        try:
            files = {}
            for artifact in plan.artifacts:
                destination = stage / artifact.artifact_id
                destination.mkdir(mode=0o700)
                paths = self.adapter.capture(remote, artifact, destination, binding)
                if not isinstance(paths, tuple) or not paths:
                    raise RecoveryError("materialization profile has no artifacts", "incomplete_materialization")
                try:
                    destination_info = destination.lstat()
                except OSError as exc:
                    raise RecoveryError("materialization profile directory is unavailable", "invalid_artifact") from exc
                if (not stat.S_ISDIR(destination_info.st_mode) or
                        destination_info.st_uid != os.geteuid() or destination.is_symlink()):
                    raise RecoveryError("materialization profile directory is invalid", "invalid_artifact")
                for path in paths:
                    if not isinstance(path, Path) or path.is_symlink():
                        raise RecoveryError("materialization artifact is invalid", "invalid_artifact")
                    try:
                        relative = path.resolve().relative_to(destination.resolve())
                    except ValueError as exc:
                        raise RecoveryError("materialization artifact escapes owned output", "invalid_artifact") from exc
                    # A symlinked parent can resolve back inside the stage and
                    # otherwise evade the leaf check. Every path component
                    # between the artifact and its private profile directory
                    # must be a real directory/file owned by this capture.
                    current = path
                    while current != destination:
                        if current.is_symlink():
                            raise RecoveryError("materialization artifact is invalid", "invalid_artifact")
                        current = current.parent
                    if destination.is_symlink():
                        raise RecoveryError("materialization profile directory is invalid", "invalid_artifact")
                    try:
                        info = path.lstat()
                    except OSError as exc:
                        raise RecoveryError("materialization artifact is unavailable", "invalid_artifact") from exc
                    if (not stat.S_ISREG(info.st_mode) or info.st_size == 0 or
                            info.st_nlink != 1 or info.st_uid != os.geteuid()):
                        raise RecoveryError("materialization artifact is unavailable", "invalid_artifact")
                    name = f"{artifact.profile_id}/{relative.as_posix()}"
                    if name in files:
                        raise RecoveryError("materialization artifact is duplicated", "invalid_artifact")
                    files[name] = path
            if self.adapter.observe(remote, plan) != binding:
                raise RecoveryError("materialization source binding changed", "source_changed")
            return capture.publish_files(
                set_id, files, profiles=plan.profiles, profile_bindings=profile_bindings,
                provenance={"remote": binding.remote, "machine_identity": binding.machine_identity,
                            "revision": binding.revision, "source_digest": binding.source_digest},
            )
        finally:
            shutil.rmtree(stage)
