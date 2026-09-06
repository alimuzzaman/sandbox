"""Strict projection redactor and field allowlisting for owned storage authority public evidence."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from sandbox.services.redaction import redact_structure, redact_text

# Strict allowlist of top-level response fields
ALLOWED_TOP_LEVEL_FIELDS: Set[str] = {
    "ok",
    "protocol",
    "operation",
    "operation_id",
    "request_id",
    "status",
    "code",
    "message",
    "preview_id",
    "cleanup_id",
    "object_id",
    "remote_identity",
    "project_identity",
    "inventory_generation",
    "policy_generation",
    "candidate_digest",
    "estimated_reclaimable_bytes",
    "observed_reclaimed_bytes",
    "complete",
    "created_at",
    "expires_at",
    "observed_at",
    "cursor",
    "candidates",
    "objects",
    "object",
    "capability",
    "support_tier",
    "adoptable",
    "service_revision",
    "evidence_id",
    "ordinary_evidence_id",
    "acceptance_state",
    "promotion_id",
    "authority_binding_id",
    "binding_generation",
    "checks",
    "storage_authority",
    "resolver_authority",
    "reason_code",
    "already_active",
    "replay",
}

ALLOWED_CANDIDATE_FIELDS: Set[str] = {
    "object_id",
    "object_kind",
    "lifecycle",
    "decision",
    "reason_code",
    "estimated_bytes",
    "object_evidence_digest",
    "reference_snapshot_digest",
}

ALLOWED_OBJECT_FIELDS: Set[str] = {
    "object_id",
    "kind",
    "lifecycle",
    "known_bytes",
    "created_at",
    "accepted_at",
    "removed_at",
    "status",
    "id",
    "evidence_digest",
}

_HOST_PATH_PATTERN = re.compile(r"/(?:Users|home|var|tmp|run|private|etc)/[A-Za-z0-9_./-]+")


def redact_storage_projection(data: Dict[str, Any]) -> Dict[str, Any]:
    """Filter to strict allowlist, scrub host paths, and run shared redaction service."""
    if not isinstance(data, dict):
        return data

    filtered: Dict[str, Any] = {}
    for k, v in data.items():
        if k not in ALLOWED_TOP_LEVEL_FIELDS:
            continue

        if k == "candidates" and isinstance(v, list):
            filtered[k] = [
                {ck: cv for ck, cv in c.items() if ck in ALLOWED_CANDIDATE_FIELDS}
                for c in v
                if isinstance(c, dict)
            ]
        elif k == "objects" and isinstance(v, list):
            filtered[k] = [
                {ok: ov for ok, ov in o.items() if ok in ALLOWED_OBJECT_FIELDS}
                for o in v
                if isinstance(o, dict)
            ]
        elif k == "object" and isinstance(v, dict):
            filtered[k] = {ok: ov for ok, ov in v.items() if ok in ALLOWED_OBJECT_FIELDS}
        else:
            filtered[k] = v

    # Pass through shared redaction service
    redacted = redact_structure(filtered)

    # Scrub host paths
    return _scrub_paths_and_env(redacted)


def _scrub_paths_and_env(val: Any) -> Any:
    if isinstance(val, str):
        return _HOST_PATH_PATTERN.sub("[REDACTED_PATH]", val)
    elif isinstance(val, dict):
        return {k: _scrub_paths_and_env(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_scrub_paths_and_env(item) for item in val]
    return val


__all__ = [
    "ALLOWED_TOP_LEVEL_FIELDS",
    "ALLOWED_CANDIDATE_FIELDS",
    "ALLOWED_OBJECT_FIELDS",
    "redact_storage_projection",
]
