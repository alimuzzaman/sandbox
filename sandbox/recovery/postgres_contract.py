"""Closed source bindings for owner-scoped PostgreSQL recovery."""
from dataclasses import dataclass
import hashlib
import json
import re

from .errors import RecoveryError


RESTORE_INSPECTION_DIAGNOSTIC_FIELDS = frozenset({
    "schema_version", "correlation_id", "source_digest", "archive_digest",
    "local_runtime_revision", "installed_runtime_revision",
    "runtime_revision_state", "phase", "status", "code",
})
RESTORE_INSPECTION_PHASES = frozenset({
    "request_validation", "archive_validation", "retained_request",
    "request_binding", "runtime_compatibility", "target_identity",
    "target_state", "database_probe", "database_observation",
    "schema_comparison", "transport", "response_validation", "complete",
})
RESTORE_INSPECTION_STATUSES = frozenset({
    "complete", "refused", "unavailable", "unknown",
})
RESTORE_INSPECTION_CODES = frozenset({
    "restore_inspected", "restore_target_stopped", "restore_target_changed",
    "restore_target_busy", "restore_target_unavailable", "restore_data_invalid",
    "restore_database_unavailable", "schema_compared", "schema_diagnostic_unavailable",
    "schema_evidence_invalid", "schema_reference_changed",
    "schema_reference_cleanup_failed", "schema_reference_source_unavailable",
    "schema_reference_pending", "schema_reference_unavailable",
    "source_schema_changed", "target_schema_changed", "restore_verification_failed",
    "request_invalid", "request_identity_mismatch", "request_binding_mismatch",
    "operation_binding_mismatch", "source_binding_mismatch", "archive_binding_mismatch",
    "target_binding_mismatch", "acceptance_unknown", "inspection_busy",
    "inspection_failed", "retained_request_missing", "retained_request_unavailable",
    "inspection_response_invalid", "inspection_response_oversized",
    "remote_revision_mismatch", "remote_unavailable", "remote_authentication_unavailable",
    "remote_status_unavailable", "archive_changed", "source_changed", "path_unsafe",
    "reopen_plan_changed", "reopen_pending", "reopen_history_invalid",
})
RESTORE_INSPECTION_REVISION_STATES = frozenset({
    "match", "mismatch", "unavailable", "unknown",
})


def valid_restore_inspection_diagnostic(value, *, correlation_id, source_digest,
                                        archive_digest):
    """Accept only the closed, value-free inspection diagnostic projection."""
    if (type(value) is not dict
            or set(value) != RESTORE_INSPECTION_DIAGNOSTIC_FIELDS
            or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1
            or value.get("correlation_id") != correlation_id
            or value.get("source_digest") != source_digest
            or value.get("archive_digest") != archive_digest
            or any(type(value.get(field)) is not str for field in (
                "runtime_revision_state", "phase", "status", "code"))
            or value.get("phase") not in RESTORE_INSPECTION_PHASES
            or value.get("status") not in RESTORE_INSPECTION_STATUSES
            or value.get("code") not in RESTORE_INSPECTION_CODES
            or value.get("runtime_revision_state") not in RESTORE_INSPECTION_REVISION_STATES):
        return False
    for field in ("local_runtime_revision", "installed_runtime_revision"):
        revision = value.get(field)
        if revision is not None and (
                type(revision) is not str or not re.fullmatch(r"[a-f0-9]{12,40}", revision)):
            return False
    if (value["runtime_revision_state"] in {"match", "mismatch"}
            and (value["local_runtime_revision"] is None
                 or value["installed_runtime_revision"] is None)):
        return False
    return bool(re.fullmatch(r"[a-f0-9]{64}", correlation_id or "")
                and re.fullmatch(r"sha256:[a-f0-9]{64}", source_digest or "")
                and re.fullmatch(r"sha256:[a-f0-9]{64}", archive_digest or ""))


