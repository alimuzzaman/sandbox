"""Bounded public job history; full execution data stays in job-status/output."""
from __future__ import annotations

import json
import math

from sandbox.services.redaction import redact_structure
from sandbox.jobs.models import validate_job_id


MAX_JOB_PAGE_BYTES = 512 * 1024
SUMMARY_FIELDS = (
    "job_id", "request_id", "parent_job_id", "kind", "project_identity",
    "target_kind", "remote_name", "workspace_id", "workspace_label",
    "workspace_mode", "lifecycle", "health", "queue_reason", "queue_position",
    "execution_profile", "output_profile", "deadline_seconds", "deadline_source",
    "source_identity", "source_commit", "source_dirty_digest", "source_access",
    "sync_relationship_id", "sync_generation_id", "accepted_at", "queued_at",
    "started_at", "finished_at", "updated_at", "exit_code", "termination_reason",
    "output_completeness", "cleanup_state",
)


def compact_job(row: dict) -> dict:
    result = {}
    omitted = []
    for key in SUMMARY_FIELDS:
        if key not in row:
            continue
        value = row[key]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            omitted.append(key)
        elif isinstance(value, float) and not math.isfinite(value):
            omitted.append(key)
        elif isinstance(value, str) and len(value.encode("utf-8")) > 1024:
            omitted.append(key)
        else:
            result[key] = value
    if not isinstance(result.get("job_id"), str) or not result["job_id"]:
        raise ValueError("job list row has no bounded job identity")
    validate_job_id(result["job_id"])
    if omitted:
        result["detail_required"] = omitted
    # Command, submission, environment, results and output are never list fields.
    sanitized = redact_structure(result)
    if not isinstance(sanitized, dict):
        raise ValueError("job list row could not be safely projected")
    return sanitized


def job_page(rows: list[dict], *, limit: int, has_more: bool | None,
             max_bytes: int = MAX_JOB_PAGE_BYTES) -> dict:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("job list limit must be between 1 and 200")
    if has_more is not None and type(has_more) is not bool:
        raise ValueError("job list completeness is invalid")
    selected = []
    used = 0
    for row in rows[:limit]:
        item = compact_job(row)
        size = len(json.dumps(item, sort_keys=True, ensure_ascii=True).encode()) + 2
        # Reserve the complete envelope before accepting a row. Never return a
        # truncated JSON document, or consume a cursor for an omitted record.
        if used + size + 2048 > max_bytes:
            if not selected:
                raise ValueError("job_list_record_too_large")
            has_more = True
            break
        selected.append(item)
        used += size
    if len(selected) < len(rows):
        has_more = True
    next_cursor = selected[-1]["job_id"] if selected and has_more is not False else None
    return {"ok": True, "jobs": selected, "page": {
        "schema_version": 2, "row_format": "summary-v1", "limit": limit,
        "max_bytes": max_bytes, "has_more": has_more,
        "next_cursor": next_cursor,
        "completeness": "unknown" if has_more is None else
                        "bounded" if has_more else "complete",
    }}
