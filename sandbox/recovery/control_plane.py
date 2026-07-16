"""Safe control-plane declaration capture; credentials remain encrypted artifacts only."""
from __future__ import annotations

from pathlib import Path
import stat

from .errors import RecoveryError

_EXCLUDED = (".env", "session", "cookie", "token", "cache", "log", "job")


def capture_declarations(root: str | Path, declared: tuple[str, ...]) -> dict:
    try:
        root = Path(root).resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise RecoveryError("control-plane root is invalid", "invalid_control_plane_root") from exc
    if not root.is_dir():
        raise RecoveryError("control-plane root is unavailable", "invalid_control_plane_root")
    if not isinstance(declared, tuple):
        raise RecoveryError("control-plane declarations are invalid", "invalid_control_plane_path")
    artifacts, excluded = [], []
    seen = set()
    for relative in declared:
        if (not isinstance(relative, str) or not relative or
                any(ord(char) < 32 or ord(char) == 127 for char in relative)):
            raise RecoveryError("control-plane path is invalid", "invalid_control_plane_path")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or relative in seen:
            raise RecoveryError("control-plane path is invalid", "invalid_control_plane_path")
        seen.add(relative)
        if any(part.lower() in _EXCLUDED or part.lower().endswith(".env") for part in candidate.parts):
            excluded.append(relative); continue
        source = root / candidate
        try:
            source_metadata = source.lstat()
        except OSError as exc:
            raise RecoveryError("control-plane declaration is absent", "missing_control_plane_declaration") from exc
        if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(source_metadata.st_mode):
            raise RecoveryError("control-plane declaration must be a regular file", "invalid_control_plane_path")
        resolved = source.resolve()
        if root not in resolved.parents and resolved != root:
            raise RecoveryError("control-plane path escapes root", "invalid_control_plane_path")
        artifacts.append({"path": str(candidate), "bytes": resolved.stat().st_size})
    return {"artifacts": tuple(artifacts), "excluded": tuple(excluded),
            "cloudflare": tuple(item for item in artifacts if "cloudflare" in item["path"].lower())}
