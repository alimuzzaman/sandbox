"""Git provenance and explicitly selected unpublished-state capture."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from .errors import RecoveryError


class GitRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str | None = None,
            env=None, timeout: float | None = None): ...


_SENSITIVE_NAMES = (".env", "id_rsa", "id_ed25519", ".pem", ".key", "credentials")


class GitCapture:
    def __init__(self, runner: GitRunner) -> None:
        self.runner = runner

    def _text(self, argv: Sequence[str], root: str | Path) -> str:
        result = self.runner.run(argv, cwd=str(root), timeout=30)
        if result.returncode != 0:
            raise RecoveryError("git provenance command failed", "git_command_failed")
        # Porcelain status deliberately uses leading spaces as a status column.
        return result.stdout.rstrip()

    def provenance(self, root: str | Path, remote: str = "origin") -> dict:
        root = Path(root).resolve()
        revision = self._text(("git", "rev-parse", "HEAD"), root)
        remote_url = self._text(("git", "remote", "get-url", remote), root)
        status = self._text(("git", "status", "--porcelain=v1", "--untracked-files=all"), root)
        dirty = tuple(line for line in status.splitlines() if line)
        ignored_sensitive = tuple(line for line in dirty if any(name in line.lower() for name in _SENSITIVE_NAMES))
        included_dirty = tuple(line for line in dirty if line not in ignored_sensitive)
        return {"root": str(root), "revision": revision, "remote": remote_url,
                "dirty": included_dirty, "ignored_sensitive": ignored_sensitive}

    def create_bundle(self, root: str | Path, destination: str | Path, revision: str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        result = self.runner.run(("git", "bundle", "create", str(target), revision), cwd=str(root), timeout=300)
        if result.returncode != 0:
            raise RecoveryError("git bundle creation failed", "git_bundle_failed")
        check = self.runner.run(("git", "bundle", "verify", str(target)), cwd=str(root), timeout=60)
        if check.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            raise RecoveryError("git bundle verification failed", "git_bundle_invalid")
        return target

    def create_patch(self, root: str | Path, destination: str | Path, paths: tuple[str, ...]) -> Path:
        if not paths or any(not path or Path(path).is_absolute() or ".." in Path(path).parts for path in paths):
            raise RecoveryError("Git patch paths are invalid", "invalid_git_patch")
        if any(any(name in path.lower() for name in _SENSITIVE_NAMES) for path in paths):
            raise RecoveryError("sensitive files cannot be added to a Git patch", "sensitive_git_patch")
        result = self.runner.run(("git", "diff", "--binary", "--", *paths), cwd=str(root), timeout=60)
        if result.returncode != 0 or not result.stdout:
            raise RecoveryError("Git patch generation failed", "git_patch_failed")
        target = Path(destination); target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.stdout)
        return target
