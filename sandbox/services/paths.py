from pathlib import Path
from typing import Protocol, Sequence

class PathPolicy(Protocol):
    def require_allowed(self, path: str | Path) -> str | Path: ...
    def artifact_path(self, root: str | Path, *parts: str | Path) -> Path: ...


class AllowedRootPathPolicy:
    def __init__(self, roots: Sequence[str | Path]) -> None:
        self.roots = tuple(Path(root).expanduser().resolve() for root in roots)
        if not self.roots:
            raise ValueError("at least one allowed root is required")

    def require_allowed(self, path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not any(resolved == root or root in resolved.parents for root in self.roots):
            raise ValueError(f"path is outside allowed roots: {resolved}")
        return resolved

    def artifact_path(self, root: str | Path, *parts: str | Path) -> Path:
        """Build an artifact location only when both root and result are allowed."""
        allowed_root = self.require_allowed(root)
        return self.require_allowed(allowed_root.joinpath(*map(str, parts)))
