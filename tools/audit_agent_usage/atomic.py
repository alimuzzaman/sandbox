"""Small atomic text-file primitive for audit-only derived outputs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from collections.abc import Mapping


def atomic_write_text(path: str | Path, text: str) -> Path:
    """Write *text* through a sibling temporary file and replace atomically."""

    atomic_write_texts({Path(path): text})
    return Path(path)


def atomic_write_texts(files: Mapping[str | Path, str]) -> tuple[Path, ...]:
    """Commit several text files together, rolling back on a failed replace."""

    payloads = {Path(path): text for path, text in files.items()}
    destinations = tuple(payloads)
    if not destinations:
        return ()
    parents = {path.parent for path in destinations}
    if len(parents) != 1:
        raise ValueError("atomic file group must share one parent directory")

    for parent in parents:
        parent.mkdir(parents=True, exist_ok=True)

    temporary: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    backup_paths: list[Path] = []
    unresolved_backups: set[Path] = set()
    replaced: list[Path] = []
    try:
        for destination in destinations:
            fd, raw_temporary = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=destination.parent,
            )
            temporary[destination] = Path(raw_temporary)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(payloads[destination])
                stream.flush()
                os.fsync(stream.fileno())

        for destination in destinations:
            if not (destination.exists() or destination.is_symlink()):
                continue
            fd, raw_backup = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".bak",
                dir=destination.parent,
            )
            os.close(fd)
            backup = Path(raw_backup)
            backup_paths.append(backup)
            os.replace(destination, backup)
            backups[destination] = backup

        for destination in destinations:
            os.replace(temporary[destination], destination)
            replaced.append(destination)

    except BaseException:
        for destination in reversed(replaced):
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        # Restore each backup independently.  A persistent restore failure must
        # not abort the loop or delete an unresolved backup in ``finally``.
        for destination, backup in backups.items():
            if backup.exists():
                try:
                    os.replace(backup, destination)
                except OSError:
                    unresolved_backups.add(backup)
        raise
    finally:
        for temporary_path in temporary.values():
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
        for backup in backup_paths:
            if backup in unresolved_backups:
                continue
            try:
                backup.unlink()
            except FileNotFoundError:
                pass

    return destinations


__all__ = ["atomic_write_text", "atomic_write_texts"]
