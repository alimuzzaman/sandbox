from pathlib import Path
from typing import Protocol, Sequence


def _safe_path(value: str | Path, label: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value) or any(
            ord(char) < 32 or ord(char) == 127 for char in str(value)):
        raise ValueError(f"{label} is invalid")
    try:
        return Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc


class PathPolicy(Protocol):
    def require_allowed(self, path: str | Path) -> str | Path: ...
    def artifact_path(self, root: str | Path, *parts: str | Path) -> Path: ...


class AllowedRootPathPolicy:
    def __init__(self, roots: Sequence[str | Path]) -> None:
        if isinstance(roots, (str, bytes)):
            raise ValueError("allowed roots must be a sequence")
        self.roots = tuple(_safe_path(root, "allowed root") for root in roots)
        if not self.roots:
            raise ValueError("at least one allowed root is required")
        if any(root == Path(root.anchor or "/") for root in self.roots):
            raise ValueError("allowed root is too broad")

    def require_allowed(self, path: str | Path) -> Path:
        resolved = _safe_path(path, "path")
        if not any(resolved == root or root in resolved.parents for root in self.roots):
            raise ValueError(f"path is outside allowed roots: {resolved}")
        return resolved

    def artifact_path(self, root: str | Path, *parts: str | Path) -> Path:
        """Build an artifact location only when both root and result are allowed."""
        allowed_root = self.require_allowed(root)
        if not parts or not all(isinstance(part, (str, Path)) for part in parts):
            raise ValueError("artifact path parts are invalid")
        return self.require_allowed(allowed_root.joinpath(*parts))
