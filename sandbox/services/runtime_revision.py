from __future__ import annotations

import hashlib
from pathlib import Path


def runtime_revision_sources(root: Path) -> tuple[Path, ...]:
    """Return the canonical staged CLI/control source surface."""
    root = Path(root)
    parts = [root / "VERSION", root / "sb"]
    for source_root in (root / "sandbox", root / "mcp" / "wp-server"):
        parts.extend(source_root.rglob("*.py"))
    return tuple(sorted(
        (
            source for source in parts
            if source.is_file()
            and ".venv" not in source.parts
            and "__pycache__" not in source.parts
            and not source.name.startswith("._")
        ),
        key=lambda source: source.relative_to(root).as_posix(),
    ))


def runtime_revision(root: Path) -> str:
    """Hash the actual staged CLI/control source using one canonical algorithm."""
    root = Path(root)
    digest = hashlib.sha256()
    for source in runtime_revision_sources(root):
        relative = source.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(source.read_bytes())
    return digest.hexdigest()[:24]
