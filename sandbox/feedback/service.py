from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from typing import Any, Callable


CATEGORIES = frozenset({"bug", "incident", "idea", "usability", "other"})
SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ /-]{0,79}$")
_SAFE_REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|passphrase|authorization|cookie|credential|secret|api[_-]?key)"
    r"\s*[=:]\s*(?:bearer\s+)?[^\s,;]+"
)
_BARE_SECRET = re.compile(
    r"(?i)(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}|BEGIN (?:RSA|OPENSSH|EC|PRIVATE) KEY)"
)


class FeedbackError(RuntimeError):
    def __init__(self, message: str, code: str = "invalid_feedback") -> None:
        super().__init__(message)
        self.code = code


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("feedback timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sanitize_text(value: str, field: str, *, maximum: int, required: bool = False) -> tuple[str, bool]:
    if not isinstance(value, str):
        raise FeedbackError(f"{field} must be a string")
    text = value.strip()
    if required and not text:
        raise FeedbackError(f"{field} is required")
    if len(text) > maximum:
        raise FeedbackError(f"{field} must be at most {maximum} characters")
    if _CONTROL.search(text):
        raise FeedbackError(f"{field} contains unsupported control characters")
    redacted = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    redacted = _BARE_SECRET.sub("[redacted]", redacted)
    return redacted, redacted != text


def _project_context(project_dir: str | None) -> dict[str, str] | None:
    if project_dir is None:
        return None
    if not isinstance(project_dir, str) or not project_dir.strip():
        raise FeedbackError("project_dir must be a non-empty path")
    path = Path(project_dir).expanduser().resolve()
    if not path.is_dir():
        raise FeedbackError("project_dir does not exist", "project_not_found")
    return {
        "identity": hashlib.sha256(str(path).encode()).hexdigest(),
        "name": path.name,
    }


class FeedbackStore:
    """One immutable JSON record per report; contents are untrusted data."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        self._prepare()
        feedback_id = record.get("feedback_id")
        if not isinstance(feedback_id, str) or not re.fullmatch(r"[a-f0-9]{32}", feedback_id):
            raise FeedbackError("feedback id is invalid")
        created = str(record.get("created_at") or "").replace(":", "").replace("-", "")
        created = re.sub(r"[^0-9TZ.]", "", created)
        target = self.root / f"{created}-{feedback_id}.json"
        descriptor, temporary = tempfile.mkstemp(prefix=".feedback-", suffix=".tmp", dir=self.root)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                raise FeedbackError("feedback record already exists", "feedback_exists")
            os.replace(temporary, target)
            try:
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return record

    def list(self, limit: int) -> tuple[list[dict[str, Any]], int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise FeedbackError("limit must be between 1 and 100")
        try:
            paths = sorted(self.root.glob("*.json"), reverse=True)
        except OSError as exc:
            raise FeedbackError("feedback log is unavailable", "feedback_unavailable") from exc
        records: list[dict[str, Any]] = []
        invalid = 0
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict) or value.get("schema_version") != 1:
                    raise ValueError("invalid feedback record")
                records.append(value)
            except (OSError, ValueError, json.JSONDecodeError):
                invalid += 1
            if len(records) >= limit:
                break
        return records, invalid


class FeedbackService:
    def __init__(self, store: FeedbackStore, *, clock: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _result(ok: bool, action: str, status: str, *, data: dict | None = None,
                error: FeedbackError | None = None) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ok": ok,
            "action": action,
            "status": status,
            "data": data or {},
            "error": None if error is None else {"code": error.code, "message": str(error)},
        }

    def submit(
        self,
        summary: str,
        *,
        details: str = "",
        category: str = "other",
        severity: str = "medium",
        source: str = "agent",
        project_dir: str | None = None,
        remote: str | None = None,
        reference: str = "",
    ) -> dict[str, Any]:
        try:
            if category not in CATEGORIES:
                raise FeedbackError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
            if severity not in SEVERITIES:
                raise FeedbackError(f"severity must be one of: {', '.join(sorted(SEVERITIES))}")
            if not isinstance(source, str) or not _SAFE_SOURCE.fullmatch(source.strip()):
                raise FeedbackError("source contains unsupported characters")
            if remote is not None and (
                not isinstance(remote, str) or not _SAFE_REMOTE.fullmatch(remote.strip())
            ):
                raise FeedbackError("remote contains unsupported characters")
            clean_summary, summary_redacted = _sanitize_text(
                summary, "summary", maximum=200, required=True,
            )
            clean_details, details_redacted = _sanitize_text(details, "details", maximum=8000)
            clean_reference, reference_redacted = _sanitize_text(
                reference, "reference", maximum=300,
            )
            record = {
                "schema_version": 1,
                "feedback_id": secrets.token_hex(16),
                "created_at": _timestamp(self.clock()),
                "category": category,
                "severity": severity,
                "source": source.strip(),
                "summary": clean_summary,
                "details": clean_details,
                "reference": clean_reference,
                "remote": remote.strip() if remote else None,
                "project": _project_context(project_dir),
                "redacted": summary_redacted or details_redacted or reference_redacted,
                "trust": "untrusted_data",
            }
            self.store.save(record)
            return self._result(True, "submit", "recorded", data={"feedback": record})
        except FeedbackError as exc:
            return self._result(False, "submit", "refused", error=exc)
        except OSError:
            return self._result(
                False, "submit", "failed",
                error=FeedbackError("feedback log is unavailable", "feedback_unavailable"),
            )

    def list(self, limit: int = 20) -> dict[str, Any]:
        try:
            records, invalid = self.store.list(limit)
            return self._result(True, "list", "complete", data={
                "feedback": records,
                "count": len(records),
                "invalid_record_count": invalid,
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "list", "failed", error=exc)
