from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def validate_init_destination(project_root: Path, destination: Path):
    """Return the contained parent, mode, and current file identity."""
    root = project_root.resolve()
    try:
        parent = destination.parent.resolve()
        parent.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "Sandbox init descriptor destination must stay within the project root"
        ) from exc
    if destination.exists() or destination.is_symlink():
        metadata = destination.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                "Sandbox init descriptor destination must be a regular non-symlink file"
            )
        return parent, stat.S_IMODE(metadata.st_mode), (
            metadata.st_dev, metadata.st_ino
        )
    return parent, 0o644, None


def atomic_write_init_file(project_root: Path, destination: Path,
                           payload: str) -> None:
    """Publish one init file atomically without following a swapped target."""
    parent, mode, initial_identity = validate_init_destination(
        project_root, destination
    )
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=parent
    )
    temporary_path = Path(temporary)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Sandbox init temporary descriptor is not a regular file")
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        current_identity = None
        if destination.exists() or destination.is_symlink():
            current = destination.lstat()
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
                raise ValueError(
                    "Sandbox init descriptor destination changed during publication"
                )
            current_identity = (current.st_dev, current.st_ino)
        if current_identity != initial_identity:
            raise ValueError(
                "Sandbox init descriptor destination changed during publication"
            )
        os.replace(temporary_path, parent / destination.name)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
