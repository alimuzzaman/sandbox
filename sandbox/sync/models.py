"""Validated, path-light synchronization values and public envelopes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping

MODES = frozenset({"off", "live", "checkpoint"})
RELATIONSHIP_LIFECYCLES = frozenset({
    "active", "stopped", "conflicted", "refused", "indeterminate", "diverged",
})
GENERATION_LIFECYCLES = frozenset({
    "capturing", "pending", "transferring", "accepted", "refused", "failed", "diverged",
})
PARTICIPANT_ROLES = frozenset({"owner", "participant", "observer"})
SOURCE_ACCESS_MODES = frozenset({"managed_read_only", "isolated_copy"})
JOB_RELEASE_STATES = frozenset({"active", "released", "failed"})
FAILURE_CODES = frozenset({
    "credential_detected", "ownership_conflict", "remote_unavailable",
    "unstable_capture", "divergence", "transport_unknown",
})
FAILURE_STATUSES = frozenset({"refused", "failed", "conflicted", "unknown"})
SUCCESS_STATUSES = frozenset({"accepted", "pending", "stopped", "diverged"})

MAX_IDENTIFIER_BYTES = 160
MAX_REMOTE_NAME_BYTES = 64
MAX_FILE_COUNT = 1_000_000
MAX_BYTE_COUNT = 512 * 1024 * 1024
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_identifier(value: object, label: str = "identifier") -> str:
    if (not isinstance(value, str) or not value or
            len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES or
            not _SAFE_IDENTIFIER.fullmatch(value)):
        raise ValueError(f"{label} is invalid")
    return value


def validate_safe_name(value: object, label: str = "name") -> str:
    if (not isinstance(value, str) or not value or
            len(value.encode("utf-8")) > MAX_REMOTE_NAME_BYTES or
            not _SAFE_NAME.fullmatch(value)):
        raise ValueError(f"{label} is invalid")
    return value


def validate_timestamp(value: object, label: str = "timestamp") -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_count(value: object, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{label} is outside the supported bound")
    return value


def _optional_identifier(value: object, label: str) -> str | None:
    return None if value is None else validate_identifier(value, label)


def _optional_digest(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _required_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _optional_commit(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _FULL_SHA.fullmatch(value):
        raise ValueError("commit is invalid")
    return value


@dataclass(frozen=True)
class SynchronizationRelationship:
    relationship_id: str
    project_identity: str
    remote_name: str
    workspace_id: str
    mode: str = "off"
    lifecycle: str = "stopped"
    owner_generation: int = 0
    accepted_generation_id: str | None = None
    pending_generation_id: str | None = None
    updated_at: str = "1970-01-01T00:00:00Z"

    def __post_init__(self) -> None:
        validate_identifier(self.relationship_id, "relationship id")
        validate_identifier(self.project_identity, "project identity")
        validate_safe_name(self.remote_name, "remote name")
        validate_identifier(self.workspace_id, "workspace id")
        if self.mode not in MODES:
            raise ValueError("synchronization mode is invalid")
        if self.lifecycle not in RELATIONSHIP_LIFECYCLES:
            raise ValueError("relationship lifecycle is invalid")
        validate_count(self.owner_generation, "owner generation", maximum=2**63 - 1)
        _optional_identifier(self.accepted_generation_id, "accepted generation id")
        _optional_identifier(self.pending_generation_id, "pending generation id")
        object.__setattr__(self, "updated_at", validate_timestamp(self.updated_at, "updated at"))

    @property
    def ownership_key(self) -> tuple[str, str, str]:
        return self.project_identity, self.remote_name, self.workspace_id

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SynchronizationRelationship":
        return cls(**dict(value))


@dataclass(frozen=True)
class SourceGeneration:
    generation_id: str
    relationship_id: str
    sequence: int
    manifest_digest: str
    file_count: int
    byte_count: int
    lifecycle: str
    request_id: str
    commit: str | None = None
    dirty_digest: str | None = None
    refusal_code: str | None = None
    created_at: str = "1970-01-01T00:00:00Z"
    accepted_at: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.generation_id, "generation id")
        validate_identifier(self.relationship_id, "relationship id")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 1:
            raise ValueError("generation sequence must be positive")
        _required_digest(self.manifest_digest, "manifest digest")
        validate_count(self.file_count, "file count", maximum=MAX_FILE_COUNT)
        validate_count(self.byte_count, "byte count", maximum=MAX_BYTE_COUNT)
        if self.lifecycle not in GENERATION_LIFECYCLES:
            raise ValueError("generation lifecycle is invalid")
        validate_identifier(self.request_id, "request id")
        _optional_commit(self.commit)
        _optional_digest(self.dirty_digest, "dirty digest")
        if self.refusal_code is not None:
            validate_safe_name(self.refusal_code, "refusal code")
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created at"))
        if self.accepted_at is not None:
            object.__setattr__(self, "accepted_at", validate_timestamp(self.accepted_at, "accepted at"))
        if self.lifecycle == "accepted" and self.accepted_at is None:
            raise ValueError("accepted generation requires accepted_at")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SourceGeneration":
        return cls(**dict(value))


@dataclass(frozen=True)
class Participant:
    participant_id: str
    relationship_id: str
    last_seen_at: str
    role: str = "participant"

    def __post_init__(self) -> None:
        validate_identifier(self.participant_id, "participant id")
        validate_identifier(self.relationship_id, "relationship id")
        if self.role not in PARTICIPANT_ROLES:
            raise ValueError("participant role is invalid")
        object.__setattr__(self, "last_seen_at", validate_timestamp(self.last_seen_at, "last seen at"))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Participant":
        return cls(**dict(value))


@dataclass(frozen=True)
class PinnedJob:
    job_id: str
    relationship_id: str
    generation_id: str
    source_access: str = "managed_read_only"
    parallel_safe: bool = False
    release_state: str = "active"

    def __post_init__(self) -> None:
        validate_identifier(self.job_id, "job id")
        validate_identifier(self.relationship_id, "relationship id")
        validate_identifier(self.generation_id, "generation id")
        if self.source_access not in SOURCE_ACCESS_MODES:
            raise ValueError("source access is invalid")
        if not isinstance(self.parallel_safe, bool):
            raise ValueError("parallel_safe must be boolean")
        if self.release_state not in JOB_RELEASE_STATES:
            raise ValueError("job release state is invalid")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PinnedJob":
        return cls(**dict(value))


@dataclass(frozen=True)
class DivergenceRecord:
    relationship_id: str
    affected_count: int
    comparison_generation_id: str
    detected_at: str
    resolution_code: str

    def __post_init__(self) -> None:
        validate_identifier(self.relationship_id, "relationship id")
        validate_count(self.affected_count, "affected count", maximum=MAX_FILE_COUNT)
        validate_identifier(self.comparison_generation_id, "comparison generation id")
        object.__setattr__(self, "detected_at", validate_timestamp(self.detected_at, "detected at"))
        validate_safe_name(self.resolution_code, "resolution code")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DivergenceRecord":
        return cls(**dict(value))


def success_envelope(
    relationship: SynchronizationRelationship,
    generation: SourceGeneration | None = None,
    *,
    status: str | None = None,
    active_generation: str | None = None,
) -> dict[str, Any]:
    """Return only the bounded, path-free success contract fields."""
    chosen = status or (generation.lifecycle if generation else relationship.lifecycle)
    if chosen not in SUCCESS_STATUSES:
        raise ValueError("success status is invalid")
    if active_generation is not None:
        validate_identifier(active_generation, "active generation")
    generation_value = None
    if generation is not None:
        public_state = "pending" if generation.lifecycle in {"capturing", "transferring"} else generation.lifecycle
        generation_value = {
            "id": generation.generation_id,
            "sequence": generation.sequence,
            "state": public_state,
            "commit": generation.commit,
            "file_count": generation.file_count,
            "byte_count": generation.byte_count,
        }
    return {
        "ok": True,
        "status": chosen,
        "relationship": {
            "id": relationship.relationship_id,
            "mode": relationship.mode,
            "lifecycle": relationship.lifecycle,
            "project_identity": relationship.project_identity,
            "remote": relationship.remote_name,
            "workspace_id": relationship.workspace_id,
        },
        "generation": generation_value,
        "job": {"active_generation": active_generation},
        "error": None,
    }


_FAILURE_MESSAGES = {
    "credential_detected": "Credential-like input was refused before remote mutation.",
    "ownership_conflict": "The selected workspace is owned by a different project identity.",
    "remote_unavailable": "The selected remote is unavailable; the generation remains pending.",
    "unstable_capture": "Source changed during bounded capture; retry after edits settle.",
    "divergence": "Managed remote source diverged and requires explicit resolution.",
    "transport_unknown": "Transfer acknowledgment is unknown; reconcile with the same request ID.",
}


def failure_envelope(
    *,
    code: str,
    status: str,
    relationship_id: str,
    remote_name: str,
    request_id: str,
    accepted_generation: str | None = None,
    pending_generation: str | None = None,
    retryable: bool = False,
    message: str | None = None,
) -> dict[str, Any]:
    """Build a fail-closed failure envelope without accepting raw diagnostics."""
    if code not in FAILURE_CODES:
        raise ValueError("failure code is invalid")
    if status not in FAILURE_STATUSES:
        raise ValueError("failure status is invalid")
    validate_identifier(relationship_id, "relationship id")
    validate_safe_name(remote_name, "remote name")
    validate_identifier(request_id, "request id")
    _optional_identifier(accepted_generation, "accepted generation")
    _optional_identifier(pending_generation, "pending generation")
    if not isinstance(retryable, bool):
        raise ValueError("retryable must be boolean")
    # Raw diagnostics can contain paths even after credential redaction. Public
    # failure guidance therefore comes from a fixed code-owned catalog only.
    _ = message
    safe_message = _FAILURE_MESSAGES[code]
    return {
        "ok": False,
        "status": status,
        "code": code,
        "message": safe_message,
        "relationship": {"id": relationship_id, "remote": remote_name},
        "request_id": request_id,
        "accepted_generation": accepted_generation,
        "pending_generation": pending_generation,
        "retryable": retryable,
    }


def validate_sync_envelope(value: object) -> dict[str, Any]:
    """Validate a public sync envelope and reject unknown/unbounded fields."""
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise ValueError("sync envelope is malformed")
    if value["ok"]:
        expected = {"ok", "status", "relationship", "generation", "job", "error"}
        if set(value) != expected or value["error"] is not None:
            raise ValueError("success envelope is malformed")
        relationship = value.get("relationship")
        if not isinstance(relationship, dict) or set(relationship) != {
            "id", "mode", "lifecycle", "project_identity", "remote", "workspace_id",
        }:
            raise ValueError("success relationship is malformed")
        model = SynchronizationRelationship(
            relationship_id=relationship["id"], project_identity=relationship["project_identity"],
            remote_name=relationship["remote"], workspace_id=relationship["workspace_id"],
            mode=relationship["mode"], lifecycle=relationship["lifecycle"],
        )
        generation = value.get("generation")
        if generation is not None:
            if not isinstance(generation, dict) or set(generation) != {
                "id", "sequence", "state", "commit", "file_count", "byte_count",
            }:
                raise ValueError("success generation is malformed")
            SourceGeneration(
                generation_id=generation["id"], relationship_id=model.relationship_id,
                sequence=generation["sequence"], manifest_digest="0" * 64,
                file_count=generation["file_count"], byte_count=generation["byte_count"],
                lifecycle=generation["state"], request_id="validation",
                commit=generation["commit"],
                accepted_at=(utc_now() if generation["state"] == "accepted" else None),
            )
            if value.get("status") in {"accepted", "pending", "diverged"} and (
                    generation["state"] != value.get("status")):
                raise ValueError("success generation state does not match status")
        job = value.get("job")
        if not isinstance(job, dict) or set(job) != {"active_generation"}:
            raise ValueError("success job is malformed")
        _optional_identifier(job["active_generation"], "active generation")
        if value.get("status") not in SUCCESS_STATUSES:
            raise ValueError("success status is invalid")
    else:
        expected = {
            "ok", "status", "code", "message", "relationship", "request_id",
            "accepted_generation", "pending_generation", "retryable",
        }
        if set(value) != expected:
            raise ValueError("failure envelope is malformed")
        rebuilt = failure_envelope(
            code=value["code"], status=value["status"],
            relationship_id=value["relationship"]["id"],
            remote_name=value["relationship"]["remote"], request_id=value["request_id"],
            accepted_generation=value["accepted_generation"],
            pending_generation=value["pending_generation"], retryable=value["retryable"],
        )
        if rebuilt["message"] != value["message"]:
            raise ValueError("failure message is not safely redacted")
    return dict(value)
