"""Build an isolated, local-only target for a future archive review run.

The builder is intentionally lifecycle-free.  It prepares a fresh project
descriptor and owner-only directories, but it does not call ``sb``, Docker,
the registry, or a secret/config loader.  The later archive command can hand
the returned contract to its journaled runtime adapter without accidentally
inheriting the machine's normal Sandbox state.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .archive import ArchivePreflight


_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION_RE = re.compile(r"^[0-9][A-Za-z0-9.+_-]*$")


class ArchiveTargetError(ValueError):
    """A target/config boundary violation before runtime side effects."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class PluginCheckPin:
    """Immutable Plugin Check source identity required by archive mode."""

    source: str
    version: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not self.source.startswith("https://") or not self.source.endswith(".zip"):
            raise ArchiveTargetError("archive_provenance_missing", "Plugin Check source must be a pinned HTTPS ZIP URL")
        if not _VERSION_RE.fullmatch(self.version):
            raise ArchiveTargetError("archive_provenance_missing", "Plugin Check version is not a stable release identifier")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ArchiveTargetError("archive_provenance_missing", "Plugin Check source must include a SHA-256 digest")
        # Keep the in-memory identity canonical as well as the serialized one;
        # a caller must not be able to make the same release look different
        # merely by changing hex-letter casing.
        object.__setattr__(self, "sha256", self.sha256.lower())

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "version": self.version, "sha256": self.sha256.lower()}


@dataclass(frozen=True)
class ArchiveReviewTarget:
    """The complete run-local target identity consumed by later lifecycle code."""

    caller_project_root: Path
    archive_path: Path
    archive_sha256: str
    extraction_root: Path
    archive_slug: str
    main_file: str
    baseline_path: Path
    artifact_dir: Path
    review_project_root: Path
    review_instance: str
    sandbox_home: Path
    project_roots: tuple[Path, ...]
    descriptor_path: Path
    descriptor: dict
    environment: dict[str, str]

    @property
    def plugin_path(self) -> Path:
        return self.extraction_root / self.archive_slug

    @property
    def member_manifest_sha256(self) -> str:
        return str(self.descriptor["archiveReview"]["memberManifestSha256"])

    def contract_dict(self) -> dict[str, object]:
        """Return non-secret paths/identity for a journal or test assertion."""

        return {
            "caller_project_root": str(self.caller_project_root),
            "archive_path": str(self.archive_path),
            "archive_sha256": self.archive_sha256,
            "extraction_root": str(self.extraction_root),
            "archive_slug": self.archive_slug,
            "main_file": self.main_file,
            "baseline_path": str(self.baseline_path),
            "artifact_dir": str(self.artifact_dir),
            "review_project_root": str(self.review_project_root),
            "review_instance": self.review_instance,
            "sandbox_home": str(self.sandbox_home),
            "project_roots": [str(path) for path in self.project_roots],
            "descriptor_path": str(self.descriptor_path),
            "environment": dict(self.environment),
        }


def _canonical_path(path: os.PathLike[str] | str, *, label: str) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ArchiveTargetError("archive_isolation_failed", f"{label} is not a usable path") from exc


