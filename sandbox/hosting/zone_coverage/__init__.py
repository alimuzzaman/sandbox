"""Zone coverage for wildcard subdomain hosting (spec 070)."""
from __future__ import annotations


class CoverageError(RuntimeError):
    """A finite, actionable zone-coverage refusal."""

    def __init__(self, code: str, message: str, *, reason: str | None = None, remedy: str | None = None):
        super().__init__(f"{code}: {message}" if not message.startswith(f"{code}:") else message)
        self.code = code
        self.message = message
        self.reason = reason
        self.remedy = remedy


# Refusal codes from contracts/cli.md and contracts/zone-store.md
COVERAGE_OPERATION_ACTIVE = "coverage_operation_active"
COVERAGE_RECORD_MISSING = "coverage_record_missing"
COVERAGE_PLAN_STALE = "coverage_plan_stale"
COVERAGE_CONFLICT = "coverage_conflict"
COVERAGE_ZONE_UNAVAILABLE = "coverage_zone_unavailable"
COVERAGE_SSL_NOT_STRICT = "coverage_ssl_not_strict"
COVERAGE_RECORD_EXISTS = "coverage_record_exists"
COVERAGE_DEPENDENTS_UNKNOWN = "coverage_dependents_unknown"
COVERAGE_FALLBACK_UNVERIFIED = "coverage_fallback_unverified"
COVERAGE_SCHEMA_UNSUPPORTED = "coverage_schema_unsupported"
COVERAGE_NOT_ACTIVE = "coverage_not_active"
COVERAGE_DRIFT = "coverage_drift"
COVERAGE_NAME_INELIGIBLE = "coverage_name_ineligible"
COVERAGE_DISABLED_DEPENDENT = "coverage_disabled_dependent"
COVERAGE_IMAGE_UNSUPPORTED = "coverage_image_unsupported"
MANAGED_RECORD_DRIFT = "managed_record_drift"
RECORD_NOT_OWNED = "record_not_owned"
COVERAGE_STORE_UNAVAILABLE = "coverage_store_unavailable"

# Store-level refusals
COVERAGE_GENERATION_STALE = "coverage_generation_stale"
COVERAGE_DEPENDENTS_STALE = "coverage_dependents_stale"
COVERAGE_DOCUMENT_CORRUPT = "coverage_document_corrupt"
COVERAGE_KIND_MISMATCH = "coverage_kind_mismatch"
COVERAGE_LEASE_ACTIVE = "coverage_lease_active"

REFUSAL_CODES = (
    COVERAGE_OPERATION_ACTIVE,
    COVERAGE_RECORD_MISSING,
    COVERAGE_PLAN_STALE,
    COVERAGE_CONFLICT,
    COVERAGE_ZONE_UNAVAILABLE,
    COVERAGE_SSL_NOT_STRICT,
    COVERAGE_RECORD_EXISTS,
    COVERAGE_DEPENDENTS_UNKNOWN,
    COVERAGE_FALLBACK_UNVERIFIED,
    COVERAGE_SCHEMA_UNSUPPORTED,
    COVERAGE_NOT_ACTIVE,
    COVERAGE_DRIFT,
    COVERAGE_NAME_INELIGIBLE,
    COVERAGE_DISABLED_DEPENDENT,
    COVERAGE_IMAGE_UNSUPPORTED,
    MANAGED_RECORD_DRIFT,
    RECORD_NOT_OWNED,
    COVERAGE_STORE_UNAVAILABLE,
)
