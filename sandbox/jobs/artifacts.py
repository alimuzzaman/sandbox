"""Contained, bounded collection of job artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


MAX_ARTIFACTS = 50
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


class ArtifactError(RuntimeError):
    pass


def collect(storage, repository, job_id: str, *, project_root: str | Path,
            declared_paths: tuple[str, ...]) -> list[dict]:
    root = Path(project_root).resolve()
    destination = storage.job_dir(job_id) / "artifacts"
    destination.mkdir(mode=0o700, exist_ok=True)
    results = []
    for index, declared in enumerate(declared_paths):
        if index >= MAX_ARTIFACTS: raise ArtifactError("artifact count limit exceeded")
        source = (root / declared).resolve()
        if root not in source.parents or not source.is_file() or source.is_symlink():
            raise ArtifactError(f"artifact is outside project or not a regular file: {declared}")
        size = source.stat().st_size
        if size > MAX_ARTIFACT_BYTES: raise ArtifactError(f"artifact exceeds size limit: {declared}")
        artifact_id = hashlib.sha256(f"{job_id}:{declared}".encode()).hexdigest()[:24]
        stored = destination / artifact_id
        shutil.copyfile(source, stored)
        os.chmod(stored, 0o600)
        digest = hashlib.sha256(stored.read_bytes()).hexdigest()
        repository.add_artifact(job_id, artifact_id=artifact_id, display_name=source.name,
            stored_relative_path=str(Path("artifacts") / artifact_id), declared_path=declared,
            size_bytes=size, sha256=digest)
        results.append({"artifact_id": artifact_id, "display_name": source.name, "size_bytes": size, "sha256": digest})
    return results
