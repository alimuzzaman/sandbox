"""Strict, bounded public and persisted recovery values."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


SCHEMA_VERSION = 1
MAX_ID_BYTES = 160
MAX_PHASES = 64
MAX_SERVICES = 16
MAX_IMAGES = 32
MAX_RECEIPT_BYTES = 128 * 1024
MAX_EDGE_ROUTES = 64
MAX_EDGE_RECORDS = 128
MAX_CERTIFICATE_HOSTNAMES = 64
MAX_EDGE_INTENT_BYTES = 64 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RecoveryAction(str, Enum):
    OBSERVE_RECONCILE = "observe_reconcile"
    CONTINUE_EDGE = "continue_edge"


RESULT_CLASSES = frozenset({
    "observation_reconciled", "already_reconciled", "edge_only_completed",
    "legacy_evidence", "job_ineligible", "binding_mismatch", "dirty_source",
    "changed_target", "partial_evidence", "evidence_changed",
    "generation_conflict", "operation_busy", "mutation_required",
    "governance_unavailable", "confirmation_required", "expired_evidence",
    "effect_unknown", "observation_failed", "edge_failed", "persistence_failed",
    "retention_full",
})
RESULT_FAMILIES = frozenset({"success", "refused", "uncertain", "failed"})


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_edge_intent(value: object) -> dict:
    """Validate the canonical bounded non-secret edge authority."""
    if not isinstance(value, dict):
        raise ValueError("edge intent is invalid")
    routes = value.get("routes")
    records = value.get("records")
    hostnames = value.get("certificate_hostnames")
    if (not isinstance(routes, list) or not 1 <= len(routes) <= MAX_EDGE_ROUTES or
            not isinstance(records, list) or len(records) > MAX_EDGE_RECORDS or
            not isinstance(hostnames, list) or
            not 1 <= len(hostnames) <= MAX_CERTIFICATE_HOSTNAMES or
            any(not isinstance(item, str) or not item for item in hostnames) or
            len(hostnames) != len(set(hostnames))):
        raise ValueError("edge intent exceeds its collection bounds")
    safe = json.loads(json.dumps(value, sort_keys=True))
    if len(json.dumps(safe, sort_keys=True, separators=(",", ":")).encode()) > MAX_EDGE_INTENT_BYTES:
        raise ValueError("edge intent exceeds its byte bound")
    return safe


@dataclass(frozen=True)
class TargetIdentity:
    remote: str
    project: str
    environment: str

    def __post_init__(self) -> None:
        _safe_id(self.remote, "remote")
        _safe_id(self.project, "project")
        _safe_id(self.environment, "environment")

    @property
    def key(self) -> str:
        return f"{self.remote}/{self.project}/{self.environment}"

    def as_dict(self) -> dict:
        return {"remote": self.remote, "project": self.project,
                "environment": self.environment}


@dataclass(frozen=True)
class RecoveryRequest:
    action: RecoveryAction
    request_id: str
    job_id: str
    original_request_id: str
    target: TargetIdentity
    expected_generation: int
    observation_request_id: str | None = None
    evidence_id: str | None = None
    confirmed: bool = False

    def __post_init__(self) -> None:
        _safe_id(self.request_id, "recovery request id")
        _safe_id(self.job_id, "job id")
        _safe_id(self.original_request_id, "original request id")
        if isinstance(self.expected_generation, bool) or not isinstance(
                self.expected_generation, int) or self.expected_generation < 0:
            raise ValueError("expected generation must be a non-negative integer")
        if self.request_id == self.original_request_id:
            raise ValueError("recovery request id must differ from the original request id")
        if self.action is RecoveryAction.OBSERVE_RECONCILE:
            if self.confirmed or self.observation_request_id is not None or self.evidence_id is not None:
                raise ValueError("observation recovery cannot include edge confirmation fields")
        else:
            _safe_id(self.observation_request_id, "observation request id")
            _digest(self.evidence_id, "evidence id")
            if self.request_id in {self.original_request_id, self.observation_request_id}:
                raise ValueError("edge request id must be distinct")

    @property
    def effect_scope(self) -> str:
        return "receipt_only" if self.action is RecoveryAction.OBSERVE_RECONCILE else "edge_only"

    def identity_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "action": self.action.value,
            "request_id": self.request_id,
            "job_id": self.job_id,
            "original_request_id": self.original_request_id,
            "target": self.target.as_dict(),
            "expected_generation": self.expected_generation,
            "observation_request_id": self.observation_request_id,
            "evidence_id": self.evidence_id,
            "confirmed": self.confirmed,
            "effect_scope": self.effect_scope,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.identity_dict())


@dataclass(frozen=True)
class RecoveryResult:
    request: RecoveryRequest
    family: str
    result_class: str
    resulting_generation: int
    evidence_id: str | None = None
    phases: tuple[dict, ...] = ()
    evidence_expires_at: int | None = None

    def __post_init__(self) -> None:
        if self.family not in RESULT_FAMILIES:
            raise ValueError("recovery result family is invalid")
        if self.result_class not in RESULT_CLASSES:
            raise ValueError("recovery result class is invalid")
        if isinstance(self.resulting_generation, bool) or not isinstance(
                self.resulting_generation, int) or self.resulting_generation < 0:
            raise ValueError("resulting generation is invalid")
        if self.evidence_id is not None:
            _digest(self.evidence_id, "evidence id")
        if len(self.phases) > MAX_PHASES:
            raise ValueError("recovery phase limit exceeded")
        if (self.evidence_expires_at is not None and
                (isinstance(self.evidence_expires_at, bool) or
                 not isinstance(self.evidence_expires_at, int) or
                 self.evidence_expires_at < 0)):
            raise ValueError("evidence expiry is invalid")

    @property
    def ok(self) -> bool:
        return self.family == "success"

    def as_dict(self) -> dict:
        value = {
            "ok": self.ok,
            "schema_version": SCHEMA_VERSION,
            "action": self.request.action.value,
            "result_family": self.family,
            "result_class": self.result_class,
            "request_id": self.request.request_id,
            "request_digest": self.request.digest,
            "original": {"job_id": self.request.job_id,
                         "request_id": self.request.original_request_id},
            "target": self.request.target.as_dict(),
            "generation": {"expected": self.request.expected_generation,
                           "resulting": self.resulting_generation},
            "effect_scope": self.request.effect_scope,
            "evidence": {"id": self.evidence_id,
                         "complete": self.evidence_id is not None,
                         "expires_at": self.evidence_expires_at},
            "phases": list(self.phases),
        }
        if len(json.dumps(value, sort_keys=True).encode("utf-8")) > MAX_RECEIPT_BYTES:
            raise ValueError("recovery receipt exceeds the persistence bound")
        return value


RecoveryAttempt = RecoveryResult


def secret_binding_identities(values: Mapping[str, str], *, key: bytes,
                              key_version: str) -> dict:
    """Return opaque exact identities. Raw values never enter the result."""
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("secret binding key is unavailable")
    _safe_id(key_version, "secret binding key version")
    if len(values) > 64:
        raise ValueError("secret binding reference limit exceeded")
    bindings = []
    for name in sorted(values):
        _safe_id(name, "secret reference")
        value = values[name]
        if not isinstance(value, str):
            raise ValueError("secret binding value is invalid")
        digest = hmac.new(key, name.encode() + b"\0" + value.encode(), hashlib.sha256).hexdigest()
        bindings.append({"reference": name, "digest": "sha256:" + digest})
    return {"key_version": key_version, "bindings": bindings}


def validate_observation(value: object) -> dict:
    """Validate and canonicalize the safe, already-redacted observation."""
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("observation schema is unsupported")
    phases = value.get("phases")
    services = value.get("services")
    images = value.get("images")
    if not isinstance(phases, list) or len(phases) > MAX_PHASES:
        raise ValueError("observation phases are invalid")
    if not isinstance(services, list) or len(services) > MAX_SERVICES:
        raise ValueError("observation services are invalid")
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError("observation images are invalid")
    for collection, label in ((phases, "phase"), (services, "service"), (images, "image")):
        identities = []
        for item in collection:
            if not isinstance(item, dict):
                raise ValueError(f"observation {label} is invalid")
            identity = item.get(label) or item.get("name")
            if not isinstance(identity, str) or not identity:
                raise ValueError(f"observation {label} identity is invalid")
            identities.append(identity)
        if len(identities) != len(set(identities)):
            raise ValueError(f"observation {label} identities are duplicated")
    safe = json.loads(json.dumps(value, sort_keys=True))
    if len(json.dumps(safe, sort_keys=True).encode()) > MAX_RECEIPT_BYTES:
        raise ValueError("observation exceeds the persistence bound")
    safe["evidence_id"] = canonical_digest({key: item for key, item in safe.items()
                                             if key != "evidence_id"})
    return safe
