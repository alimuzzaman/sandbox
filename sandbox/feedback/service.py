from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from typing import Any, Callable

from sandbox.services.redaction import redact_text


CATEGORIES = frozenset({"bug", "incident", "idea", "usability", "other"})
SEVERITIES = frozenset({"low", "medium", "high", "critical"})
REVIEW_STATUSES = frozenset({
    "unreviewed", "open", "in_progress", "resolved", "verified", "blocked",
    "duplicate", "invalid", "wont_fix", "not_applicable",
})
REVIEW_CONFIDENCES = frozenset({"low", "medium", "high"})
MAX_LIMIT = 100
MAX_CURSOR_LENGTH = 512
MAX_EXPORT_BYTES = 1_000_000
MAX_REVIEW_REASON = 500
MAX_REVIEW_EVIDENCE_ITEMS = 8
MAX_REVIEW_EVIDENCE = 240
MAX_REVIEW_LINE_BYTES = 16_384
_SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ /-]{0,79}$")
_SAFE_REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,79}$")
_SAFE_REVIEWER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ /-]{0,79}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_FEEDBACK_ID = re.compile(r"[a-f0-9]{32}\Z")
_FEEDBACK_REF = re.compile(r"[a-f0-9]{8,32}\Z")
_CURSOR_NAME = re.compile(r"[A-Za-z0-9_.TZ:-]{1,240}\.json\Z")
_TIMESTAMP = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")


class FeedbackError(RuntimeError):
    def __init__(self, message: str, code: str = "invalid_feedback") -> None:
        super().__init__(message)
        self.code = code


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("feedback timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_project_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # A project name is a label, never a path.  Basename extraction also keeps
    # hand-written legacy records from exporting an absolute path.
    name = Path(value).name.strip()
    if not name or not _SAFE_PROJECT_NAME.fullmatch(name):
        return None
    return name


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
    redacted = redact_text(text)
    return redacted, redacted != text


def _project_context(project_dir: str | None, project_name: str | None = None) -> dict[str, str] | None:
    if project_dir is None and project_name is None:
        return None
    identity: str
    if project_dir is not None:
        if not isinstance(project_dir, str) or not project_dir.strip():
            raise FeedbackError("project_dir must be a non-empty path")
        path = Path(project_dir).expanduser().resolve()
        if not path.is_dir():
            raise FeedbackError("project_dir does not exist", "project_not_found")
        identity = hashlib.sha256(str(path).encode()).hexdigest()
        if project_name is not None and _safe_project_name(project_name) is None:
            raise FeedbackError("project_name contains unsupported characters")
        name = _safe_project_name(project_name) or _safe_project_name(path.name)
    else:
        # Name-only context is useful to callers that cannot safely disclose a
        # local path.  Prefixing prevents a name-only identity colliding with a
        # real path identity.
        name = _safe_project_name(project_name)
        if name is None:
            raise FeedbackError("project_name contains unsupported characters")
        identity = hashlib.sha256(f"name:{name}".encode()).hexdigest()
    if name is None:
        raise FeedbackError("project name contains unsupported characters")
    return {"identity": identity, "name": name}


def _encode_cursor(name: str) -> str:
    encoded = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")
    return f"v1.{encoded}"


def _decode_cursor(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_CURSOR_LENGTH:
        raise FeedbackError("cursor is invalid", "invalid_cursor")
    if not value.startswith("v1."):
        raise FeedbackError("cursor is invalid", "invalid_cursor")
    try:
        encoded = value[3:]
        encoded += "=" * (-len(encoded) % 4)
        name = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError):
        raise FeedbackError("cursor is invalid", "invalid_cursor") from None
    if not _CURSOR_NAME.fullmatch(name):
        raise FeedbackError("cursor is invalid", "invalid_cursor")
    return name


def _validate_limit(limit: int, *, maximum: int = MAX_LIMIT) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise FeedbackError(f"limit must be between 1 and {maximum}")
    return limit


def _filter_value(value: Any, field: str, *, maximum: int = 160) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > maximum
        or _CONTROL.search(value)
    ):
        raise FeedbackError(f"{field} filter is invalid")
    return value.strip()


