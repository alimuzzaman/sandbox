"""Contained, bounded collection of job artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from pathlib import Path


MAX_ARTIFACTS = 50
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000


class ArtifactError(RuntimeError):
    pass


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _copy_exact(input_stream, output_stream, expected_size: int) -> int:
    remaining = expected_size
    copied = 0
    while remaining:
        chunk = input_stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ArtifactError("artifact shrank during collection")
        output_stream.write(chunk)
        copied += len(chunk)
        remaining -= len(chunk)
    if input_stream.read(1):
        raise ArtifactError("artifact grew during collection")
    return copied


def _same_file_observation(before: os.stat_result, after: os.stat_result) -> bool:
    return all(getattr(before, field) == getattr(after, field) for field in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _literal_paths(declared_paths: tuple[str, ...]) -> tuple[str, ...]:
    values = []
    for raw in declared_paths:
        if not isinstance(raw, str):
            raise ArtifactError("artifact path must be a literal relative string")
        for value in raw.splitlines() or [raw]:
            value = value.strip()
            if value:
                values.append(value)
    if len(values) > MAX_ARTIFACTS:
        raise ArtifactError("artifact count limit exceeded")
    return tuple(values)


def _contained_source(root: Path, declared: str) -> Path:
    relative = Path(declared)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ArtifactError(f"artifact path escapes project: {declared}")
    current = root
    try:
        for part in relative.parts:
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ArtifactError(f"artifact symlink is not allowed: {declared}")
    except FileNotFoundError as exc:
        raise ArtifactError(f"artifact does not exist: {declared}") from exc
    source = current.resolve(strict=True)
    if source != root and root not in source.parents:
        raise ArtifactError(f"artifact path escapes project: {declared}")
    return source


def _open_regular(path: Path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ArtifactError(f"artifact is not a regular file: {path.name}")
    return fd, info


def _archive_entries(source: Path, *, excluded: Path | None = None) -> list[tuple[Path, str, os.stat_result]]:
    top = source.name or "artifact"
    entries: list[tuple[Path, str, os.stat_result]] = []

    def visit(path: Path, archive_name: str) -> None:
        if excluded is not None and (path == excluded or excluded in path.parents):
            return
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ArtifactError(f"artifact symlink is not allowed: {path.relative_to(source)}")
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ArtifactError(
                f"artifact directory entries must be a regular file or directory: {path.relative_to(source)}")
        entries.append((path, archive_name, info))
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise ArtifactError("artifact archive entry count limit exceeded")
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                visit(child, f"{archive_name}/{child.name}")

    visit(source, top)
    return entries


def _write_archive(storage, source: Path, stored: Path, *, excluded: Path | None = None,
                   max_source_bytes: int = MAX_ARTIFACT_BYTES) -> None:
    entries = _archive_entries(source, excluded=excluded)
    planned_bytes = sum(info.st_size for _path, _name, info in entries if stat.S_ISREG(info.st_mode))
    if planned_bytes > max_source_bytes:
        raise ArtifactError(f"artifact exceeds size limit: {source.name}")
    storage.require_capacity(planned_bytes + len(entries) * 1_024 + 1_024)
    live_bytes = 0
    try:
        with tarfile.open(stored, "w", format=tarfile.GNU_FORMAT) as archive:
            for path, name, observed in entries:
                info = tarfile.TarInfo(name)
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if stat.S_ISDIR(observed.st_mode):
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.size = 0
                    archive.addfile(info)
                    continue
                fd, current = _open_regular(path)
                try:
                    if (current.st_dev != observed.st_dev or current.st_ino != observed.st_ino or
                            current.st_size != observed.st_size):
                        raise ArtifactError(f"artifact changed during collection: {path.name}")
                    live_bytes += current.st_size
                    if live_bytes > max_source_bytes:
                        raise ArtifactError(f"artifact exceeds size limit: {source.name}")
                    info.mode = 0o644
                    info.size = current.st_size
                    with os.fdopen(fd, "rb", closefd=False) as handle:
                        try:
                            archive.addfile(info, handle)
                        except (OSError, EOFError) as exc:
                            raise ArtifactError(f"artifact changed during collection: {path.name}") from exc
                        after = os.fstat(fd)
                    if not _same_file_observation(current, after):
                        raise ArtifactError(f"artifact changed during collection: {path.name}")
                finally:
                    os.close(fd)
    except BaseException:
        stored.unlink(missing_ok=True)
        raise
    os.chmod(stored, 0o600)


def collect(storage, repository, job_id: str, *, project_root: str | Path,
            declared_paths: tuple[str, ...]) -> list[dict]:
    root = Path(project_root).resolve()
    destination = storage.job_dir(job_id) / "artifacts"
    destination.mkdir(mode=0o700, exist_ok=True)
    results = []
    total_size = 0
    for declared in _literal_paths(declared_paths):
        source = _contained_source(root, declared)
        info = source.lstat()
        artifact_id = hashlib.sha256(f"{job_id}:{declared}".encode()).hexdigest()[:24]
        stored = destination / artifact_id
        kind = "file"
        display_name = source.name
        if stat.S_ISREG(info.st_mode):
            fd, current = _open_regular(source)
            try:
                if not _same_file_observation(info, current):
                    raise ArtifactError(f"artifact changed during collection: {declared}")
                size = current.st_size
                if size > MAX_ARTIFACT_BYTES:
                    raise ArtifactError(f"artifact exceeds size limit: {declared}")
                if total_size + size > MAX_ARTIFACT_TOTAL_BYTES:
                    raise ArtifactError("artifact total size limit exceeded")
                storage.require_capacity(size)
                with os.fdopen(fd, "rb", closefd=False) as input_stream, stored.open("xb") as output_stream:
                    copied = _copy_exact(input_stream, output_stream, size)
                    after = os.fstat(fd)
                    if copied != size or not _same_file_observation(current, after):
                        raise ArtifactError(f"artifact changed during collection: {declared}")
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                os.chmod(stored, 0o600)
            except BaseException:
                stored.unlink(missing_ok=True)
                raise
            finally:
                if fd >= 0:
                    os.close(fd)
        elif stat.S_ISDIR(info.st_mode):
            kind = "archive"
            display_name = f"{source.name or 'artifact'}.tar"
            _write_archive(storage, source, stored, excluded=destination.resolve(),
                           max_source_bytes=min(MAX_ARTIFACT_BYTES,
                                                MAX_ARTIFACT_TOTAL_BYTES - total_size))
            size = stored.stat().st_size
            if size > MAX_ARTIFACT_BYTES:
                stored.unlink(missing_ok=True)
                raise ArtifactError(f"artifact exceeds size limit: {declared}")
        else:
            raise ArtifactError(f"artifact must be a regular file or directory: {declared}")
        total_size += size
        if total_size > MAX_ARTIFACT_TOTAL_BYTES:
            stored.unlink(missing_ok=True)
            raise ArtifactError("artifact total size limit exceeded")
        digest = _digest(stored)
        media_type = "application/x-tar" if kind == "archive" else "application/octet-stream"
        repository.add_artifact(
            job_id, artifact_id=artifact_id, display_name=display_name,
            stored_relative_path=str(Path("artifacts") / artifact_id), declared_path=declared,
            size_bytes=size, sha256=digest, kind=kind, media_type=media_type,
        )
        results.append({
            "artifact_id": artifact_id, "display_name": display_name, "kind": kind,
            "size_bytes": size, "sha256": digest, "media_type": media_type,
        })
    return results
