"""Safe control-plane declaration capture; credentials remain encrypted artifacts only."""
from __future__ import annotations

from pathlib import Path

from .errors import RecoveryError

_EXCLUDED = (".env", "session", "cookie", "token", "cache", "log", "job")


def capture_declarations(root: str | Path, declared: tuple[str, ...]) -> dict:
    root = Path(root).resolve()
    artifacts, excluded = [], []
    for relative in declared:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RecoveryError("control-plane path is invalid", "invalid_control_plane_path")
        if any(part.lower() in _EXCLUDED or part.lower().endswith(".env") for part in candidate.parts):
            excluded.append(relative); continue
        resolved = (root / candidate).resolve()
        if root not in resolved.parents and resolved != root:
            raise RecoveryError("control-plane path escapes root", "invalid_control_plane_path")
        if not resolved.exists():
            raise RecoveryError("control-plane declaration is absent", "missing_control_plane_declaration")
        artifacts.append({"path": str(candidate), "bytes": resolved.stat().st_size})
    return {"artifacts": tuple(artifacts), "excluded": tuple(excluded),
            "cloudflare": tuple(item for item in artifacts if "cloudflare" in item["path"].lower())}