def _matches(
    record: dict[str, Any],
    filters: dict[str, Any],
    review: dict[str, Any] | None = None,
) -> bool:
    expected_status = filters.get("status")
    if expected_status is not None:
        actual_status = review.get("status") if isinstance(review, dict) else "unreviewed"
        if actual_status != expected_status:
            return False
    for key in ("category", "severity", "source", "remote"):
        expected = filters.get(key)
        if expected is not None and record.get(key) != expected:
            return False
    project = filters.get("project")
    if project is not None:
        value = record.get("project")
        if not isinstance(value, dict):
            return False
        if project not in {value.get("identity"), value.get("name")}:
            return False
    created = _parse_timestamp(record.get("created_at"))
    since = filters.get("since")
    until = filters.get("until")
    # Malformed timestamps are valid-record failures at read time only when the
    # schema is otherwise valid; they simply cannot satisfy date filters.
    if since is not None and (created is None or created < since):
        return False
    if until is not None and (created is None or created > until):
        return False
    return True


def _normalise_review_event(value: Any) -> dict[str, Any]:
    """Validate and project one untrusted append-only closure event."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid feedback review event")
    feedback_id = value.get("feedback_id")
    if not isinstance(feedback_id, str) or not _FEEDBACK_ID.fullmatch(feedback_id):
        raise ValueError("invalid feedback review id")
    status = value.get("status")
    if not isinstance(status, str) or status not in REVIEW_STATUSES:
        raise ValueError("invalid feedback review status")
    closure = value.get("closure")
    if not isinstance(closure, dict):
        raise ValueError("invalid feedback closure")
    reviewed_at = closure.get("reviewed_at")
    if _parse_timestamp(reviewed_at) is None:
        raise ValueError("invalid feedback review timestamp")
    reviewer = closure.get("reviewer")
    if not isinstance(reviewer, str) or not _SAFE_REVIEWER.fullmatch(reviewer.strip()):
        raise ValueError("invalid feedback reviewer")
    reason_value = closure.get("reason")
    if not isinstance(reason_value, str):
        raise ValueError("invalid feedback review reason")
    reason, _ = _sanitize_text(
        reason_value, "review reason", maximum=MAX_REVIEW_REASON, required=True,
    )
    evidence_value = closure.get("evidence", [])
    if not isinstance(evidence_value, list) or len(evidence_value) > MAX_REVIEW_EVIDENCE_ITEMS:
        raise ValueError("invalid feedback review evidence")
    evidence: list[str] = []
    for item in evidence_value:
        if not isinstance(item, str):
            raise ValueError("invalid feedback review evidence")
        clean, _ = _sanitize_text(
            item, "review evidence", maximum=MAX_REVIEW_EVIDENCE, required=True,
        )
        evidence.append(clean)
    confidence = closure.get("confidence", "medium")
    if not isinstance(confidence, str) or confidence not in REVIEW_CONFIDENCES:
        raise ValueError("invalid feedback review confidence")
    duplicate_of = closure.get("duplicate_of")
    if duplicate_of is not None and (
        not isinstance(duplicate_of, str) or not _FEEDBACK_ID.fullmatch(duplicate_of)
    ):
        raise ValueError("invalid duplicate feedback id")
    clean_closure: dict[str, Any] = {
        "reviewed_at": reviewed_at,
        "reviewer": reviewer.strip(),
        "reason": reason,
        "evidence": evidence,
        "confidence": confidence,
    }
    if duplicate_of is not None:
        clean_closure["duplicate_of"] = duplicate_of
    return {
        "schema_version": 1,
        "feedback_id": feedback_id,
        "status": status,
        "closure": clean_closure,
    }


def _public_review(value: Any) -> tuple[str, dict[str, Any] | None]:
    """Return the bounded status/closure projection for a record response."""
    if not isinstance(value, dict):
        return "unreviewed", None
    try:
        event = _normalise_review_event(value)
    except (FeedbackError, ValueError, TypeError):
        return "unreviewed", None
    return event["status"], dict(event["closure"])


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
        if not isinstance(feedback_id, str) or not _FEEDBACK_ID.fullmatch(feedback_id):
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

    @property
    def review_path(self) -> Path:
        """Append-only review journal kept beside immutable report records."""
        return self.root / "closures.jsonl"

    def append_review(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append one validated closure event without rewriting reports."""
        normalised = _normalise_review_event(event)
        self._prepare()
        line = json.dumps(normalised, sort_keys=True, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > MAX_REVIEW_LINE_BYTES:
            raise FeedbackError("feedback review event is too large")
        descriptor = os.open(
            self.review_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, encoded)
            if written != len(encoded):
                raise OSError("feedback review event was only partially written")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return normalised

    def latest_reviews(self) -> tuple[dict[str, dict[str, Any]], int]:
        """Read the latest valid closure event for each ID and count bad lines."""
        path = self.review_path
        if not path.exists():
            return {}, 0
        if path.is_symlink() or not path.is_file():
            return {}, 1
        latest: dict[str, dict[str, Any]] = {}
        invalid = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                while True:
                    raw = handle.readline(MAX_REVIEW_LINE_BYTES + 1)
                    if not raw:
                        break
                    if len(raw.encode("utf-8", errors="ignore")) > MAX_REVIEW_LINE_BYTES:
                        invalid += 1
                        if not raw.endswith("\n"):
                            while raw and not raw.endswith("\n"):
                                raw = handle.readline(MAX_REVIEW_LINE_BYTES + 1)
                        continue
                    try:
                        value = _normalise_review_event(json.loads(raw))
                    except (OSError, UnicodeError, ValueError, TypeError, FeedbackError, json.JSONDecodeError):
                        invalid += 1
                        continue
                    latest[value["feedback_id"]] = value
        except (OSError, UnicodeError) as exc:
            raise FeedbackError("feedback review log is unavailable", "feedback_unavailable") from exc
        return latest, invalid

    def records(self) -> tuple[list[dict[str, Any]], int]:
        """Return all valid reports and the number withheld as invalid."""
        records: list[dict[str, Any]] = []
        invalid = 0
        for path in self._paths():
            try:
                records.append(self._read(path))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                invalid += 1
        return records, invalid

    def _paths(self) -> list[Path]:
        try:
            return sorted(self.root.glob("*.json"), key=lambda item: item.name, reverse=True)
        except OSError as exc:
            raise FeedbackError("feedback log is unavailable", "feedback_unavailable") from exc

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise OSError("feedback record is not a regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("invalid feedback record")
        return value

    def query(
        self,
        limit: int,
        *,
        cursor: str | None = None,
        filters: dict[str, Any] | None = None,
        reviews: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        _validate_limit(limit)
        cursor_name = _decode_cursor(cursor)
        paths = self._paths()
        start = 0
        if cursor_name is not None:
            names = {path.name for path in paths}
            if cursor_name not in names:
                raise FeedbackError("cursor does not identify a retained record", "invalid_cursor")
            start = next(index for index, path in enumerate(paths) if path.name == cursor_name) + 1
        filters = filters or {}
        records: list[dict[str, Any]] = []
        selected_names: list[str] = []
        invalid = 0
        matching_after_limit = False
        for index, path in enumerate(paths):
            try:
                value = self._read(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                # Count every invalid file, including records after the
                # display limit.  This is intentionally independent of paging.
                invalid += 1
                continue
            review = (reviews or {}).get(value.get("feedback_id"))
            if index < start or not _matches(value, filters, review):
                continue
            if len(records) < limit:
                records.append(value)
                selected_names.append(path.name)
            else:
                matching_after_limit = True
        next_cursor = _encode_cursor(selected_names[-1]) if matching_after_limit and selected_names else None
        return {
            "records": records,
            "invalid_record_count": invalid,
            "next_cursor": next_cursor,
            "has_more": matching_after_limit,
        }

    def list(self, limit: int, cursor: str | None = None, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        """Compatibility tuple for older callers; use :meth:`query` for paging."""
        page = self.query(limit, cursor=cursor, filters=filters)
        return page["records"], page["invalid_record_count"]

    def find(self, feedback_id: str) -> dict[str, Any] | None:
        if not isinstance(feedback_id, str) or not _FEEDBACK_ID.fullmatch(feedback_id):
            raise FeedbackError("feedback id is invalid")
        for path in self._paths():
            try:
                value = self._read(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
            if value.get("feedback_id") == feedback_id:
                return value
        return None

    def resolve(self, feedback_ref: str) -> dict[str, Any] | None:
        """Resolve an exact ID or a unique lower-hex ID prefix.

        Exact 32-character IDs retain the established :meth:`find` behavior.
        Prefix resolution reads only valid regular records, skips malformed or
        symlink entries, and remembers the newest record for each canonical ID.
        Multiple distinct canonical IDs are an explicit ambiguity rather than a
        guess; the caller receives no candidate details.
        """
        if not isinstance(feedback_ref, str) or not _FEEDBACK_REF.fullmatch(feedback_ref):
            # Validate before touching the store so malformed references cannot
            # trigger a filesystem scan or leak storage availability details.
            raise FeedbackError("feedback id is invalid")
        if len(feedback_ref) == 32:
            return self.find(feedback_ref)

        matches: dict[str, dict[str, Any]] = {}
        for path in self._paths():
            try:
                value = self._read(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
            canonical = value.get("feedback_id")
            if (not isinstance(canonical, str) or not _FEEDBACK_ID.fullmatch(canonical)
                    or not canonical.startswith(feedback_ref)):
                continue
            # _paths() is newest-first. Keeping the first record means duplicate
            # files for one canonical ID resolve to the newest valid record.
            matches.setdefault(canonical, value)
            if len(matches) > 1:
                raise FeedbackError(
                    "feedback id prefix is ambiguous", "feedback_id_ambiguous"
                )
        return next(iter(matches.values()), None)

    def candidates(
        self,
        cutoff: datetime,
        *,
        limit: int = MAX_LIMIT,
        filters: dict[str, Any] | None = None,
        reviews: dict[str, dict[str, Any]] | None = None,
    ) -> list[tuple[Path, dict[str, Any]]]:
        _validate_limit(limit)
        selected: list[tuple[Path, dict[str, Any]]] = []
        for path in self._paths():
            try:
                value = self._read(path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                continue
            created = _parse_timestamp(value.get("created_at"))
            review = (reviews or {}).get(value.get("feedback_id"))
            if created is not None and created < cutoff and _matches(value, filters or {}, review):
                selected.append((path, value))
                if len(selected) >= limit:
                    break
        return selected

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

    def _filters(
        self,
        *,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        source: str | None = None,
        remote: str | None = None,
        project: str | None = None,
        project_dir: str | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
    ) -> dict[str, Any]:
        if category is not None and (not isinstance(category, str) or category not in CATEGORIES):
            raise FeedbackError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
        if severity is not None and (not isinstance(severity, str) or severity not in SEVERITIES):
            raise FeedbackError(f"severity must be one of: {', '.join(sorted(SEVERITIES))}")
        if status is not None and (not isinstance(status, str) or status not in REVIEW_STATUSES):
            raise FeedbackError(f"status must be one of: {', '.join(sorted(REVIEW_STATUSES))}")
        values = {
            "category": _filter_value(category, "category"),
            "severity": _filter_value(severity, "severity"),
            "status": _filter_value(status, "status"),
            "source": _filter_value(source, "source"),
            "remote": _filter_value(remote, "remote"),
        }
        if project_dir is not None:
            context = _project_context(project_dir)
            project = context["identity"] if context else project
        values["project"] = _filter_value(project, "project")
        for key, value in (("since", since), ("until", until)):
            if value is None:
                values[key] = None
                continue
            if isinstance(value, datetime):
                parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            elif isinstance(value, str):
                parsed = _parse_timestamp(value.strip())
                if parsed is None:
                    raise FeedbackError(f"{key} filter must be an ISO-8601 UTC timestamp")
            else:
                raise FeedbackError(f"{key} filter is invalid")
            values[key] = parsed.astimezone(timezone.utc)
        return values

    @staticmethod
    def _public_filters(filters: dict[str, Any]) -> dict[str, Any]:
        """Return the normalized filter receipt using JSON-safe values.

        Date filters are normalized to UTC ``datetime`` objects internally so
        comparisons cannot mix naive timestamps or offsets.  Those internal
        values must never escape into the CLI/MCP response envelope: Python's
        JSON encoder cannot serialize them and a successful bounded query would
        otherwise fail only at presentation time.
        """
        output: dict[str, Any] = {}
        for key, value in filters.items():
            if value is None:
                continue
            output[key] = _timestamp(value) if isinstance(value, datetime) else value
        return output

    @staticmethod
    def _export_record(
        record: dict[str, Any],
        *,
        review: dict[str, Any] | None = None,
        embedded_review: bool = False,
    ) -> dict[str, Any]:
        """Project a record into a bounded, path-free export representation."""
        output: dict[str, Any] = {"schema_version": 1}
        for key, maximum in (("feedback_id", 32), ("created_at", 40), ("category", 20),
                             ("severity", 20), ("source", 80), ("summary", 200),
                             ("details", 8000), ("reference", 300), ("remote", 64)):
            value = record.get(key)
            if value is None and key == "remote":
                output[key] = None
                continue
            if not isinstance(value, str):
                continue
            try:
                clean, _ = _sanitize_text(value, key, maximum=maximum, required=False)
            except FeedbackError:
                continue
            output[key] = clean
        project = record.get("project")
        if isinstance(project, dict):
            identity = project.get("identity")
            name = _safe_project_name(project.get("name"))
            safe_project: dict[str, str] = {}
            if isinstance(identity, str) and re.fullmatch(r"[a-f0-9]{64}", identity):
                safe_project["identity"] = identity
            if name is not None:
                safe_project["name"] = name
            if safe_project:
                output["project"] = safe_project
        output["redacted"] = bool(record.get("redacted"))
        output["trust"] = "untrusted_data"
        if review is None and embedded_review:
            candidate_status = record.get("status")
            candidate_closure = record.get("closure")
            if candidate_status in REVIEW_STATUSES and isinstance(candidate_closure, dict):
                review = {
                    "schema_version": 1,
                    "feedback_id": record.get("feedback_id"),
                    "status": candidate_status,
                    "closure": candidate_closure,
                }
        status, closure = _public_review(review)
        output["status"] = status
        output["closure"] = closure
        return output

    @staticmethod
    def _display_record(
        record: dict[str, Any],
        *,
        review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return only supported fields; never pass through legacy keys."""
        output = FeedbackService._export_record(record, review=review)
        # Preserve the established record shape for records without project
        # context while keeping the value strictly bounded to ``None``.
        output.setdefault("project", None)
        return output

    def submit(
        self,
        summary: str,
        *,
        details: str = "",
        category: str = "other",
        severity: str = "medium",
        source: str = "agent",
        project_dir: str | None = None,
        project_name: str | None = None,
        remote: str | None = None,
        reference: str = "",
    ) -> dict[str, Any]:
        try:
            if not isinstance(category, str) or category not in CATEGORIES:
                raise FeedbackError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
            if not isinstance(severity, str) or severity not in SEVERITIES:
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
                "project": _project_context(project_dir, project_name),
                "redacted": summary_redacted or details_redacted or reference_redacted,
                "trust": "untrusted_data",
            }
            self.store.save(record)
            return self._result(
                True, "submit", "recorded",
                data={"feedback": self._display_record(record)},
            )
        except FeedbackError as exc:
            return self._result(False, "submit", "refused", error=exc)
        except OSError:
            return self._result(
                False, "submit", "failed",
                error=FeedbackError("feedback log is unavailable", "feedback_unavailable"),
            )

    def list(
        self,
        limit: int = 20,
        cursor: str | None = None,
        *,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        source: str | None = None,
        remote: str | None = None,
        project: str | None = None,
        project_dir: str | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
    ) -> dict[str, Any]:
        try:
            _validate_limit(limit)
            filters = self._filters(
                category=category, severity=severity, status=status, source=source, remote=remote,
                project=project, project_dir=project_dir, since=since, until=until,
            )
            reviews, invalid_reviews = self.store.latest_reviews()
            page = self.store.query(limit, cursor=cursor, filters=filters, reviews=reviews)
            return self._result(True, "list", "complete", data={
                "feedback": [
                    self._display_record(
                        record, review=reviews.get(record.get("feedback_id")),
                    )
                    for record in page["records"]
                ],
                "count": len(page["records"]),
                "invalid_record_count": page["invalid_record_count"],
                "invalid_review_count": invalid_reviews,
                "cursor": cursor,
                "next_cursor": page["next_cursor"],
                "has_more": page["has_more"],
                "filters": self._public_filters(filters),
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "list", "failed", error=exc)

    def show(self, feedback_id: str) -> dict[str, Any]:
        try:
            record = self.store.resolve(feedback_id)
            if record is None:
                raise FeedbackError("feedback record was not found", "feedback_not_found")
            reviews, invalid_reviews = self.store.latest_reviews()
            return self._result(True, "show", "complete", data={
                "feedback": self._display_record(
                    record, review=reviews.get(record.get("feedback_id")),
                ),
                "invalid_review_count": invalid_reviews,
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "show", "failed", error=exc)

    def detail(self, feedback_id: str) -> dict[str, Any]:
        result = self.show(feedback_id)
        result["action"] = "detail"
        return result

    def review(
        self,
        feedback_id: str,
        *,
        status: str,
        reviewer: str = "codex",
        reason: str,
        evidence: list[str] | None = None,
        confidence: str = "medium",
        duplicate_of: str | None = None,
        reviewed_at: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Append a closure decision for an existing report."""
        try:
            record = self.store.resolve(feedback_id)
            if record is None:
                raise FeedbackError("feedback record was not found", "feedback_not_found")
            if not isinstance(status, str) or status not in REVIEW_STATUSES:
                raise FeedbackError(
                    f"status must be one of: {', '.join(sorted(REVIEW_STATUSES))}",
                )
            if not isinstance(reviewer, str) or not _SAFE_REVIEWER.fullmatch(reviewer.strip()):
                raise FeedbackError("reviewer contains unsupported characters")
            clean_reason, _ = _sanitize_text(
                reason, "reason", maximum=MAX_REVIEW_REASON, required=True,
            )
            if evidence is None:
                evidence = []
            if not isinstance(evidence, list) or len(evidence) > MAX_REVIEW_EVIDENCE_ITEMS:
                raise FeedbackError(
                    f"evidence must contain at most {MAX_REVIEW_EVIDENCE_ITEMS} items",
                )
            clean_evidence: list[str] = []
            for item in evidence:
                clean_item, _ = _sanitize_text(
                    item, "evidence", maximum=MAX_REVIEW_EVIDENCE, required=True,
                )
                clean_evidence.append(clean_item)
            if not isinstance(confidence, str) or confidence not in REVIEW_CONFIDENCES:
                raise FeedbackError(
                    f"confidence must be one of: {', '.join(sorted(REVIEW_CONFIDENCES))}",
                )
            if reviewed_at is None:
                reviewed_value = _timestamp(self.clock())
            elif isinstance(reviewed_at, datetime):
                reviewed_value = _timestamp(reviewed_at)
            elif isinstance(reviewed_at, str):
                parsed = _parse_timestamp(reviewed_at.strip())
                if parsed is None:
                    raise FeedbackError("reviewed_at must be an ISO-8601 UTC timestamp")
                reviewed_value = _timestamp(parsed)
            else:
                raise FeedbackError("reviewed_at is invalid")
            canonical_duplicate: str | None = None
            if duplicate_of is not None:
                duplicate = self.store.resolve(duplicate_of)
                if duplicate is None:
                    raise FeedbackError("duplicate feedback record was not found", "feedback_not_found")
                canonical_duplicate = duplicate.get("feedback_id")
                if canonical_duplicate == record.get("feedback_id"):
                    raise FeedbackError("a feedback record cannot duplicate itself")
            closure: dict[str, Any] = {
                "reviewed_at": reviewed_value,
                "reviewer": reviewer.strip(),
                "reason": clean_reason,
                "evidence": clean_evidence,
                "confidence": confidence,
            }
            if canonical_duplicate is not None:
                closure["duplicate_of"] = canonical_duplicate
            event = self.store.append_review({
                "schema_version": 1,
                "feedback_id": record["feedback_id"],
                "status": status,
                "closure": closure,
            })
            return self._result(True, "review", "updated", data={
                "feedback": self._display_record(record, review=event),
                "review": event,
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "review", "failed", error=exc)
        except OSError:
            return self._result(
                False, "review", "failed",
                error=FeedbackError("feedback log is unavailable", "feedback_unavailable"),
            )

    def counts(
        self,
        *,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        source: str | None = None,
        remote: str | None = None,
        project: str | None = None,
        project_dir: str | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Return global status counts over the valid retained feedback set."""
        try:
            filters = self._filters(
                category=category, severity=severity, status=status, source=source,
                remote=remote, project=project, project_dir=project_dir,
                since=since, until=until,
            )
            reviews, invalid_reviews = self.store.latest_reviews()
            records, invalid_records = self.store.records()
            by_status = {name: 0 for name in sorted(REVIEW_STATUSES)}
            by_category = {name: 0 for name in sorted(CATEGORIES)}
            by_severity = {name: 0 for name in sorted(SEVERITIES)}
            matched = 0
            for record in records:
                review = reviews.get(record.get("feedback_id"))
                if not _matches(record, filters, review):
                    continue
                matched += 1
                actual_status = review.get("status") if isinstance(review, dict) else "unreviewed"
                by_status[actual_status] = by_status.get(actual_status, 0) + 1
                if record.get("category") in by_category:
                    by_category[record["category"]] += 1
                if record.get("severity") in by_severity:
                    by_severity[record["severity"]] += 1
            reviewed = matched - by_status.get("unreviewed", 0)
            return self._result(True, "counts", "complete", data={
                "count": matched,
                "total": len(records),
                "reviewed": reviewed,
                "unreviewed": by_status.get("unreviewed", 0),
                "by_status": by_status,
                "by_category": by_category,
                "by_severity": by_severity,
                "invalid_record_count": invalid_records,
                "invalid_review_count": invalid_reviews,
                "filters": self._public_filters(filters),
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "counts", "failed", error=exc)

    def export(
        self,
        limit: int = MAX_LIMIT,
        cursor: str | None = None,
        *,
        format: str = "json",
        max_bytes: int = MAX_EXPORT_BYTES,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        source: str | None = None,
        remote: str | None = None,
        project: str | None = None,
        project_dir: str | None = None,
        since: str | datetime | None = None,
        until: str | datetime | None = None,
    ) -> dict[str, Any]:
        try:
            _validate_limit(limit)
            if format not in {"json", "jsonl"}:
                raise FeedbackError("format must be json or jsonl")
            if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_EXPORT_BYTES:
                raise FeedbackError(f"max_bytes must be between 1 and {MAX_EXPORT_BYTES}")
            page_result = self.list(
                limit, cursor, category=category, severity=severity, status=status, source=source,
                remote=remote, project=project, project_dir=project_dir,
                since=since, until=until,
            )
            if not page_result["ok"]:
                page_result["action"] = "export"
                return page_result
            page = page_result["data"]
            records = [
                self._export_record(record, embedded_review=True)
                for record in page["feedback"]
            ]
            if format == "jsonl":
                content = "".join(
                    json.dumps(
                        record, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":"),
                    ) + "\n"
                    for record in records
                )
            else:
                content = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            encoded_size = len(content.encode("utf-8"))
            if encoded_size > max_bytes:
                raise FeedbackError("export exceeds max_bytes", "export_too_large")
            return self._result(True, "export", "complete", data={
                "feedback": records,
                "records": records,
                "content": content,
                "format": format,
                "bytes": encoded_size,
                "count": len(records),
                "invalid_record_count": page["invalid_record_count"],
                "cursor": page["cursor"],
                "next_cursor": page["next_cursor"],
                "has_more": page["has_more"],
                "bounded": True,
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "export", "failed", error=exc)

    def retention(
        self,
        *,
        retention_days: int = 30,
        limit: int = MAX_LIMIT,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        project: str | None = None,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        try:
            if (
                isinstance(retention_days, bool)
                or not isinstance(retention_days, int)
                or not 0 <= retention_days <= 3650
            ):
                raise FeedbackError("retention_days must be between 0 and 3650")
            _validate_limit(limit)
            filters = self._filters(
                category=category, severity=severity, status=status,
                project=project, project_dir=project_dir,
            )
            now = self.clock()
            if now.tzinfo is None:
                raise FeedbackError("feedback clock must return a timezone-aware timestamp")
            cutoff = now.astimezone(timezone.utc) - timedelta(days=retention_days)
            reviews, invalid_reviews = self.store.latest_reviews()
            candidates = [
                self._export_record(
                    record, review=reviews.get(record.get("feedback_id")),
                )
                for _path, record in self.store.candidates(
                    cutoff, limit=limit, filters=filters, reviews=reviews,
                )
            ]
            return self._result(True, "retention", "planned", data={
                "retention_days": retention_days,
                "cutoff": _timestamp(cutoff),
                "candidates": candidates,
                "count": len(candidates),
                "invalid_review_count": invalid_reviews,
                "deletion": "disabled_by_default",
                "requires_confirmation": True,
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "retention", "failed", error=exc)

    def retention_plan(self, **kwargs: Any) -> dict[str, Any]:
        return self.retention(**kwargs)

    def prune(
        self,
        *,
        retention_days: int = 30,
        limit: int = MAX_LIMIT,
        confirm: bool = False,
        category: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        project: str | None = None,
        project_dir: str | None = None,
    ) -> dict[str, Any]:
        """Return a non-mutating candidate plan; originals are append-only."""
        try:
            if not isinstance(confirm, bool):
                raise FeedbackError("confirm must be a boolean")
            if (
                isinstance(retention_days, bool)
                or not isinstance(retention_days, int)
                or not 0 <= retention_days <= 3650
            ):
                raise FeedbackError("retention_days must be between 0 and 3650")
            _validate_limit(limit)
            filters = self._filters(
                category=category, severity=severity, status=status,
                project=project, project_dir=project_dir,
            )
            now = self.clock()
            if now.tzinfo is None:
                raise FeedbackError("feedback clock must return a timezone-aware timestamp")
            cutoff = now.astimezone(timezone.utc) - timedelta(days=retention_days)
            reviews, invalid_reviews = self.store.latest_reviews()
            candidates = list(self.store.candidates(
                cutoff, limit=limit, filters=filters, reviews=reviews,
            ))
            return self._result(True, "prune", "planned", data={
                "retention_days": retention_days,
                "cutoff": _timestamp(cutoff),
                "candidates": [
                    self._export_record(
                        record, review=reviews.get(record.get("feedback_id")),
                    )
                    for _path, record in candidates
                ],
                "count": len(candidates),
                "deleted": 0,
                "requested_confirmation": bool(confirm),
                "requires_confirmation": False,
                "deletion": "disabled_append_only",
                "invalid_review_count": invalid_reviews,
                "trust": "untrusted_data",
            })
        except FeedbackError as exc:
            return self._result(False, "prune", "failed", error=exc)
