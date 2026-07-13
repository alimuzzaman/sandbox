from __future__ import annotations

import tarfile
from pathlib import Path

from .errors import RecoveryError


def _member_name(root: Path, path: Path) -> str:
    try:
        name = str(path.relative_to(root))
    except ValueError as exc:
        raise RecoveryError("archive member escapes declared root", "invalid_source") from exc
    if not name or name == "." or name.startswith("../") or "/../" in name:
        raise RecoveryError("archive member path is unsafe", "invalid_source")
    return name


def validate_archive(path: str | Path) -> tuple[str, ...]:
    """Reject traversal and escaping link targets before a restore can use it."""
    names = []
    with tarfile.open(path, "r") as archive:
        for member in archive.getmembers():
            name = member.name
            if name.startswith("/") or name == ".." or name.startswith("../") or "/../" in name:
                raise RecoveryError("archive contains unsafe member", "unsafe_archive")
            if member.issym() or member.islnk():
                target = Path(member.name).parent / member.linkname
                if target.is_absolute() or ".." in target.parts:
                    raise RecoveryError("archive link escapes staging root", "unsafe_archive")
            names.append(name)
    return tuple(names)


def archive_paths(root: str | Path, paths: tuple[str | Path, ...], destination: str | Path) -> Path:
    root = Path(root).resolve(); destination = Path(destination)
    members = []
    for raw in paths:
        raw_path = Path(raw)
        # A symlink itself is permitted only if its resolved target remains inside root.
        path = raw_path.resolve()
        _member_name(root, path)
        if not path.exists(): raise RecoveryError("archive member is absent", "missing_source")
        members.append(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w") as archive:
        for path in members:
            before = (path.stat().st_mtime_ns, path.stat().st_size)
            archive.add(path, arcname=_member_name(root, path), recursive=True)
            after = (path.stat().st_mtime_ns, path.stat().st_size)
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
