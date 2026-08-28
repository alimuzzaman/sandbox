"""Stable, bounded, Git-relative source manifest capture."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from typing import Callable, Iterable

from .policy import SyncPolicy, validate_relative_path


DEFAULT_MAX_FILES = 100
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_RETRIES = 2


class CaptureError(RuntimeError):
    code = "capture_failed"


class ManifestLimitExceeded(CaptureError):
    code = "manifest_limit_exceeded"


class UnstableCapture(CaptureError):
    code = "unstable_capture"


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str
    executable: bool

    def canonical(self) -> dict[str, object]:
        return {
            "path": self.path, "size": self.size,
            "sha256": self.sha256, "executable": self.executable,
        }


@dataclass(frozen=True)
class CaptureManifest:
    git_root: Path
    entries: tuple[ManifestEntry, ...]
    manifest_digest: str
    generation_id: str
    commit: str | None
    dirty_digest: str | None
    file_count: int
    byte_count: int
    excluded_count: int

    def canonical_entries(self) -> list[dict[str, object]]:
        return [entry.canonical() for entry in self.entries]

    def public_counts(self) -> dict[str, int]:
        return {"file_count": self.file_count, "byte_count": self.byte_count}


@dataclass(frozen=True)
class _View:
    entry: ManifestEntry
    signature: tuple[int, int, int, int, int]


def _git(root: Path, *arguments: str, allow_failure: bool = False) -> bytes:
    result = subprocess.run(
        ["git", *arguments], cwd=str(root), capture_output=True, check=False,
    )
    if result.returncode != 0 and not allow_failure:
        raise CaptureError("Git source inventory is unavailable")
    return result.stdout or b""


def _git_root(project_root: Path) -> Path:
    output = _git(project_root, "rev-parse", "--show-toplevel")
    try:
        root = Path(os.fsdecode(output).strip()).resolve(strict=True)
        project_root.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CaptureError("project root is not inside a supported Git checkout") from exc
    return root


def _pathspec(project_root: Path, git_root: Path) -> str:
    relative = project_root.relative_to(git_root).as_posix()
    return "." if relative == "." else relative


def _nul_paths(output: bytes) -> set[str]:
    paths: set[str] = set()
    for raw in output.split(b"\0"):
        if not raw:
            continue
        try:
            value = os.fsdecode(raw)
            paths.add(validate_relative_path(value))
        except (UnicodeError, ValueError) as exc:
            raise CaptureError("Git returned an unsafe source path") from exc
    return paths


def _expand_explicit(project_root: Path, git_root: Path, values: Iterable[str]) -> set[str]:
    selected: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("explicit includes must be relative paths")
        validate_relative_path(raw)
        requested = project_root / raw
        try:
            requested.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("explicit include escapes the project root") from exc
        if not requested.exists() and not requested.is_symlink():
            raise ValueError("explicit include does not exist")
        candidates = [requested]
        if requested.is_dir() and not requested.is_symlink():
            candidates = [item for item in requested.rglob("*") if item.is_file() or item.is_symlink()]
        for item in candidates:
            selected.add(validate_relative_path(item.relative_to(git_root).as_posix()))
    return selected


def _candidate_paths(
    project_root: Path, git_root: Path, explicit_includes: tuple[str, ...],
) -> tuple[set[str], set[str]]:
    scope = _pathspec(project_root, git_root)
    tracked = _nul_paths(_git(git_root, "ls-files", "-z", "--cached", "--", scope))
    modified = _nul_paths(_git(git_root, "ls-files", "-z", "--modified", "--", scope))
    untracked = _nul_paths(_git(
        git_root, "ls-files", "-z", "--others", "--exclude-standard", "--", scope,
    ))
    explicit = _expand_explicit(project_root, git_root, explicit_includes)
    return tracked | modified | untracked | explicit, explicit


def _read_view(path: Path, relative: str) -> _View | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        return None
    try:
        content = path.read_bytes()
        after = path.lstat()
    except (FileNotFoundError, OSError) as exc:
        raise UnstableCapture("source changed during capture") from exc
    before_signature = (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode,
    )
    after_signature = (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode,
    )
    if before_signature != after_signature or len(content) != after.st_size:
        raise UnstableCapture("source changed during capture")
    return _View(
        ManifestEntry(
            path=relative, size=len(content), sha256=hashlib.sha256(content).hexdigest(),
            executable=bool(after.st_mode & stat.S_IXUSR),
        ),
        after_signature,
    )


def _second_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return None
    return (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_mode)


def _status_digest(git_root: Path, scope: str) -> str | None:
    status = _git(
        git_root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", scope,
    )
    return hashlib.sha256(status).hexdigest() if status else None


def capture_manifest(
    project_root: str | Path,
    *,
    explicit_includes: Iterable[str] = (),
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    retries: int = DEFAULT_RETRIES,
    policy: SyncPolicy | None = None,
    after_first_view: Callable[[int, tuple[ManifestEntry, ...]], None] | None = None,
) -> CaptureManifest:
    """Capture one coherent manifest or fail without returning mixed content.

    ``after_first_view`` is a deterministic test/coordination seam.  It receives
    metadata only, never source contents.
    """
    if (isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 1 or
            isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1 or
            isinstance(retries, bool) or not isinstance(retries, int) or retries < 0 or retries > 10):
        raise ValueError("capture bounds are invalid")
    root = Path(project_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise CaptureError("project root must be a directory")
    git_root = _git_root(root)
    explicit = tuple(explicit_includes)
    selected_policy = policy or SyncPolicy()

    for attempt in range(retries + 1):
        try:
            candidates, _explicit_paths = _candidate_paths(root, git_root, explicit)
            views: list[_View] = []
            excluded = 0
            total_bytes = 0
            for relative in sorted(candidates, key=lambda item: item.encode("utf-8")):
                path = git_root / relative
                is_symlink = path.is_symlink()
                # Sensitive names are generation-fatal even when the path would
                # otherwise be an ordinary exclusion.
                selected_policy.screen(relative)
                if selected_policy.ordinary_exclusion(relative, is_symlink=is_symlink):
                    excluded += 1
                    continue
                view = _read_view(path, relative)
                if view is None:
                    excluded += 1
                    continue
                # Read once more only for screening. The first view's digest and
                # the second metadata check ensure these bytes cannot become a
                # falsely current mixed manifest.
                content = path.read_bytes()
                selected_policy.screen(relative, content)
                if hashlib.sha256(content).hexdigest() != view.entry.sha256:
                    raise UnstableCapture("source changed during capture")
                views.append(view)
                total_bytes += view.entry.size
                if len(views) > max_files or total_bytes > max_bytes:
                    raise ManifestLimitExceeded("source generation exceeds the configured bound")

            entries = tuple(view.entry for view in views)
            if after_first_view is not None:
                after_first_view(attempt, entries)
            second_candidates, _ = _candidate_paths(root, git_root, explicit)
            if candidates != second_candidates or any(
                _second_signature(git_root / view.entry.path) != view.signature for view in views
            ):
                raise UnstableCapture("source changed during capture")

            canonical = json.dumps(
                [entry.canonical() for entry in entries],
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
            manifest_digest = hashlib.sha256(canonical).hexdigest()
            commit_raw = _git(git_root, "rev-parse", "HEAD", allow_failure=True).decode().strip()
            commit = commit_raw if re_full_sha(commit_raw) else None
            dirty_digest = _status_digest(git_root, _pathspec(root, git_root))
            # Generation identity is a content identity. Commit and dirty
            # state remain evidence, but do not make identical bytes produce
            # duplicate generations.
            identity = hashlib.sha256(manifest_digest.encode()).hexdigest()
            return CaptureManifest(
                git_root=git_root, entries=entries, manifest_digest=manifest_digest,
                generation_id=f"gen_{identity}", commit=commit, dirty_digest=dirty_digest,
                file_count=len(entries), byte_count=total_bytes, excluded_count=excluded,
            )
        except UnstableCapture:
            if attempt >= retries:
                raise
    raise UnstableCapture("source changed during capture")


def re_full_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


__all__ = [
    "CaptureError", "CaptureManifest", "DEFAULT_MAX_BYTES", "DEFAULT_MAX_FILES",
    "ManifestEntry", "ManifestLimitExceeded", "UnstableCapture", "capture_manifest",
]
