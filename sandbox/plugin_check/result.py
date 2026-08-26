"""Archive finding identity, baseline, and retained-artifact helpers.

The functions here are deliberately independent of the CLI and runtime.  They
make it possible for the later command integration to prove three ordering
rules: archive paths are relative before comparison, a baseline update happens
only after a complete cleanup receipt, and persisted reports contain no
temporary absolute paths or secrets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence


PLANE_ORDER = (
    "container",
    "network",
    "volume",
    "runtime",
    "registry",
    "extraction",
    "report",
)
_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_KEY_RE = re.compile(r"^[^/\\\x00]+::[^/\\\x00]+$")


class ArchiveResultError(ValueError):
    """A finding, baseline, or artifact would violate archive-mode policy."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


# More specific aliases are useful at integration boundaries without creating
# separate exception hierarchies for baseline and artifact failures.
ArchiveArtifactError = ArchiveResultError


def _relative_finding_path(raw: object, extracted_plugin_root: Path) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ArchiveResultError("archive_finding_path", "finding file must be a non-empty string")
    value = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if _DRIVE_RE.match(value):
        raise ArchiveResultError("archive_finding_path", "drive-letter finding paths are not allowed")
    if value.startswith("/"):
        try:
            value = os.path.relpath(
                str(Path(value).expanduser().resolve(strict=False)),
                str(extracted_plugin_root),
            )
        except (OSError, ValueError) as exc:
            raise ArchiveResultError("archive_finding_path", "finding path cannot be made relative") from exc
        value = value.replace("\\", "/")
    else:
        root_name = unicodedata.normalize("NFC", extracted_plugin_root.name)
        if value == root_name:
            value = ""
        elif value.startswith(root_name + "/"):
            value = value[len(root_name) + 1:]
    if not value or value.startswith("/") or _DRIVE_RE.match(value):
        raise ArchiveResultError("archive_finding_path", "finding path is not relative to the extracted plugin")
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ArchiveResultError("archive_finding_path", "finding path contains an unsafe component")
    if len(parts) > 32:
        raise ArchiveResultError("archive_finding_path", "finding path exceeds the canonical depth limit")
    try:
        if len(value.encode("utf-8")) > 240:
            raise ArchiveResultError("archive_finding_path", "finding path exceeds the canonical path limit")
    except UnicodeEncodeError as exc:
        raise ArchiveResultError("archive_finding_path", "finding path is not valid UTF-8") from exc
    return "/".join(parts)


def normalize_archive_findings(
    findings: Sequence[Mapping[str, object]],
    extracted_plugin_root: os.PathLike[str] | str,
) -> list[dict[str, object]]:
    """Copy findings with file identities relative to the extracted plugin root."""

    root = Path(extracted_plugin_root).expanduser().resolve(strict=False)
    normalized: list[dict[str, object]] = []
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise ArchiveResultError("archive_finding_invalid", "finding must be an object")
        item = dict(finding)
        item["file"] = _relative_finding_path(finding.get("file"), root)
        normalized.append(item)
    return normalized


def archive_error_counts(findings: Sequence[Mapping[str, object]]) -> dict[str, int]:
    """Return the source Plugin Check baseline identity for normalized findings."""

    counts: dict[str, int] = {}
    for finding in findings:
        if finding.get("type") != "ERROR":
            continue
        file_name = finding.get("file")
        code = finding.get("code")
        if not isinstance(file_name, str) or not isinstance(code, str) or not code:
            raise ArchiveResultError("archive_finding_invalid", "ERROR finding lacks file or code")
        key = f"{file_name}::{code}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def cleanup_receipt_complete(receipt: Mapping[str, object]) -> bool:
    """Return true only for a complete, seven-plane cleanup receipt."""

    if not isinstance(receipt, Mapping) or receipt.get("status") != "complete" or receipt.get("recovery_required"):
        return False
    planes = receipt.get("planes")
    if not isinstance(planes, Mapping) or set(planes) != set(PLANE_ORDER):
        return False
    return all(planes[name] == ("complete" if name == "report" else "absent") for name in PLANE_ORDER)


