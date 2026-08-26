"""Durable journal and cleanup primitives for archive review runs.

This module owns no Docker, registry, or filesystem-specific cleanup policy.
The later archive runtime supplies one callback and one absence/retention check
for each cleanup plane.  The service records intent before every callback,
checks the result afterwards, and never turns an uncertain plane into a
successful receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping


PLANE_ORDER = (
    "container",
    "network",
    "volume",
    "runtime",
    "registry",
    "extraction",
    "report",
)
_PLANE_SET = frozenset(PLANE_ORDER)
_PHASE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


class ArchiveJournalError(ValueError):
    """A journal could not be created, trusted, or durably updated."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class ArchivePhaseError(RuntimeError):
    """A lifecycle phase failed after its state was persisted in the journal."""

    def __init__(self, phase: str):
        self.code = "archive_phase_failed"
        self.phase = phase
        super().__init__(f"archive_phase_failed: {phase}")


class ArchiveCleanupError(RuntimeError):
    """Cleanup could not produce a trustworthy receipt."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CleanupPlane:
    """One reversible cleanup action and its independent postcondition check."""

    name: str
    cleanup: Callable[[], object]
    verify: Callable[[], bool]
    desired: str | None = None

    def __post_init__(self) -> None:
        if self.name not in _PLANE_SET:
            raise ArchiveCleanupError("archive_cleanup_plane_invalid", f"unknown cleanup plane {self.name!r}")
        expected = "complete" if self.name == "report" else "absent"
        if self.desired is None:
            object.__setattr__(self, "desired", expected)
        elif self.desired != expected:
            raise ArchiveCleanupError(
                "archive_cleanup_plane_invalid",
                f"plane {self.name!r} must resolve to {expected!r}",
            )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _private_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ArchiveJournalError("archive_journal_parent_missing", f"journal directory is unavailable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ArchiveJournalError("archive_journal_parent_invalid", f"journal parent is not a directory: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ArchiveJournalError("archive_journal_parent_permissions", f"journal parent is not owner-only: {path}")
    if info.st_uid != os.getuid():
        raise ArchiveJournalError("archive_journal_parent_owner", f"journal parent is not owned by the invoking UID: {path}")


def _private_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ArchiveJournalError("archive_journal_missing", f"journal is unavailable: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ArchiveJournalError("archive_journal_invalid", f"journal is not a regular file: {path}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ArchiveJournalError("archive_journal_permissions", f"journal is not owner-only: {path}")
    if info.st_uid != os.getuid():
        raise ArchiveJournalError("archive_journal_owner", f"journal is not owned by the invoking UID: {path}")


def _safe_target(target: Mapping[str, object]) -> dict[str, object]:
    """Keep only the non-secret identity fields allowed in the journal."""

    allowed = {
        "caller_project_root",
        "archive_path",
        "archive_sha256",
        "extraction_root",
        "archive_slug",
        "main_file",
        "baseline_path",
        "artifact_dir",
        "review_project_root",
        "review_instance",
        "sandbox_home",
        "project_roots",
        "descriptor_path",
    }
    output: dict[str, object] = {}
    for key in sorted(allowed):
        if key not in target:
            continue
        value = target[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            output[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(item, (str, int, float, bool)) for item in value):
            output[key] = list(value)
        else:
            output[key] = str(value)
    return output


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
    """Write mode-0600 JSON with an atomic replace in an owner-only parent."""

    _private_dir(path.parent)
    encoded = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    temporary: Path | None = None
    fd: int | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".archive-journal-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
        _private_file(path)
    except OSError as exc:
        raise ArchiveJournalError("archive_journal_write_failed", "journal update could not be persisted") from exc
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


class ArchiveReviewJournal:
    """Owner-only JSON ledger that survives an interrupted review run."""

    VERSION = 1

    def __init__(self, path: Path, data: dict[str, object]):
        self.path = path
        self.data = data

    @classmethod
    def create(
        cls,
        path: os.PathLike[str] | str,
        *,
        run_id: str,
        target: Mapping[str, object],
        planes: Iterable[str] = PLANE_ORDER,
    ) -> "ArchiveReviewJournal":
        if not isinstance(run_id, str) or not _PHASE_RE.fullmatch(run_id):
            raise ArchiveJournalError("archive_journal_invalid", "run_id is not path-safe")
        journal_path = Path(path).expanduser()
        if not journal_path.is_absolute():
            journal_path = journal_path.resolve()
        _private_dir(journal_path.parent)
        names = tuple(planes)
        if set(names) != _PLANE_SET or len(names) != len(_PLANE_SET):
            raise ArchiveJournalError("archive_journal_invalid", "journal must declare every cleanup plane exactly once")
        try:
            archive_sha = str(target.get("archive_sha256") or "")
            receipt = hashlib.sha256(f"{run_id}|{archive_sha}|archive-receipt-v1".encode()).hexdigest()[:24]
            now = _now()
            data: dict[str, object] = {
                "version": cls.VERSION,
                "run_id": run_id,
                "receipt_id": receipt,
                "created_at": now,
                "updated_at": now,
                "phase": "journal:in_progress",
                "target": _safe_target(target),
                "planes": {
                    name: {
                        "desired": "complete" if name == "report" else "absent",
                        "status": "pending",
                        "attempts": 0,
                        "updated_at": now,
                    }
                    for name in PLANE_ORDER
                },
                "recovery_required": False,
            }
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(journal_path, flags, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    fd = -1
                    json.dump(data, stream, indent=2, sort_keys=True, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if fd >= 0:
                    os.close(fd)
            os.chmod(journal_path, 0o600)
            _private_file(journal_path)
            data["phase"] = "journal:complete"
            data["updated_at"] = _now()
            _atomic_json_write(journal_path, data)
            return cls(journal_path, data)
        except FileExistsError as exc:
            raise ArchiveJournalError("archive_journal_exists", f"journal already exists: {journal_path}") from exc
        except ArchiveJournalError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise ArchiveJournalError("archive_journal_write_failed", "journal could not be created") from exc

    @classmethod
    def open(cls, path: os.PathLike[str] | str) -> "ArchiveReviewJournal":
        journal_path = Path(path).expanduser()
        if not journal_path.is_absolute():
            journal_path = journal_path.resolve()
        _private_dir(journal_path.parent)
        _private_file(journal_path)
        try:
            data = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ArchiveJournalError("archive_journal_invalid", "journal JSON could not be read") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != cls.VERSION
            or not isinstance(data.get("run_id"), str)
            or not _PHASE_RE.fullmatch(data["run_id"])
            or not isinstance(data.get("receipt_id"), str)
            or not re.fullmatch(r"[0-9a-f]{24}", data["receipt_id"])
            or not isinstance(data.get("planes"), dict)
        ):
            raise ArchiveJournalError("archive_journal_invalid", "journal schema is not recognised")
        if set(data["planes"]) != _PLANE_SET:
            raise ArchiveJournalError("archive_journal_invalid", "journal cleanup planes are incomplete")
        for name, plane in data["planes"].items():
            if not isinstance(plane, dict) or plane.get("desired") not in {"absent", "complete"} or plane.get("status") not in {"pending", "in_progress", "absent", "complete", "unknown"}:
                raise ArchiveJournalError("archive_journal_invalid", f"journal plane {name!r} is malformed")
        return cls(journal_path, data)

    @property
    def run_id(self) -> str:
        return str(self.data["run_id"])

    @property
    def receipt_id(self) -> str:
        return str(self.data["receipt_id"])

    def _persist(self) -> None:
        self.data["updated_at"] = _now()
        try:
            _atomic_json_write(self.path, self.data)
        except ArchiveJournalError:
            raise
        except OSError as exc:
            raise ArchiveJournalError("archive_journal_write_failed", "journal update could not be persisted") from exc

    def transition(self, phase: str, *, error: str | None = None) -> None:
        if not isinstance(phase, str) or not _PHASE_RE.fullmatch(phase.split(":", 1)[0]):
            raise ArchiveJournalError("archive_journal_invalid", "phase is not path-safe")
        self.data["phase"] = phase
        if error is None:
            self.data.pop("last_error", None)
        else:
            self.data["last_error"] = str(error)[:120]
        self._persist()

    def execute_phase(self, phase: str, action: Callable[[], object]) -> object:
        """Persist intent, run one lifecycle action, and persist its outcome."""

        self.transition(f"{phase}:in_progress")
        try:
            result = action()
        except BaseException as exc:
            # A SIGKILL cannot run this block, so the in-progress marker itself
            # is the recovery signal.  Other interruptions still leave a
            # durable failure marker before being propagated.
            try:
                self.transition(f"{phase}:failed", error=type(exc).__name__)
            except ArchiveJournalError:
                pass
            if isinstance(exc, Exception):
                raise ArchivePhaseError(phase) from exc
            raise
        self.transition(f"{phase}:complete")
        return result

    def mark_plane(self, name: str, status: str, *, error: str | None = None) -> None:
        if name not in _PLANE_SET or status not in {"pending", "in_progress", "absent", "complete", "unknown"}:
            raise ArchiveJournalError("archive_journal_invalid", "invalid cleanup plane update")
        planes = self.data.get("planes")
        if not isinstance(planes, dict) or not isinstance(planes.get(name), dict):
            raise ArchiveJournalError("archive_journal_invalid", "journal cleanup plane is missing")
        plane = planes[name]
        plane["status"] = status
        plane["attempts"] = int(plane.get("attempts") or 0) + (1 if status == "in_progress" else 0)
        plane["updated_at"] = _now()
        if error is None:
            plane.pop("last_error", None)
        else:
            plane["last_error"] = str(error)[:120]
        self._persist()

    def set_recovery_required(self, required: bool) -> None:
        self.data["recovery_required"] = bool(required)
        self._persist()


def _safe_bool(check: Callable[[], bool]) -> bool:
    value = check()
    return value is True


class ArchiveCleanupService:
    """Run idempotent, independently verified cleanup for all seven planes."""

    def __init__(self, journal: ArchiveReviewJournal, planes: Iterable[CleanupPlane]):
        by_name: dict[str, CleanupPlane] = {}
        for plane in planes:
            if plane.name in by_name:
                raise ArchiveCleanupError("archive_cleanup_plane_invalid", f"duplicate cleanup plane {plane.name!r}")
            by_name[plane.name] = plane
        if set(by_name) != _PLANE_SET:
            raise ArchiveCleanupError("archive_cleanup_plane_invalid", "cleanup requires every plane")
        self.journal = journal
        self.planes = tuple(by_name[name] for name in PLANE_ORDER)

    def _verify(self, plane: CleanupPlane) -> bool:
        try:
            return _safe_bool(plane.verify)
        except BaseException:
            return False

    def cleanup(self) -> dict[str, object]:
        """Attempt every plane; unknown state is retained for recovery."""

        try:
            self.journal.transition("cleanup:in_progress")
        except ArchiveJournalError as exc:
            raise ArchiveCleanupError("archive_journal_write_failed", str(exc)) from exc
        statuses: dict[str, str] = {}
        fatal_interrupt: BaseException | None = None
        for plane in self.planes:
            try:
                prior = self.journal.data["planes"][plane.name].get("status")
                self.journal.mark_plane(plane.name, "in_progress")
                if prior in {"absent", "complete"} and self._verify(plane):
                    status = str(prior)
                else:
                    operation_error: BaseException | None = None
                    try:
                        plane.cleanup()
                    except BaseException as exc:
                        operation_error = exc
                    status = str(plane.desired) if self._verify(plane) else "unknown"
                    if operation_error is not None and status == "unknown":
                        self.journal.mark_plane(plane.name, "unknown", error=type(operation_error).__name__)
                        statuses[plane.name] = "unknown"
                        if not isinstance(operation_error, Exception):
                            fatal_interrupt = operation_error
                        continue
                    if operation_error is not None and not isinstance(operation_error, Exception):
                        fatal_interrupt = operation_error
                self.journal.mark_plane(plane.name, status)
                statuses[plane.name] = status
            except BaseException as exc:
                try:
                    self.journal.mark_plane(plane.name, "unknown", error=type(exc).__name__)
                except ArchiveJournalError:
                    pass
                statuses[plane.name] = "unknown"
                if not isinstance(exc, Exception):
                    fatal_interrupt = exc
        # A callback failure can still be safe if its postcondition proves the
        # plane absent/retained.  Anything else is explicitly unknown.
        for plane in self.planes:
            statuses.setdefault(plane.name, "unknown")
        complete = all(statuses[name] == self.planes[index].desired for index, name in enumerate(PLANE_ORDER))
        final_status = "complete" if complete else "unknown"
        try:
            self.journal.data["cleanup"] = {
                "status": final_status,
                "receipt": self.journal.receipt_id,
                "planes": dict(statuses),
                "finished_at": _now(),
            }
            self.journal.data["recovery_required"] = not complete
            self.journal.transition("cleanup:complete" if complete else "cleanup:unknown")
        except ArchiveJournalError as exc:
            raise ArchiveCleanupError("archive_journal_write_failed", str(exc)) from exc
        receipt = {
            "status": final_status,
            "receipt": self.journal.receipt_id,
            "planes": dict(statuses),
            "journal": str(self.journal.path),
            "recovery_required": not complete,
        }
        if fatal_interrupt is not None:
            raise fatal_interrupt
        return receipt


def recover_archive_cleanup(
    journal_path: os.PathLike[str] | str,
    planes: Iterable[CleanupPlane],
) -> dict[str, object]:
    """Load an owner-only journal and retry the same cleanup contract."""

    try:
        journal = ArchiveReviewJournal.open(journal_path)
    except ArchiveJournalError as exc:
        raise ArchiveCleanupError("archive_cleanup_recovery_unavailable", str(exc)) from exc
    return ArchiveCleanupService(journal, planes).cleanup()


__all__ = [
    "ArchiveCleanupError",
    "ArchiveJournalError",
    "ArchivePhaseError",
    "ArchiveReviewJournal",
    "CleanupPlane",
    "ArchiveCleanupService",
    "PLANE_ORDER",
    "recover_archive_cleanup",
]