def _ensure_directory(path: Path, *, mode: int = 0o700) -> Path:
    """Create one owner-only directory and reject a pre-existing symlink."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=mode, parents=False)
        except FileExistsError:
            info = path.lstat()
        else:
            info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ArchiveTargetError("archive_isolation_failed", f"{path} is not a directory")
    try:
        path.chmod(mode)
        info = path.stat()
    except OSError as exc:
        raise ArchiveTargetError("archive_isolation_failed", f"cannot secure {path}") from exc
    if info.st_uid != os.getuid():
        raise ArchiveTargetError("archive_isolation_failed", f"{path} is not owned by the invoking UID")
    return path


def _ensure_owner_tree(path: Path, *, mode: int = 0o700) -> Path:
    """Create a path below a trusted base without recursive symlink following."""

    path = path.resolve(strict=False)
    missing: list[Path] = []
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            missing.append(current)
        else:
            if stat.S_ISLNK(info.st_mode):
                raise ArchiveTargetError("archive_isolation_failed", f"{current} is a symlink")
            break
        if current == current.parent:
            break
        current = current.parent
    for directory in reversed(missing):
        _ensure_directory(directory, mode=mode)
    return _ensure_directory(path, mode=mode)


def _assert_outside(path: Path, root: Path, *, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        return
    raise ArchiveTargetError("archive_isolation_failed", f"{label} must be outside the caller checkout")


def _assert_below(path: Path, root: Path, *, label: str) -> Path:
    """Resolve a child and reject an existing symlink that escapes its base."""

    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ArchiveTargetError("archive_isolation_failed", f"{label} escapes the run-local state root") from exc
    return resolved


def _assert_caller_baseline(path: Path, caller_root: Path) -> None:
    if path.is_symlink():
        raise ArchiveTargetError("archive_isolation_failed", "caller baseline must not be a symlink")
    path = path.resolve(strict=False)
    try:
        path.relative_to(caller_root)
    except ValueError as exc:
        raise ArchiveTargetError("archive_isolation_failed", "baseline must belong to the caller project") from exc


def _write_descriptor(path: Path, descriptor: dict) -> None:
    payload = json.dumps(descriptor, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=".sandbox-config-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ArchiveTargetError("archive_isolation_failed", "cannot write the review descriptor") from exc


def _archive_review_descriptor(
    preflight: ArchivePreflight,
    *,
    plugin_check: PluginCheckPin,
    plugin_path: Path,
    baseline_path: Path,
    artifact_dir: Path,
    review_instance: str,
    extraction_root: Path,
    sandbox_home: Path,
    review_project_root: Path,
    wordpress_version: str | None = None,
    php_version: str | None = None,
    sandbox_revision: str | None = None,
) -> dict:
    """Build the allowlisted descriptor; no global config is consulted."""

    return {
        "slug": preflight.archive_slug,
        "plugins": {
            preflight.archive_slug: {
                "path": str(plugin_path),
                "active": False,
                "onDemand": False,
            },
            "plugin-check": {
                "zip": plugin_check.source,
                "active": True,
                "onDemand": False,
            },
        },
        "themes": [],
        "mappings": {},
        "mappings_inactive": {},
        "server": "nginx",
        "phpVersion": php_version,
        "wpVersion": wordpress_version,
        "multisite": False,
        "config": {},
        "port": None,
        "pluginCheck": {
            "excludeDirectories": [],
            "versionFile": Path(preflight.main_file).name,
            "baselineFile": "archive-review-baseline.json",
        },
        "archiveReview": {
            "inputMode": "archive",
            "archiveSha256": preflight.archive_sha256,
            "memberManifestSha256": preflight.member_manifest_sha256,
            "archiveSlug": preflight.archive_slug,
            "mainFile": preflight.main_file,
            "memberCount": preflight.member_count,
            "callerBaseline": str(baseline_path),
            "artifactDir": str(artifact_dir),
            "reviewInstance": review_instance,
            "extractionRoot": str(extraction_root),
            "sandboxHome": str(sandbox_home),
            "reviewProjectRoot": str(review_project_root),
            "runtime": {"kind": "compose", "scope": "local", "remote": False},
            "target": {"active": False, "readOnly": True},
            "pluginCheck": {
                **plugin_check.as_dict(),
                "active": True,
            },
            "provenance": {
                "pluginCheck": plugin_check.as_dict(),
                "wordpress": wordpress_version,
                "php": php_version,
                "sandbox": sandbox_revision,
            },
            "environmentAllowlist": ["SANDBOX_HOME", "SANDBOX_PROJECT_ROOTS"],
        },
    }


def build_archive_review_target(
    caller_project_root: os.PathLike[str] | str,
    preflight: ArchivePreflight,
    *,
    run_id: str,
    sandbox_home: os.PathLike[str] | str,
    plugin_check: PluginCheckPin,
    baseline_path: os.PathLike[str] | str | None = None,
    wordpress_version: str | None = None,
    php_version: str | None = None,
    sandbox_revision: str | None = None,
) -> ArchiveReviewTarget:
    """Prepare a run-local, local-Compose archive target.

    The caller must keep the returned directories until journaled cleanup is
    complete.  Archive bytes are not copied here; call
    ``preflight_archive(..., extraction_root=target.extraction_root)`` while
    the same archive session is open, then hand the target to the later runtime
    adapter.
    """

    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ArchiveTargetError("archive_isolation_failed", "run_id must be lowercase and path-safe")
    caller_root = _canonical_path(caller_project_root, label="caller project root")
    if not caller_root.is_dir():
        raise ArchiveTargetError("archive_isolation_failed", "caller project root must be a directory")
    base = _canonical_path(sandbox_home, label="sandbox home")
    _assert_outside(base, caller_root, label="run state")
    baseline_input = Path(baseline_path or (caller_root / "plugin-check-baseline.json")).expanduser()
    if not baseline_input.is_absolute():
        baseline_input = caller_root / baseline_input
    _assert_caller_baseline(baseline_input, caller_root)
    baseline = _canonical_path(baseline_input, label="baseline")

    state_root = _assert_below(base / "runtime" / "plugin-check", base, label="review state")
    reports_root = _assert_below(state_root / "reports", base, label="report state")
    _ensure_owner_tree(state_root)
    _ensure_owner_tree(reports_root)
    run_root_candidate = state_root / run_id
    artifact_candidate = reports_root / run_id
    run_root = _assert_below(run_root_candidate, base, label="review state")
    artifact_dir = _assert_below(artifact_candidate, base, label="report state")
    _assert_outside(run_root, caller_root, label="review state")
    if run_root.exists() or artifact_dir.exists():
        raise ArchiveTargetError("archive_isolation_failed", f"review run {run_id!r} already exists")
    try:
        run_root.mkdir(mode=0o700)
        artifact_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ArchiveTargetError("archive_isolation_failed", f"review run {run_id!r} already exists") from exc
    _ensure_directory(run_root)
    _ensure_directory(artifact_dir)
    run_sandbox_home = run_root / "sandbox"
    review_instance = f"plugin-check-{run_id}"
    # Give the disposable project the same path-safe identity that the
    # registry/Compose adapter will use.  A fixed ``project`` basename would
    # make concurrent archive runs collide on the Docker project name.
    review_project_root = run_root / review_instance
    extraction_root = run_root / "extracted"
    _ensure_owner_tree(run_sandbox_home)
    _ensure_owner_tree(review_project_root)
    _ensure_owner_tree(extraction_root)

    descriptor = _archive_review_descriptor(
        preflight,
        plugin_check=plugin_check,
        plugin_path=extraction_root / preflight.archive_slug,
        baseline_path=baseline,
        artifact_dir=artifact_dir,
        review_instance=review_instance,
        extraction_root=extraction_root,
        sandbox_home=run_sandbox_home,
        review_project_root=review_project_root,
        wordpress_version=wordpress_version,
        php_version=php_version,
        sandbox_revision=sandbox_revision,
    )
    descriptor_path = review_project_root / "sandbox.config.json"
    _write_descriptor(descriptor_path, descriptor)
    environment = {
        "SANDBOX_HOME": str(run_sandbox_home),
        "SANDBOX_PROJECT_ROOTS": str(review_project_root),
    }
    return ArchiveReviewTarget(
        caller_project_root=caller_root,
        archive_path=preflight.archive_path,
        archive_sha256=preflight.archive_sha256,
        extraction_root=extraction_root,
        archive_slug=preflight.archive_slug,
        main_file=preflight.main_file,
        baseline_path=baseline,
        artifact_dir=artifact_dir,
        review_project_root=review_project_root,
        review_instance=review_instance,
        sandbox_home=run_sandbox_home,
        project_roots=(review_project_root,),
        descriptor_path=descriptor_path,
        descriptor=descriptor,
        environment=environment,
    )


__all__ = [
    "ArchiveReviewTarget",
    "ArchiveTargetError",
    "PluginCheckPin",
    "build_archive_review_target",
]