def _baseline_path(path: os.PathLike[str] | str, caller_project_root: os.PathLike[str] | str) -> tuple[Path, Path]:
    caller = Path(caller_project_root).expanduser().resolve(strict=False)
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = caller / raw
    if raw.is_symlink():
        raise ArchiveResultError("archive_baseline_invalid", "caller baseline must not be a symlink")
    resolved = raw.resolve(strict=False)
    try:
        resolved.relative_to(caller)
    except ValueError as exc:
        raise ArchiveResultError("archive_baseline_invalid", "baseline must belong to the caller project") from exc
    return resolved, caller


def update_caller_baseline_atomic(
    path: os.PathLike[str] | str,
    counts: Mapping[str, int],
    cleanup_receipt: Mapping[str, object],
    *,
    caller_project_root: os.PathLike[str] | str,
) -> Path:
    """Atomically replace only the caller baseline after complete cleanup."""

    if not cleanup_receipt_complete(cleanup_receipt):
        raise ArchiveResultError("archive_cleanup_unknown", "baseline update requires a complete cleanup receipt")
    baseline, _caller = _baseline_path(path, caller_project_root)
    if not isinstance(counts, Mapping):
        raise ArchiveResultError("archive_baseline_invalid", "baseline counts must be an object")
    clean: dict[str, int] = {}
    for key, value in counts.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ArchiveResultError("archive_baseline_invalid", "baseline key is not a file/rule identity")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ArchiveResultError("archive_baseline_invalid", "baseline count must be a non-negative integer")
        clean[key] = value
    parent = baseline.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ArchiveResultError("archive_baseline_invalid", "baseline parent is unavailable")
    mode = 0o644
    if baseline.exists():
        try:
            info = baseline.lstat()
        except OSError as exc:
            raise ArchiveResultError("archive_baseline_invalid", "baseline cannot be inspected") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ArchiveResultError("archive_baseline_invalid", "baseline must be a regular file")
        mode = stat.S_IMODE(info.st_mode) or mode
    encoded = (json.dumps(clean, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary: Path | None = None
    fd: int | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".plugin-check-baseline-", suffix=".tmp", dir=parent)
        temporary = Path(name)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, baseline)
        temporary = None
        os.chmod(baseline, mode)
        return baseline
    except OSError as exc:
        raise ArchiveResultError("archive_baseline_write_failed", "caller baseline could not be replaced") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _private_artifact_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ArchiveResultError("archive_artifact_invalid", f"artifact directory is unavailable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ArchiveResultError("archive_artifact_invalid", "artifact directory must be a real directory")
    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid():
        raise ArchiveResultError("archive_artifact_invalid", "artifact directory must be owner-only")


def _atomic_artifact_write(path: Path, data: bytes) -> None:
    _private_artifact_dir(path.parent)
    temporary: Path | None = None
    fd: int | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".archive-artifact-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ArchiveResultError("archive_artifact_write_failed", "artifact could not be persisted") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_findings(findings: object) -> list[dict[str, object]]:
    if not isinstance(findings, list):
        return []
    clean: list[dict[str, object]] = []
    for finding in findings:
        if not isinstance(finding, Mapping) or not isinstance(finding.get("file"), str):
            continue
        file_name = unicodedata.normalize("NFC", finding["file"].replace("\\", "/"))
        if file_name.startswith("/") or _DRIVE_RE.match(file_name) or any(
            not part or part in {".", ".."} for part in file_name.split("/")
        ):
            raise ArchiveResultError("archive_artifact_invalid", "artifact finding path must be relative")
        if len(file_name.encode("utf-8")) > 240 or len(file_name.split("/")) > 32:
            raise ArchiveResultError("archive_artifact_invalid", "artifact finding path exceeds canonical limits")
        item = {
            "file": file_name,
            "type": finding.get("type"),
            "code": finding.get("code"),
            "line": finding.get("line"),
            "column": finding.get("column"),
            "message": finding.get("message"),
        }
        clean.append(item)
    return clean


def _safe_result(result: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "ok", "action", "plugin_slug", "errors", "warnings", "baseline_total",
        "new_count", "violations", "baseline_exists", "message", "error",
        "input_mode", "archive_sha256", "archive_slug", "main_file",
        "member_count", "member_manifest_sha256", "review_instance",
        "checker_provenance", "cleanup", "findings",
    }
    clean: dict[str, object] = {}
    for key in sorted(allowed):
        if key not in result:
            continue
        value = result[key]
        if key == "findings":
            clean[key] = _safe_findings(value)
        elif key == "cleanup" and isinstance(value, Mapping):
            planes = value.get("planes")
            clean[key] = {
                "status": value.get("status"),
                "receipt": value.get("receipt"),
                "planes": dict(planes) if isinstance(planes, Mapping) else {},
                "recovery_required": bool(value.get("recovery_required")),
            }
        elif key == "violations" and isinstance(value, list):
            violations = []
            for item in value:
                if not isinstance(item, Mapping):
                    continue
                identity = item.get("key")
                if not isinstance(identity, str) or not _KEY_RE.fullmatch(identity):
                    raise ArchiveResultError("archive_artifact_invalid", "artifact violation key must be relative")
                violations.append({
                    "key": identity,
                    "current": item.get("current"),
                    "baseline": item.get("baseline"),
                    "delta": item.get("delta"),
                })
            clean[key] = violations
        elif key == "checker_provenance" and isinstance(value, Mapping):
            allowed_provenance = {"plugin_check", "wordpress", "php", "sandbox"}
            clean[key] = {
                str(k): str(v) for k, v in value.items()
                if str(k) in allowed_provenance and "/" not in str(v) and "\\" not in str(v)
            }
        elif key in {"message", "error"} and isinstance(value, str):
            # Typed archive errors are enough for a retained artifact. A free
            # text message containing an absolute path could disclose the
            # disposable checkout, so omit it rather than attempting partial
            # path redaction.
            clean[key] = None if "/" in value or "\\" in value else value[:500]
        elif key == "main_file" and isinstance(value, str):
            if value.startswith("/") or _DRIVE_RE.match(value) or any(
                part in {".", ".."} for part in value.replace("\\", "/").split("/")
            ):
                raise ArchiveResultError("archive_artifact_invalid", "artifact main file must be relative")
            clean[key] = value.replace("\\", "/")
        elif isinstance(value, (str, int, float, bool)) or value is None:
            clean[key] = value
    return clean


def persist_archive_artifact(
    artifact_dir: os.PathLike[str] | str,
    result: Mapping[str, object],
    report_html: str,
    *,
    reports_root: os.PathLike[str] | str | None = None,
    now: datetime | None = None,
) -> dict[str, Path]:
    """Persist a sanitized result/report below an existing owner-only run dir."""

    directory = Path(artifact_dir).expanduser().resolve(strict=False)
    _private_artifact_dir(directory)
    if not isinstance(report_html, str):
        raise ArchiveResultError("archive_artifact_invalid", "report must be text")
    clean_result = _safe_result(result)
    _atomic_artifact_write(
        directory / "result.json",
        (json.dumps(clean_result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"),
    )
    _atomic_artifact_write(directory / "plugin-check-report.html", report_html.encode("utf-8"))
    if reports_root is not None:
        prune_archive_artifacts(reports_root, now=now)
    return {
        "result": directory / "result.json",
        "report": directory / "plugin-check-report.html",
    }


def prune_archive_artifacts(
    reports_root: os.PathLike[str] | str,
    *,
    max_reports: int = 20,
    max_age_days: int = 7,
    now: datetime | None = None,
) -> list[Path]:
    """Retain at most ``max_reports`` recent owner-only run directories."""

    root = Path(reports_root).expanduser().resolve(strict=False)
    _private_artifact_dir(root)
    if isinstance(max_reports, bool) or max_reports < 1 or isinstance(max_age_days, bool) or max_age_days < 1:
        raise ArchiveResultError("archive_artifact_invalid", "retention limits must be positive")
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=max_age_days)
    entries: list[tuple[Path, datetime]] = []
    removed: list[Path] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.is_symlink() or not _RUN_ID_RE.fullmatch(entry.name):
            continue
        _private_artifact_dir(entry)
        try:
            timestamp = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc)
        except OSError as exc:
            raise ArchiveResultError("archive_artifact_invalid", "artifact timestamp is unavailable") from exc
        if timestamp < cutoff:
            shutil.rmtree(entry)
            removed.append(entry)
        else:
            entries.append((entry, timestamp))
    entries.sort(key=lambda item: item[1], reverse=True)
    for entry, _timestamp in entries[max_reports:]:
        shutil.rmtree(entry)
        removed.append(entry)
    return removed


__all__ = [
    "ArchiveArtifactError",
    "ArchiveResultError",
    "archive_error_counts",
    "cleanup_receipt_complete",
    "normalize_archive_findings",
    "persist_archive_artifact",
    "prune_archive_artifacts",
    "update_caller_baseline_atomic",
]
