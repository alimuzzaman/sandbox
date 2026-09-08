"""Closed, digest-bound values for explicit incident settlement.

These values describe an operator decision only.  They contain no runtime
authority and intentionally do not model the settlement terminal result; the
repository/service layer owns that transition and proof-custody handoff.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

from ..staging_models import StagingTarget
from .models import (
    ActivationContractError,
    _closed,
    _digest,
    _integer,
    _text,
    activation_digest,
    canonical_bytes,
)


_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_REVISION40 = re.compile(r"[0-9a-f]{40}\Z")
_OBSERVATION_FIELDS = frozenset({
    "schema_version", "target", "transaction_digest", "generation",
    "runtime_epoch", "container_identities", "preserved_identities",
    "inventory_digest", "process_identities", "quiescent", "observation_digest",
})
_ASSESSMENT_FIELDS = frozenset({
    "schema_version", "target", "transaction_digest", "application_revision",
    "backup_receipt_digests", "compatibility", "assessment_digest",
})
_PLAN_FIELDS = frozenset({
    "schema_version", "request_id", "active_request_id", "active_request_digest",
    "transaction_digest", "target", "generation", "current_generation_digest",
    "observation", "data_assessment", "reason", "plan_digest",
})
_APPROVAL_FIELDS = frozenset({
    "schema_version", "authority_id", "authority_revision", "plan_digest",
    "issued_at", "expires_at", "signature", "approval_digest",
})
_SSH_HEADER = b"-----BEGIN SSH SIGNATURE-----\n"
_SSH_FOOTER = b"-----END SSH SIGNATURE-----"


def _sorted_unique(value: object, pattern: re.Pattern[str], maximum: int) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > maximum:
        raise ActivationContractError()
    if any(type(item) is not str or pattern.fullmatch(item) is None for item in value):
        raise ActivationContractError()
    if tuple(sorted(value)) != value or len(set(value)) != len(value):
        raise ActivationContractError()
    return value


def _mapping_sequence(value: object, pattern: re.Pattern[str], maximum: int) -> tuple[str, ...]:
    if type(value) is not list:
        raise ActivationContractError()
    return _sorted_unique(tuple(value), pattern, maximum)


def _digest_sequence(value: object, maximum: int, *, nonempty: bool = False) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > maximum or (nonempty and not value):
        raise ActivationContractError()
    for item in value:
        _digest(item)
    if tuple(sorted(value)) != value or len(set(value)) != len(value):
        raise ActivationContractError()
    return value


def _digest_mapping_sequence(value: object, maximum: int, *, nonempty: bool = False) -> tuple[str, ...]:
    if type(value) is not list:
        raise ActivationContractError()
    return _digest_sequence(tuple(value), maximum, nonempty=nonempty)


def _target(value: object) -> StagingTarget:
    if type(value) is StagingTarget:
        return value
    return StagingTarget.from_mapping(value)


def _as_target(value: StagingTarget) -> dict[str, str]:
    return value.as_mapping()


@dataclass(frozen=True, slots=True)
class SettlementObservation:
    schema_version: int
    target: StagingTarget
    transaction_digest: str
    generation: int
    runtime_epoch: str
    container_identities: tuple[str, ...]
    preserved_identities: tuple[str, ...]
    inventory_digest: str
    process_identities: tuple[str, ...]
    quiescent: bool
    observation_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 or type(self.target) is not StagingTarget:
            raise ActivationContractError()
        _digest(self.transaction_digest)
        _integer(self.generation)
        _text(self.runtime_epoch, identity=True)
        if self.runtime_epoch != self.target.daemon_identity:
            raise ActivationContractError()
        _sorted_unique(self.container_identities, _HEX64, 128)
        _digest_sequence(self.preserved_identities, 512)
        _digest(self.inventory_digest)
        if type(self.process_identities) is not tuple or self.process_identities:
            raise ActivationContractError()
        if self.quiescent is not True:
            raise ActivationContractError()
        _digest(self.observation_digest)
        if self.observation_digest != activation_digest(
                "sandbox.hosting.images.settlement-observation.v1", self.body_mapping()):
            raise ActivationContractError()

    def body_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target": _as_target(self.target),
            "transaction_digest": self.transaction_digest,
            "generation": self.generation,
            "runtime_epoch": self.runtime_epoch,
            "container_identities": list(self.container_identities),
            "preserved_identities": list(self.preserved_identities),
            "inventory_digest": self.inventory_digest,
            "process_identities": list(self.process_identities),
            "quiescent": self.quiescent,
        }

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "observation_digest": self.observation_digest}

    @classmethod
    def create(cls, *, target: object, transaction_digest: str, generation: int,
               runtime_epoch: str, container_identities: tuple[str, ...],
               preserved_identities: tuple[str, ...], inventory_digest: str,
               process_identities: tuple[str, ...] = (), quiescent: bool = True) -> "SettlementObservation":
        if (type(container_identities) is not tuple
                or type(preserved_identities) is not tuple
                or type(process_identities) is not tuple):
            raise ActivationContractError()
        target_value = _target(target)
        body = {
            "schema_version": 1,
            "target": _as_target(target_value),
            "transaction_digest": transaction_digest,
            "generation": generation,
            "runtime_epoch": runtime_epoch,
            "container_identities": list(container_identities),
            "preserved_identities": list(preserved_identities),
            "inventory_digest": inventory_digest,
            "process_identities": list(process_identities),
            "quiescent": quiescent,
        }
        return cls(1, target_value, transaction_digest, generation, runtime_epoch,
                   tuple(container_identities), tuple(preserved_identities),
                   inventory_digest, tuple(process_identities), quiescent,
                   activation_digest("sandbox.hosting.images.settlement-observation.v1", body))

    @classmethod
    def from_mapping(cls, value: object) -> "SettlementObservation":
        raw = _closed(value, _OBSERVATION_FIELDS)
        if type(raw["process_identities"]) is not list:
            raise ActivationContractError()
        return cls(
            raw["schema_version"], StagingTarget.from_mapping(raw["target"]), raw["transaction_digest"],
            raw["generation"], raw["runtime_epoch"],
            _mapping_sequence(raw["container_identities"], _HEX64, 128),
            _digest_mapping_sequence(raw["preserved_identities"], 512),
            raw["inventory_digest"], tuple(raw["process_identities"]),
            raw["quiescent"], raw["observation_digest"])


@dataclass(frozen=True, slots=True)
class SettlementDataAssessment:
    schema_version: int
    target: StagingTarget
    transaction_digest: str
    application_revision: str
    backup_receipt_digests: tuple[str, ...]
    compatibility: str
    assessment_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 or type(self.target) is not StagingTarget:
            raise ActivationContractError()
        _digest(self.transaction_digest)
        if type(self.application_revision) is not str or _REVISION40.fullmatch(self.application_revision) is None:
            raise ActivationContractError()
        _digest_sequence(self.backup_receipt_digests, 8, nonempty=True)
        if self.compatibility != "forward_initialization_reviewed":
            raise ActivationContractError()
        _digest(self.assessment_digest)
        if self.assessment_digest != activation_digest(
                "sandbox.hosting.images.settlement-data-assessment.v1", self.body_mapping()):
            raise ActivationContractError()

    def body_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target": _as_target(self.target),
            "transaction_digest": self.transaction_digest,
            "application_revision": self.application_revision,
            "backup_receipt_digests": list(self.backup_receipt_digests),
            "compatibility": self.compatibility,
        }

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "assessment_digest": self.assessment_digest}

    @classmethod
    def create(cls, *, target: object, transaction_digest: str,
               application_revision: str, backup_receipt_digests: tuple[str, ...],
               compatibility: str = "forward_initialization_reviewed") -> "SettlementDataAssessment":
        if type(backup_receipt_digests) is not tuple:
            raise ActivationContractError()
        target_value = _target(target)
        body = {
            "schema_version": 1, "target": _as_target(target_value),
            "transaction_digest": transaction_digest,
            "application_revision": application_revision,
            "backup_receipt_digests": list(backup_receipt_digests),
            "compatibility": compatibility,
        }
        return cls(1, target_value, transaction_digest, application_revision,
                   tuple(backup_receipt_digests), compatibility,
                   activation_digest("sandbox.hosting.images.settlement-data-assessment.v1", body))

    @classmethod
    def from_mapping(cls, value: object) -> "SettlementDataAssessment":
        raw = _closed(value, _ASSESSMENT_FIELDS)
        return cls(raw["schema_version"], StagingTarget.from_mapping(raw["target"]), raw["transaction_digest"],
                   raw["application_revision"],
                   _digest_mapping_sequence(raw["backup_receipt_digests"], 8, nonempty=True),
                   raw["compatibility"], raw["assessment_digest"])


@dataclass(frozen=True, slots=True)
class SettlementPlan:
    schema_version: int
    request_id: str
    active_request_id: str
    active_request_digest: str
    transaction_digest: str
    target: StagingTarget
    generation: int
    current_generation_digest: str | None
    observation: SettlementObservation
    data_assessment: SettlementDataAssessment
    reason: str
    plan_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 or type(self.target) is not StagingTarget \
                or type(self.observation) is not SettlementObservation \
                or type(self.data_assessment) is not SettlementDataAssessment:
            raise ActivationContractError()
        _text(self.request_id, identity=True); _text(self.active_request_id, identity=True)
        _digest(self.active_request_digest); _digest(self.transaction_digest)
        _integer(self.generation)
        if self.current_generation_digest is not None:
            _digest(self.current_generation_digest)
        if self.reason != "retained_effect_unknown":
            raise ActivationContractError()
        if (self.observation.target != self.target
                or self.observation.transaction_digest != self.transaction_digest
                or self.observation.generation != self.generation
                or self.data_assessment.target != self.target
                or self.data_assessment.transaction_digest != self.transaction_digest):
            raise ActivationContractError()
        _digest(self.plan_digest)
        if self.plan_digest != activation_digest(
                "sandbox.hosting.images.settlement-plan.v1", self.body_mapping()):
            raise ActivationContractError()

    def body_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "active_request_id": self.active_request_id,
            "active_request_digest": self.active_request_digest,
            "transaction_digest": self.transaction_digest,
            "target": _as_target(self.target),
            "generation": self.generation,
            "current_generation_digest": self.current_generation_digest,
            "observation": self.observation.as_mapping(),
            "data_assessment": self.data_assessment.as_mapping(),
            "reason": self.reason,
        }

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "plan_digest": self.plan_digest}

    @classmethod
    def create(cls, *, request_id: str, active_request_id: str,
               active_request_digest: str, transaction_digest: str, target: object,
               generation: int, current_generation_digest: str | None,
               observation: object, data_assessment: object,
               reason: str = "retained_effect_unknown") -> "SettlementPlan":
        target_value = _target(target)
        observation_value = observation if type(observation) is SettlementObservation \
            else SettlementObservation.from_mapping(observation)
        assessment_value = data_assessment if type(data_assessment) is SettlementDataAssessment \
            else SettlementDataAssessment.from_mapping(data_assessment)
        body = {
            "schema_version": 1, "request_id": request_id,
            "active_request_id": active_request_id,
            "active_request_digest": active_request_digest,
            "transaction_digest": transaction_digest,
            "target": _as_target(target_value), "generation": generation,
            "current_generation_digest": current_generation_digest,
            "observation": observation_value.as_mapping(),
            "data_assessment": assessment_value.as_mapping(), "reason": reason,
        }
        return cls(1, request_id, active_request_id, active_request_digest,
                   transaction_digest, target_value, generation,
                   current_generation_digest, observation_value, assessment_value,
                   reason, activation_digest("sandbox.hosting.images.settlement-plan.v1", body))

    @classmethod
    def from_mapping(cls, value: object) -> "SettlementPlan":
        raw = _closed(value, _PLAN_FIELDS)
        return cls(raw["schema_version"], raw["request_id"], raw["active_request_id"],
                   raw["active_request_digest"], raw["transaction_digest"],
                   StagingTarget.from_mapping(raw["target"]), raw["generation"],
                   raw["current_generation_digest"],
                   SettlementObservation.from_mapping(raw["observation"]),
                   SettlementDataAssessment.from_mapping(raw["data_assessment"]),
                   raw["reason"], raw["plan_digest"])


def _validate_signature(value: object) -> str:
    if type(value) is not str or not value or len(value) > 8192 \
            or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ActivationContractError()
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        raise ActivationContractError() from None
    if len(decoded) > 4096 or not decoded.startswith(_SSH_HEADER) \
            or not (decoded.endswith(_SSH_FOOTER)
                    or decoded.endswith(_SSH_FOOTER + b"\n")):
        raise ActivationContractError()
    return value


@dataclass(frozen=True, slots=True)
class SettlementApproval:
    schema_version: int
    authority_id: str
    authority_revision: str
    plan_digest: str
    issued_at: int
    expires_at: int
    signature: str
    approval_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ActivationContractError()
        _text(self.authority_id, identity=True); _text(self.authority_revision, identity=True)
        _digest(self.plan_digest); _integer(self.issued_at); _integer(self.expires_at)
        if self.expires_at <= self.issued_at or self.expires_at - self.issued_at > 3600:
            raise ActivationContractError()
        _validate_signature(self.signature); _digest(self.approval_digest)
        if self.approval_digest != activation_digest(
                "sandbox.hosting.images.settlement-approval.v1", self.body_mapping()):
            raise ActivationContractError()

    def unsigned_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authority_id": self.authority_id,
            "authority_revision": self.authority_revision,
            "plan_digest": self.plan_digest,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }

    def signature_payload(self) -> bytes:
        return canonical_bytes(self.unsigned_mapping())

    def body_mapping(self) -> dict[str, Any]:
        return {**self.unsigned_mapping(), "signature": self.signature}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "approval_digest": self.approval_digest}

    @classmethod
    def create(cls, *, authority_id: str, authority_revision: str,
               plan_digest: str, issued_at: int, expires_at: int,
               signature: str) -> "SettlementApproval":
        body = {
            "schema_version": 1, "authority_id": authority_id,
            "authority_revision": authority_revision, "plan_digest": plan_digest,
            "issued_at": issued_at, "expires_at": expires_at,
            "signature": signature,
        }
        return cls(1, authority_id, authority_revision, plan_digest, issued_at,
                   expires_at, signature,
                   activation_digest("sandbox.hosting.images.settlement-approval.v1", body))

    @classmethod
    def from_mapping(cls, value: object) -> "SettlementApproval":
        raw = _closed(value, _APPROVAL_FIELDS)
        return cls(raw["schema_version"], raw["authority_id"], raw["authority_revision"],
                   raw["plan_digest"], raw["issued_at"], raw["expires_at"],
                   raw["signature"], raw["approval_digest"])


__all__ = (
    "SettlementObservation", "SettlementDataAssessment", "SettlementPlan",
    "SettlementApproval",
)