def restore_inspection_result_matches_diagnostic(result_code, result_ok, diagnostic):
    """Validate top-level outcome against its closed phase/status diagnostic."""
    if (type(result_code) is not str or type(result_ok) is not bool
            or type(diagnostic) is not dict):
        return False
    code_matches = result_code == diagnostic.get("code") or bool(
        result_code == "restore_inspected"
        and diagnostic.get("phase") == "schema_comparison"
        and diagnostic.get("code") in {
            "schema_compared", "schema_diagnostic_unavailable",
            "source_schema_changed", "target_schema_changed",
        }
        and diagnostic.get("status") in {"complete", "unavailable"}
    )
    if not code_matches:
        return False
    status = diagnostic.get("status")
    if status == "complete":
        return result_ok
    if status == "unknown" or status == "refused":
        # A stopped, exact target is a valid read-only refusal result. Other
        # refusals and unknown outcomes are unsuccessful transport results.
        return result_ok is (status == "refused"
                             and diagnostic.get("code") == "restore_target_stopped")
    return status == "unavailable"


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _reference(value):
    from sandbox.isolation.credential_binding import canonical_registered_source_reference
    try:
        return canonical_registered_source_reference(value)
    except (TypeError, ValueError):
        raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid") from None


@dataclass(frozen=True)
class PostgresSource:
    schema_version: int
    profile: str
    remote: str
    compose_project: str
    container_id: str
    volume: str
    database: str
    role: str
    image_id: str
    client_image_id: str
    credential_reference: str | None
    target_password_reference: str | None

    @classmethod
    def from_mapping(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid")
        source = cls(**value)
        if any(type(getattr(source, field)) is not str for field in (
                'profile', 'remote', 'compose_project', 'container_id', 'volume', 'image_id')):
            raise RecoveryError("source binding is invalid", "source_binding_invalid")
        if (type(source.schema_version) is not int or source.schema_version != 1
                or source.profile not in {"lenzora-dev", "lenzora-prod", "lenzora-prod-legacy"}
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", source.remote)
                or not re.fullmatch(r"[a-f0-9]{64}", source.container_id)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", source.image_id)
                or type(source.client_image_id) is not str
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", source.client_image_id)
                or any(type(item) is not str or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", item)
                       for item in (source.compose_project, source.volume, source.database, source.role))):
            raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid")
        if type(source.client_image_id) is not str:
            raise RecoveryError("source binding is invalid", "source_binding_invalid")
        if source.target_password_reference is not None:
            _reference(source.target_password_reference)
            if source.profile != "lenzora-prod-legacy":
                raise RecoveryError("target password applies only to production transfer", "source_binding_invalid")
        if source.credential_reference is not None:
            _reference(source.credential_reference)
            if source.profile != "lenzora-prod-legacy":
                raise RecoveryError("external credential requires the legacy profile", "source_binding_invalid")
        elif source.client_image_id != source.image_id:
            raise RecoveryError("PostgreSQL source binding is invalid", "source_binding_invalid")
        elif source.profile == "lenzora-prod-legacy":
            raise RecoveryError("legacy source requires an approved broker reference", "source_binding_invalid")
        return source

    def as_mapping(self):
        return dict(vars(self))

    @property
    def source_digest(self):
        return digest(self.as_mapping())


@dataclass(frozen=True)
class StorageSource:
    schema_version: int
    profile: str
    remote: str
    compose_project: str
    container_id: str
    volume: str
    image_id: str
    mount_path: str
    credential_reference: None = None

    @classmethod
    def from_mapping(cls, value):
        if type(value) is not dict or set(value) != set(cls.__dataclass_fields__):
            raise RecoveryError('storage source binding is invalid', 'source_binding_invalid')
        source = cls(**value)
        if any(type(getattr(source, field)) is not str for field in (
                'profile', 'remote', 'compose_project', 'container_id', 'volume', 'image_id')):
            raise RecoveryError("source binding is invalid", "source_binding_invalid")
        if (type(source.schema_version) is not int or source.schema_version != 1
                or source.profile != 'lenzora-prod-storage' or source.mount_path != '/app/storage'
                or source.credential_reference is not None
                or not re.fullmatch(r'[a-f0-9]{64}', source.container_id)
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', source.image_id)
                or any(not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item)
                    for item in (source.remote, source.compose_project, source.volume))):
            raise RecoveryError('storage source binding is invalid', 'source_binding_invalid')
        return source

    def as_mapping(self): return dict(vars(self))

    @property
    def source_digest(self): return digest(self.as_mapping())


def recovery_source(value):
    if isinstance(value, dict) and value.get('profile') == 'lenzora-prod-storage':
        return StorageSource.from_mapping(value)
    return PostgresSource.from_mapping(value)
