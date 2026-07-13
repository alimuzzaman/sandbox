from __future__ import annotations

import tarfile
from pathlib import Path

from .errors import RecoveryError


def archive_paths(root: str | Path, paths: tuple[str | Path, ...], destination: str | Path) -> Path:
    root = Path(root).resolve(); destination = Path(destination)
    members = []
    for raw in paths:
        path = Path(raw).resolve()
        try: path.relative_to(root)
        except ValueError as exc: raise RecoveryError("archive member escapes declared root", "invalid_source") from exc
        if not path.exists(): raise RecoveryError("archive member is absent", "missing_source")
        members.append(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w") as archive:
        for path in members: archive.add(path, arcname=str(path.relative_to(root)), recursive=True)
    return destination
