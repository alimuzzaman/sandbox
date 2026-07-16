from __future__ import annotations

import tarfile
from pathlib import Path
from pathlib import PurePosixPath

from .errors import RecoveryError


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
    members = []
    for raw in paths:
        raw_path = Path(raw)
        source = raw_path if raw_path.is_absolute() else root / raw_path
        # Canonicalize parent-directory aliases (including macOS /var -> /private)
        # without resolving the final entry, so a final symlink remains archivable.
        source = source.parent.resolve() / source.name
        member_name = _member_name(root, source)
        # Preserve the declared link while validating its resolved target remains inside root.
        _member_name(root, source.resolve())
        if not source.exists(): raise RecoveryError("archive member is absent", "missing_source")
        members.append((source, member_name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w") as archive:
        for source, member_name in members:
            before_stat = source.lstat()
            before = (before_stat.st_mtime_ns, before_stat.st_size)
            archive.add(source, arcname=member_name, recursive=True)
            after_stat = source.lstat()
            after = (after_stat.st_mtime_ns, after_stat.st_size)
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
