from __future__ import annotations

import tarfile
import os
import shutil
import stat
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
from typing import Protocol

from .errors import RecoveryError
from .integrity import sha256_file


def _contains_control_text(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _member_name(root: Path, path: Path) -> str:
    try:
        name = str(path.relative_to(root))
    except ValueError as exc:
        raise RecoveryError("archive member escapes declared root", "invalid_source") from exc
    if not name or name == "." or name.startswith("../") or "/../" in name:
        raise RecoveryError("archive member path is unsafe", "invalid_source")
    return name


def _capture_snapshot(path: Path) -> tuple:
    records = []

    def visit(current: Path, relative: Path) -> None:
        metadata = current.lstat()
        mode = stat.S_IFMT(metadata.st_mode)
        record = [relative.as_posix(), mode, metadata.st_size, metadata.st_mtime_ns]
        if stat.S_ISLNK(mode):
            record.append(current.readlink().as_posix())
        elif stat.S_ISREG(mode):
            record.append(sha256_file(current))
        records.append(tuple(record))
        if stat.S_ISDIR(mode):
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                visit(child, relative / child.name)

    visit(path, Path("."))
    return tuple(records)


def _validate_filesystem_boundary(path: Path, root_device: int) -> None:
    metadata = path.lstat()
    if metadata.st_dev != root_device:
        raise RecoveryError("filesystem source crosses a mount boundary", "cross_filesystem")
    mode = stat.S_IFMT(metadata.st_mode)
    if stat.S_ISLNK(mode):
        if path.resolve().lstat().st_dev != root_device:
            raise RecoveryError("filesystem link crosses a mount boundary", "cross_filesystem")
    elif stat.S_ISDIR(mode):
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            _validate_filesystem_boundary(child, root_device)


def _validated_sources(root: Path, paths: tuple[str | Path, ...]) -> tuple[tuple[Path, str], ...]:
    root_device = root.stat().st_dev
    members = []
    for raw in paths:
        raw_path = Path(raw)
        source = raw_path if raw_path.is_absolute() else root / raw_path
        source = source.parent.resolve() / source.name
        member_name = _member_name(root, source)
        _member_name(root, source.resolve())
        if not source.exists():
            raise RecoveryError("archive member is absent", "missing_source")
        _validate_filesystem_boundary(source, root_device)
        members.append((source, member_name))
    if not members:
        raise RecoveryError("archive requires at least one source", "empty_source")
    return tuple(members)


def validate_archive(path: str | Path) -> tuple[str, ...]:
    """Reject traversal, ambiguous members, and special nodes before restore."""
    names = []
    seen = set()
    with tarfile.open(path, "r") as archive:
        for member in archive.getmembers():
            name = member.name
            raw_parts = name.split("/")
            if (not name or _contains_control_text(name) or name.startswith("/") or
                    ".." in raw_parts or "." in raw_parts):
                raise RecoveryError("archive contains unsafe member", "unsafe_archive")
            canonical_name = str(PurePosixPath(name))
            if canonical_name in seen:
                raise RecoveryError("archive contains duplicate member", "unsafe_archive")
            if member.isdev() or member.isfifo():
                raise RecoveryError("archive contains a special file", "unsafe_archive")
            if member.issym() or member.islnk():
                if _contains_control_text(member.linkname):
                    raise RecoveryError("archive link contains unsafe text", "unsafe_archive")
                link_parts = PurePosixPath(member.linkname).parts
                target = PurePosixPath(*PurePosixPath(member.name).parent.parts, *link_parts)
                if PurePosixPath(member.linkname).is_absolute() or ".." in link_parts or ".." in target.parts:
                    raise RecoveryError("archive link escapes staging root", "unsafe_archive")
            names.append(canonical_name)
            seen.add(canonical_name)
    return tuple(names)


def archive_paths(root: str | Path, paths: tuple[str | Path, ...], destination: str | Path) -> Path:
    root = Path(root).resolve(); destination = Path(destination)
    if destination.is_symlink() or (destination.exists() and not stat.S_ISREG(destination.lstat().st_mode)):
        raise RecoveryError("archive destination is not a regular file", "invalid_destination")
    members = _validated_sources(root, paths)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w") as archive:
        for source, member_name in members:
            before = _capture_snapshot(source)
            archive.add(source, arcname=member_name, recursive=True)
            after = _capture_snapshot(source)
            if before != after:
                raise RecoveryError("filesystem source changed during archive", "source_changed")
    validate_archive(destination)
    return destination


class FilesystemCapture:
    """Allowlisted archive adapter with an explicit metadata-preservation warning.

    The portable Python tar implementation preserves modes, mtimes, numeric uid
    and gid.  ACLs/xattrs need the GNU-tar server adapter before a production
    profile can claim them, so callers receive a non-suppressible warning.
    """
    def capture(self, root: str | Path, paths: tuple[str | Path, ...], destination: str | Path) -> dict:
        archive = archive_paths(root, paths, destination)
        return {"path": archive, "members": validate_archive(archive),
                "warnings": ("ACL/xattr preservation requires the GNU-tar server adapter",)}


class TarRunner(Protocol):
    def run(self, argv, *, cwd: str | None = None, timeout: float | None = None): ...


class GnuTarFilesystemCapture:
    """Injectable GNU-tar adapter for ACL/xattr-preserving filesystem capture."""

    def __init__(self, runner: TarRunner, *, executable: str = "tar") -> None:
        if not isinstance(executable, str) or not executable or any(ord(char) < 32 for char in executable):
            raise RecoveryError("tar executable is invalid", "invalid_tar_executable")
        self.runner, self.executable = runner, executable

    def capture(self, root: str | Path, paths: tuple[str | Path, ...], destination: str | Path,
                *, excludes: tuple[str, ...] = (), timeout: float = 3600) -> dict:
        root = Path(root).resolve()
        destination = Path(destination)
        if destination.is_symlink() or (destination.exists() and not stat.S_ISREG(destination.lstat().st_mode)):
            raise RecoveryError("archive destination is not a regular file", "invalid_destination")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
            raise RecoveryError("tar capture timeout is invalid", "invalid_tar_timeout")
        for pattern in excludes:
            if (not isinstance(pattern, str) or not pattern or pattern.startswith("-") or
                    Path(pattern).is_absolute() or ".." in Path(pattern).parts or _contains_control_text(pattern)):
                raise RecoveryError("tar exclusion is invalid", "invalid_tar_exclude")
        members = _validated_sources(root, paths)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_directory = Path(tempfile.mkdtemp(prefix="sandbox-gnu-tar-", dir=destination.parent))
        os.chmod(temporary_directory, 0o700)
        temporary = temporary_directory / "archive.tar"
        argv = [self.executable, "--create", "--file", str(temporary), "--acls", "--xattrs",
                "--numeric-owner", "--one-file-system"]
        argv.extend(f"--exclude={pattern}" for pattern in excludes)
        argv.extend(("--", *(name for _source, name in members)))
        before = tuple((name, _capture_snapshot(source)) for source, name in members)
        try:
            result = self.runner.run(tuple(argv), cwd=str(root), timeout=timeout)
            if result.returncode != 0:
                raise RecoveryError("GNU tar capture failed", "tar_capture_failed")
            after = tuple((name, _capture_snapshot(source)) for source, name in members)
            if after != before:
                raise RecoveryError("filesystem source changed during archive", "source_changed")
            if not temporary.is_file() or not temporary.stat().st_size:
                raise RecoveryError("GNU tar archive is empty", "tar_capture_failed")
            archive_members = validate_archive(temporary)
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
            return {"path": destination, "members": archive_members, "warnings": (), "argv": tuple(argv)}
        finally:
            shutil.rmtree(temporary_directory, ignore_errors=True)
