"""Closed, secret-free values for immutable image activation.

Feature 051 consumes Feature 049/050 values as claims.  This module deliberately
contains no filesystem, process, registry, credential, trust, or staging policy
authority.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass
import hashlib
import json
import re
from typing import Any, ClassVar, Mapping

from ..models import DeliveryIdentityProjection, ImageContractError, VerifiedImagePlan, validate_verified_image_plan
from ..staging_models import StagedImageProof, StagingContractError, validate_staged_image_proof


MAX_ACTIVATION_BYTES = 1024 * 1024
MAX_SERVICES = 16
MAX_INIT_STEPS = 16
MAX_RESULTS = 64
MAX_RECOVERY_RESULTS = 64
MAX_TOMBSTONES = 4096
MAX_OUTPUT_BYTES = 1024 * 1024

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_ACTIVATION_LEASE_ID = re.compile(r"activation-lease/[0-9a-f]{48}\Z")
_ACTIVATION_HOLDER_ID = re.compile(r"activation-owner/[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_HOST_ACCEPTANCE_ID = re.compile(r"host-acceptance/[0-9a-f]{64}\Z")
OPERATIONS = frozenset({"activate", "adopt", "rollback"})
PHASES = frozenset({
    "accepted", "preflight", "init_pending", "runtime_pending", "runtime_proven",
    "edge_pending", "committed", "refused", "failed", "cancelled", "uncertain",
})
TERMINAL_PHASES = frozenset({"committed", "refused", "failed", "cancelled", "uncertain"})
RESULT_CLASSES = frozenset({"success", "in_progress", "refused", "failed", "cancelled", "uncertain"})
RESULT_CODES = frozenset({
    "committed", "accepted", "request_conflict", "generation_conflict", "target_busy",
    "confirmation_required", "artifact_invalid", "artifact_mismatch", "authority_mismatch",
    "policy_mismatch", "proof_expired", "lease_capacity", "lease_conflict",
    "local_image_mismatch", "topology_mismatch", "init_mismatch", "init_uncertain",
    "runtime_mismatch", "health_incomplete", "edge_incomplete", "edge_uncertain",
    "adoption_requires_zero_init", "adoption_requires_effect", "rollback_unavailable",
    "rollback_grant_mismatch", "recovery_ineligible", "recovery_no_effect",
    "recovery_conflict", "evidence_changed", "observation_unavailable",
    "persistence_uncertain", "cancelled", "effect_unknown", "retention_full",
})


class ActivationContractError(ValueError):
    def __init__(self, code: str = "artifact_invalid") -> None:
        self.code = code if code in RESULT_CODES else "artifact_invalid"
        super().__init__(self.code)


def _text(value: object, *, identity: bool = False) -> str:
    if type(value) is not str or not value or len(value) > 512:
        raise ActivationContractError()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ActivationContractError()
    if identity and _ID.fullmatch(value) is None:
        raise ActivationContractError()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ActivationContractError()
    return value


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ActivationContractError()
    return value


def _closed(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise ActivationContractError()
    return value


def canonical_bytes(value: object, *, maximum: int = MAX_ACTIVATION_BYTES) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise ActivationContractError() from None
    if len(encoded) > maximum:
        raise ActivationContractError()
    return encoded


def activation_digest(domain: str, value: object) -> str:
    _text(domain, identity=True)
    return "sha256:" + hashlib.sha256(domain.encode("ascii") + b"\0" + canonical_bytes(value)).hexdigest()


def _safe_mapping(value: object, *, forbidden: frozenset[str] = frozenset()) -> dict[str, Any]:
    if type(value) is not dict:
        raise ActivationContractError()
    def reject(item: object) -> None:
        if isinstance(item, dict):
            if any(type(key) is not str or key.lower() in forbidden for key in item):
                raise ActivationContractError()
            for nested in item.values(): reject(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item: reject(nested)
        elif isinstance(item, str) and len(item) > MAX_OUTPUT_BYTES:
            raise ActivationContractError()
    reject(value)
    safe = json.loads(canonical_bytes(value))
    return safe


SECRET_FIELDS = frozenset({
    "credential", "credentials", "credential_reference", "token", "password", "secret",
    "secrets", "raw_environment", "environment", "env", "stdout", "stderr", "output",
    "temporary_path", "private_path", "registry_auth", "broker", "helper_command",
})


@dataclass(frozen=True, slots=True)
class ActivationPolicy:
    schema_version: int
    policy_digest: str
    authority_id: str
    authority_revision: str
    target: dict[str, str]
    selected_services: tuple[str, ...]
    compose_projection: tuple[dict[str, Any], ...]
    init_declarations: tuple[dict[str, Any], ...]
    runtime_capability_revision: str
    compose_capability_revision: str
    edge_policy_digest: str
    edge_required: bool
    edge_route_plan: tuple[dict[str, Any], ...]
    edge_route_digest: str
    mutation_owner_revision: str
    state_revision: str
    accepted_plan_schema: int
    accepted_proof_schema: int

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ActivationContractError("policy_mismatch")
        _digest(self.policy_digest); _text(self.authority_id, identity=True)
        for item in (self.authority_revision, self.runtime_capability_revision,
                     self.compose_capability_revision, self.mutation_owner_revision,
                     self.state_revision):
            _text(item, identity=True)
        _digest(self.edge_policy_digest); _digest(self.edge_route_digest)
        target = _closed(self.target, frozenset({"machine_identity", "target_identity", "daemon_identity"}))
        for value in target.values(): _text(value, identity=True)
        if not self.selected_services or len(self.selected_services) > MAX_SERVICES \
                or tuple(sorted(set(self.selected_services))) != self.selected_services:
            raise ActivationContractError("policy_mismatch")
        for value in self.selected_services: _text(value, identity=True)
        if len(self.compose_projection) != len(self.selected_services):
            raise ActivationContractError("policy_mismatch")
        if {item.get("service") for item in self.compose_projection} != set(self.selected_services):
            raise ActivationContractError("policy_mismatch")
        for item in self.compose_projection: _safe_mapping(item, forbidden=SECRET_FIELDS)
        if type(self.edge_required) is not bool or not self.edge_route_plan:
            raise ActivationContractError("policy_mismatch")
        for route in self.edge_route_plan: _safe_mapping(route, forbidden=SECRET_FIELDS)
        if self.edge_route_digest != activation_digest(
                "sandbox.hosting.images.activation-edge-routes.v1", list(self.edge_route_plan)) \
                or self.edge_policy_digest != self.edge_route_digest:
            raise ActivationContractError("policy_mismatch")
        if len(self.init_declarations) > MAX_INIT_STEPS:
            raise ActivationContractError("policy_mismatch")
        for declaration in self.init_declarations:
            _validate_init_declaration(declaration)
        if self.accepted_plan_schema != 1 or self.accepted_proof_schema != 1:
            raise ActivationContractError("policy_mismatch")
        if self.policy_digest != activation_digest(
                "sandbox.hosting.images.activation-policy.v1", self.identity_mapping()):
            raise ActivationContractError("policy_mismatch")

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "authority_id": self.authority_id,
                "authority_revision": self.authority_revision, "target": self.target,
                "selected_services": list(self.selected_services),
                "compose_projection": list(self.compose_projection),
                "init_declarations": list(self.init_declarations),
                "runtime_capability_revision": self.runtime_capability_revision,
                "compose_capability_revision": self.compose_capability_revision,
                "edge_policy_digest": self.edge_policy_digest,
                "edge_required": self.edge_required,
                "edge_route_plan": list(self.edge_route_plan),
                "edge_route_digest": self.edge_route_digest,
                "mutation_owner_revision": self.mutation_owner_revision,
                "state_revision": self.state_revision,
                "accepted_plan_schema": self.accepted_plan_schema,
                "accepted_proof_schema": self.accepted_proof_schema}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "policy_digest": self.policy_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "ActivationPolicy":
        raw = _closed(value, frozenset({"schema_version", "policy_digest", "authority_id",
            "authority_revision", "target", "selected_services", "compose_projection", "init_declarations",
            "runtime_capability_revision", "compose_capability_revision", "edge_policy_digest",
            "edge_required", "edge_route_plan", "edge_route_digest",
            "mutation_owner_revision", "state_revision", "accepted_plan_schema",
            "accepted_proof_schema"}))
        return cls(raw["schema_version"], raw["policy_digest"], raw["authority_id"],
                   raw["authority_revision"], raw["target"], tuple(raw["selected_services"]),
                   tuple(raw["compose_projection"]), tuple(raw["init_declarations"]), raw["runtime_capability_revision"],
                   raw["compose_capability_revision"], raw["edge_policy_digest"],
                   raw["edge_required"], tuple(raw["edge_route_plan"]), raw["edge_route_digest"],
                   raw["mutation_owner_revision"], raw["state_revision"],
                   raw["accepted_plan_schema"], raw["accepted_proof_schema"])


def _validate_init_declaration(value: object) -> dict[str, Any]:
    raw = _closed(value, frozenset({"name", "service", "command", "mounts", "networks",
        "environment_keys", "privileged", "dependencies", "timeout_seconds",
        "configuration_digest"}))
    for key in ("name", "service"): _text(raw[key], identity=True)
    if type(raw["command"]) is not list or not raw["command"] or len(raw["command"]) > 64:
        raise ActivationContractError("policy_mismatch")
    for item in raw["command"]: _text(item)
    for key in ("mounts", "networks", "environment_keys", "dependencies"):
        if type(raw[key]) is not list or len(raw[key]) > 64 or len(raw[key]) != len(set(raw[key])):
            raise ActivationContractError("policy_mismatch")
        for item in raw[key]: _text(item)
    for mount in raw["mounts"]:
        fields = {part.partition("=")[0]: part.partition("=")[2]
                  for part in mount.split(",") if "=" in part}
        if fields.get("type") not in {"bind", "volume", "tmpfs"} or not fields.get("target"):
            raise ActivationContractError("policy_mismatch")
    if type(raw["privileged"]) is not bool or raw["privileged"]:
        raise ActivationContractError("policy_mismatch")
    _integer(raw["timeout_seconds"], minimum=1); _digest(raw["configuration_digest"])
    return raw


@dataclass(frozen=True, slots=True)
class ActivationAuthorityBinding:
    schema_version: int
    binding_digest: str
    authority_id: str
    authority_revision: str
    plan_digest: str
    proof_digest: str
    stage_request_id: str
    stage_request_digest: str
    staging_policy_digest: str
    staging_generation: int
    stage_ledger_authority: str
    stage_ledger_revision: int
    target: dict[str, str]
    delivery_identity_projection: dict[str, Any]
    policy_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ActivationContractError("authority_mismatch")
        for item in (self.binding_digest, self.plan_digest, self.proof_digest,
                     self.stage_request_digest, self.staging_policy_digest, self.policy_digest):
            _digest(item)
        for item in (self.authority_id, self.authority_revision, self.stage_request_id,
                     self.stage_ledger_authority): _text(item, identity=True)
        _integer(self.staging_generation, minimum=1); _integer(self.stage_ledger_revision)
        for item in _closed(self.target, frozenset({"machine_identity", "target_identity", "daemon_identity"})).values():
            _text(item, identity=True)
        DeliveryIdentityProjection.from_mapping(self.delivery_identity_projection)
        if self.binding_digest != activation_digest(
                "sandbox.hosting.images.activation-authority.v1", self.identity_mapping()):
            raise ActivationContractError("authority_mismatch")

    def identity_mapping(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__ if key != "binding_digest"}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "binding_digest": self.binding_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "ActivationAuthorityBinding":
        raw = _closed(value, frozenset(cls.__dataclass_fields__))
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class ActivationRequest:
    schema_version: int
    request_id: str
    request_digest: str
    operation: str
    expected_generation: int
    policy_digest: str
    plan: VerifiedImagePlan
    proof: StagedImageProof
    authority_binding_digest: str
    rollback_grant_digest: str | None
    confirmed: bool

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.operation not in OPERATIONS or self.confirmed is not True:
            raise ActivationContractError("confirmation_required")
        _text(self.request_id, identity=True); _integer(self.expected_generation)
        for item in (self.request_digest, self.policy_digest, self.authority_binding_digest): _digest(item)
        if self.operation == "rollback": _digest(self.rollback_grant_digest)
        elif self.rollback_grant_digest is not None: raise ActivationContractError("request_conflict")
        if type(self.plan) is not VerifiedImagePlan or type(self.proof) is not StagedImageProof:
            raise ActivationContractError()
        if self.request_digest != activation_digest(
                "sandbox.hosting.images.activation-request.v1", self.identity_mapping()):
            raise ActivationContractError("request_conflict")

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "request_id": self.request_id,
                "operation": self.operation, "expected_generation": self.expected_generation,
                "policy_digest": self.policy_digest, "plan": self.plan.as_mapping(),
                "proof": self.proof.as_mapping(),
                "authority_binding_digest": self.authority_binding_digest,
                "rollback_grant_digest": self.rollback_grant_digest,
                "confirmed": self.confirmed}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "request_digest": self.request_digest}

    @classmethod
    def create(cls, *, request_id: str, operation: str, expected_generation: int,
               policy_digest: str, plan: object, proof: object,
               authority_binding_digest: str, rollback_grant_digest: str | None,
               confirmed: bool) -> "ActivationRequest":
        try:
            verified = validate_verified_image_plan(plan)
            staged = validate_staged_image_proof(proof)
        except (ImageContractError, StagingContractError):
            raise ActivationContractError() from None
        base = {"schema_version": 1, "request_id": request_id, "operation": operation,
                "expected_generation": expected_generation, "policy_digest": policy_digest,
                "plan": verified.as_mapping(), "proof": staged.as_mapping(),
                "authority_binding_digest": authority_binding_digest,
                "rollback_grant_digest": rollback_grant_digest, "confirmed": confirmed}
        digest = activation_digest("sandbox.hosting.images.activation-request.v1", base)
        return cls(1, request_id, digest, operation, expected_generation, policy_digest,
                   verified, staged, authority_binding_digest, rollback_grant_digest, confirmed)


@dataclass(frozen=True, slots=True)
class ProofPinBinding:
    lease_id: str
    holder: str
    proof_digest: str
    host_acceptance_receipt: str
    pin_digest: str

    def __post_init__(self) -> None:
        for item in (self.lease_id, self.holder, self.host_acceptance_receipt): _text(item, identity=True)
        _digest(self.proof_digest); _digest(self.pin_digest)
        if not self.holder.startswith("activation-owner/"):
            raise ActivationContractError("lease_conflict")
        if self.pin_digest != activation_digest("sandbox.hosting.images.activation-proof-pin.v1", self.body_mapping()):
            raise ActivationContractError("lease_conflict")

    def body_mapping(self) -> dict[str, str]:
        return {"lease_id": self.lease_id, "holder": self.holder,
                "proof_digest": self.proof_digest,
                "host_acceptance_receipt": self.host_acceptance_receipt}

    def as_mapping(self) -> dict[str, str]: return {**self.body_mapping(), "pin_digest": self.pin_digest}


@dataclass(frozen=True, slots=True)
class ForwardRollbackSubject:
    target: dict[str, str]
    rollback_target_generation_digest: str
    candidate_plan_digest: str
    candidate_proof_digest: str
    activation_authority_digest: str
    configuration_digest: str
    topology_digest: str
    init_data_contract_digest: str
    policy_revision: str
    subject_digest: str

    def __post_init__(self) -> None:
        for item in self.target.values(): _text(item, identity=True)
        for item in (self.rollback_target_generation_digest, self.candidate_plan_digest,
                     self.candidate_proof_digest, self.activation_authority_digest,
                     self.configuration_digest, self.topology_digest,
                     self.init_data_contract_digest, self.subject_digest): _digest(item)
        _text(self.policy_revision, identity=True)
        if self.subject_digest != activation_digest("sandbox.hosting.images.forward-rollback-subject.v1", self.body_mapping()):
            raise ActivationContractError("rollback_grant_mismatch")

    def body_mapping(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__ if key != "subject_digest"}

    def as_mapping(self) -> dict[str, Any]: return {**self.body_mapping(), "subject_digest": self.subject_digest}


@dataclass(frozen=True, slots=True)
class RollbackCompatibilityGrant:
    authority_id: str
    authority_revision: str
    issued_at: int
    policy_revision: str
    subject: ForwardRollbackSubject
    grant_digest: str
    expires_at: int | None = None
    revoked: bool = False

    def __post_init__(self) -> None:
        _text(self.authority_id, identity=True); _text(self.authority_revision, identity=True)
        _text(self.policy_revision, identity=True); _integer(self.issued_at)
        if self.expires_at is not None: _integer(self.expires_at)
        if type(self.revoked) is not bool or type(self.subject) is not ForwardRollbackSubject:
            raise ActivationContractError("rollback_grant_mismatch")
        _digest(self.grant_digest)
        if self.grant_digest != activation_digest("sandbox.hosting.images.rollback-grant.v1", self.body_mapping()):
            raise ActivationContractError("rollback_grant_mismatch")

    def body_mapping(self) -> dict[str, Any]:
        return {"authority_id": self.authority_id, "authority_revision": self.authority_revision,
                "issued_at": self.issued_at, "policy_revision": self.policy_revision,
                "subject": self.subject.as_mapping(), "expires_at": self.expires_at,
                "revoked": self.revoked}

    def as_mapping(self) -> dict[str, Any]: return {**self.body_mapping(), "grant_digest": self.grant_digest}


@dataclass(frozen=True, slots=True)
class InitReceipt:
    declaration_digest: str
    target_epoch: str
    runtime_epoch: str
    local_image_id: str
    created_identity: str
    inspection_digest: str
    effect_entered: bool
    exit_code: int
    termination_complete: bool
    cleanup_complete: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        for item in (self.declaration_digest, self.local_image_id, self.inspection_digest,
                     self.receipt_digest): _digest(item)
        for item in (self.target_epoch, self.runtime_epoch, self.created_identity): _text(item, identity=True)
        if self.effect_entered is not True or type(self.exit_code) is not int or self.exit_code != 0 \
                or self.termination_complete is not True or self.cleanup_complete is not True:
            raise ActivationContractError("init_uncertain")
        if self.receipt_digest != activation_digest("sandbox.hosting.images.init-receipt.v1", self.body_mapping()):
            raise ActivationContractError("init_mismatch")

    def body_mapping(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__ if key != "receipt_digest"}

    def as_mapping(self) -> dict[str, Any]: return {**self.body_mapping(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True, slots=True)
class RunningObservation:
    target: dict[str, str]
    target_epoch_start: str
    target_epoch_end: str
    runtime_epoch_start: str
    runtime_epoch_end: str
    services: tuple[dict[str, Any], ...]
    topology_digest: str
    health_complete: bool
    edge_identity: str
    observation_digest: str

    def __post_init__(self) -> None:
        for item in self.target.values(): _text(item, identity=True)
        for item in (self.target_epoch_start, self.target_epoch_end,
                     self.runtime_epoch_start, self.runtime_epoch_end, self.edge_identity):
            _text(item, identity=True)
        if self.target_epoch_start != self.target_epoch_end or self.runtime_epoch_start != self.runtime_epoch_end:
            raise ActivationContractError("runtime_mismatch")
        if not self.services or len(self.services) > MAX_SERVICES or self.health_complete is not True:
            raise ActivationContractError("health_incomplete")
        names = []
        required = frozenset({"service", "runtime_identity", "declared_image", "local_image_id",
                              "repository_digest", "config_digest", "platform",
                              "topology_identity", "healthy"})
        for service in self.services:
            raw = _closed(service, required); names.append(_text(raw["service"], identity=True))
            _text(raw["runtime_identity"], identity=True)
            for key in ("local_image_id", "config_digest"): _digest(raw[key])
            _text(raw["declared_image"]); _text(raw["repository_digest"])
            if raw["healthy"] is not True or type(raw["platform"]) is not dict:
                raise ActivationContractError("health_incomplete")
            _text(raw["topology_identity"], identity=True)
        if len(names) != len(set(names)):
            raise ActivationContractError("runtime_mismatch")
        _digest(self.topology_digest); _digest(self.observation_digest)
        if self.observation_digest != activation_digest("sandbox.hosting.images.running-observation.v1", self.body_mapping()):
            raise ActivationContractError("runtime_mismatch")

    def body_mapping(self) -> dict[str, Any]:
        return {"target": self.target, "target_epoch_start": self.target_epoch_start,
                "target_epoch_end": self.target_epoch_end, "runtime_epoch_start": self.runtime_epoch_start,
                "runtime_epoch_end": self.runtime_epoch_end, "services": list(self.services),
                "topology_digest": self.topology_digest, "health_complete": self.health_complete,
                "edge_identity": self.edge_identity}

    def as_mapping(self) -> dict[str, Any]: return {**self.body_mapping(), "observation_digest": self.observation_digest}


@dataclass(frozen=True, slots=True)
class VerifiedActivationGeneration:
    generation: int
    plan_digest: str
    proof_digest: str
    policy_digest: str
    request_digest: str
    transaction_digest: str
    target: dict[str, str]
    image: dict[str, Any]
    topology_digest: str
    configuration_digest: str
    compose_projection: tuple[dict[str, Any], ...]
    init_receipt_digests: tuple[str, ...]
    running_observation_digest: str
    service_projection: tuple[dict[str, Any], ...]
    edge_receipt_digest: str
    proof_pin_digest: str
    rollback_subject_digest: str
    rollback_grant_digest: str
    generation_digest: str

    def __post_init__(self) -> None:
        _integer(self.generation, minimum=1)
        for item in (self.plan_digest, self.proof_digest, self.policy_digest,
                     self.request_digest, self.transaction_digest, self.topology_digest,
                     self.configuration_digest, self.running_observation_digest,
                     self.edge_receipt_digest, self.proof_pin_digest,
                     self.rollback_subject_digest, self.rollback_grant_digest,
                     self.generation_digest, *self.init_receipt_digests): _digest(item)
        if len(self.init_receipt_digests) > MAX_INIT_STEPS or not self.compose_projection \
                or not self.service_projection:
            raise ActivationContractError()
        _safe_mapping(self.image, forbidden=SECRET_FIELDS)
        exact_image = self.image.get("repository_qualified_digest")
        exact_platform = self.image.get("platform")
        if type(exact_image) is not str or "@sha256:" not in exact_image \
                or type(exact_platform) is not dict:
            raise ActivationContractError()
        compose_fields = frozenset({"service", "image", "build", "pull_policy", "platform",
                                    "dependencies", "topology_identity", "configuration_digest"})
        compose_names = []
        if len(self.compose_projection) > MAX_SERVICES:
            raise ActivationContractError()
        for item in self.compose_projection:
            raw = _closed(item, compose_fields)
            compose_names.append(_text(raw["service"], identity=True))
            if raw["image"] != exact_image or raw["build"] is not None \
                    or raw["pull_policy"] not in {"never", "missing-refused"} \
                    or raw["platform"] != exact_platform \
                    or raw["topology_identity"] != self.topology_digest \
                    or type(raw["dependencies"]) is not list:
                raise ActivationContractError()
            _digest(raw["configuration_digest"])
            for dependency in raw["dependencies"]:
                _text(dependency, identity=True)
        if len(compose_names) != len(set(compose_names)):
            raise ActivationContractError()
        RunningObservation(
            target=self.target, target_epoch_start="projection", target_epoch_end="projection",
            runtime_epoch_start="projection", runtime_epoch_end="projection",
            services=self.service_projection, topology_digest=self.topology_digest,
            health_complete=True, edge_identity="projection",
            observation_digest=activation_digest("sandbox.hosting.images.running-observation.v1", {
                "target": self.target, "target_epoch_start": "projection", "target_epoch_end": "projection",
                "runtime_epoch_start": "projection", "runtime_epoch_end": "projection",
                "services": list(self.service_projection), "topology_digest": self.topology_digest,
                "health_complete": True, "edge_identity": "projection"}))
        if {item["service"] for item in self.service_projection} != set(compose_names) \
                or any(item["declared_image"] != exact_image
                       or item["repository_digest"] != exact_image
                       or item["local_image_id"] != self.image.get("config_digest")
                       or item["config_digest"] != self.image.get("config_digest")
                       or item["platform"] != exact_platform
                       or item["topology_identity"] != self.topology_digest
                       for item in self.service_projection):
            raise ActivationContractError()
        if self.generation_digest != activation_digest("sandbox.hosting.images.activation-generation.v1", self.body_mapping()):
            raise ActivationContractError("artifact_invalid")

    def body_mapping(self) -> dict[str, Any]:
        return {key: (list(value) if isinstance(value, tuple) else value)
                for key, value in ((name, getattr(self, name)) for name in self.__dataclass_fields__)
                if key != "generation_digest"}

    def as_mapping(self) -> dict[str, Any]: return {**self.body_mapping(), "generation_digest": self.generation_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "VerifiedActivationGeneration":
        fields = frozenset(cls.__dataclass_fields__)
        raw = _closed(value, fields)
        return cls(**{**raw, "init_receipt_digests": tuple(raw["init_receipt_digests"]),
                      "compose_projection": tuple(raw["compose_projection"]),
                      "service_projection": tuple(raw["service_projection"])})


@dataclass(frozen=True, slots=True)
class ActivationTransaction:
    schema_version: int
    transaction_digest: str
    request_id: str
    request_digest: str
    operation: str
    holder: str
    starting_generation: int
    phase: str
    effect_entered: bool
    authority_binding_digest: str
    proof_pin: dict[str, Any] | None
    rollback_subject_digest: str
    rollback_grant_digest: str
    init_receipts: tuple[dict[str, Any], ...]
    init_steps: tuple[dict[str, Any], ...]
    edge_required: bool
    running_observation: dict[str, Any] | None
    edge_result: dict[str, Any] | None
    candidate_generation: dict[str, Any] | None
    result: dict[str, Any] | None

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.operation not in OPERATIONS or self.phase not in PHASES:
            raise ActivationContractError()
        _digest(self.transaction_digest); _text(self.request_id, identity=True)
        _digest(self.request_digest); _text(self.holder, identity=True); _integer(self.starting_generation)
        _digest(self.authority_binding_digest); _digest(self.rollback_subject_digest); _digest(self.rollback_grant_digest)
        if type(self.effect_entered) is not bool or type(self.edge_required) is not bool \
                or len(self.init_receipts) > MAX_INIT_STEPS or len(self.init_steps) > MAX_INIT_STEPS:
            raise ActivationContractError()
        for value in (self.proof_pin, self.running_observation, self.edge_result,
                      self.candidate_generation, self.result):
            if value is not None: _safe_mapping(value, forbidden=SECRET_FIELDS)
        if self.proof_pin is not None:
            pin = _closed(self.proof_pin, frozenset({"lease_id", "holder", "phase",
                "proof_digest", "host_acceptance_receipt"}))
            if pin["phase"] != "accepted": raise ActivationContractError()
            _digest(pin["proof_digest"])
            for key in ("lease_id", "holder", "host_acceptance_receipt"): _text(pin[key], identity=True)
            if _ACTIVATION_LEASE_ID.fullmatch(pin["lease_id"]) is None \
                    or _ACTIVATION_HOLDER_ID.fullmatch(pin["holder"]) is None \
                    or _HOST_ACCEPTANCE_ID.fullmatch(pin["host_acceptance_receipt"]) is None:
                raise ActivationContractError()
        for receipt in self.init_receipts:
            _safe_mapping(receipt, forbidden=SECRET_FIELDS); InitReceipt(**receipt)
        if self.running_observation is not None:
            RunningObservation(**{**self.running_observation,
                                  "services": tuple(self.running_observation["services"])})
        if self.candidate_generation is not None:
            VerifiedActivationGeneration.from_mapping(self.candidate_generation)
        if self.result is not None: ActivationResult.from_mapping(self.result)
        if self.edge_result is not None:
            prepared = {"request_id", "request_digest", "phase", "terminal", "receipt_digest"}
            terminal = {"request_id", "request_digest", "terminal", "receipt_digest"}
            if set(self.edge_result) not in {frozenset(prepared), frozenset(terminal)} \
                    or type(self.edge_result.get("terminal")) is not bool:
                raise ActivationContractError("edge_uncertain")
            _text(self.edge_result["request_id"], identity=True)
            _digest(self.edge_result["request_digest"])
            if self.edge_result["terminal"] is True: _digest(self.edge_result["receipt_digest"])
            elif self.edge_result.get("receipt_digest") is not None:
                raise ActivationContractError("edge_uncertain")
        for index, step in enumerate(self.init_steps):
            raw = _closed(step, frozenset({"index", "declaration_digest", "inspection_digest",
                                           "effect_entered", "receipt_digest"}))
            if raw["index"] != index or type(raw["effect_entered"]) is not bool:
                raise ActivationContractError("init_uncertain")
            _digest(raw["declaration_digest"])
            if raw["inspection_digest"] is not None: _digest(raw["inspection_digest"])
            if raw["receipt_digest"] is not None: _digest(raw["receipt_digest"])

    def as_mapping(self) -> dict[str, Any]:
        return {key: (list(value) if isinstance(value, tuple) else value)
                for key, value in ((name, getattr(self, name)) for name in self.__dataclass_fields__)}


@dataclass(frozen=True, slots=True)
class ActivationRecoveryProvisional:
    request_id: str
    request_digest: str
    transaction_digest: str
    expected_generation: int
    owner: str
    evidence_identity: str
    classification: str
    target_epoch_start: str
    target_epoch_end: str
    runtime_epoch_start: str
    runtime_epoch_end: str
    authorizing: bool = False

    def __post_init__(self) -> None:
        for item in (self.request_id, self.owner, self.target_epoch_start, self.target_epoch_end,
                     self.runtime_epoch_start, self.runtime_epoch_end): _text(item, identity=True)
        for item in (self.request_digest, self.transaction_digest, self.evidence_identity): _digest(item)
        _integer(self.expected_generation)
        if self.classification not in {"exact_new", "exact_prior", "neither", "ambiguous"} \
                or self.authorizing is not False:
            raise ActivationContractError("recovery_conflict")

    def as_mapping(self) -> dict[str, Any]: return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ActivationResult:
    schema_version: int
    ok: bool
    result_class: str
    code: str
    operation: str
    request_id: str
    request_digest: str
    starting_generation: int
    resulting_generation: int
    transaction_digest: str
    generation_digest: str | None = None
    observation_digest: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1 or type(self.ok) is not bool or self.result_class not in RESULT_CLASSES \
                or self.code not in RESULT_CODES or self.operation not in {*OPERATIONS, "image_recover"}:
            raise ActivationContractError()
        _text(self.request_id, identity=True); _digest(self.request_digest)
        _integer(self.starting_generation); _integer(self.resulting_generation); _digest(self.transaction_digest)
        if self.generation_digest is not None: _digest(self.generation_digest)
        if self.observation_digest is not None: _digest(self.observation_digest)
        if self.ok != (self.result_class == "success") or (self.ok and self.resulting_generation != self.starting_generation + 1):
            raise ActivationContractError()

    def as_mapping(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__ if getattr(self, name) is not None}

    @classmethod
    def from_mapping(cls, value: object) -> "ActivationResult":
        if type(value) is not dict:
            raise ActivationContractError()
        required = {name for name, field in cls.__dataclass_fields__.items()
                    if field.default is MISSING and field.default_factory is MISSING}
        allowed = set(cls.__dataclass_fields__)
        if not required <= set(value) <= allowed:
            raise ActivationContractError()
        return cls(**value)


def validate_activation_artifacts(plan: object, proof: object) -> tuple[VerifiedImagePlan, StagedImageProof]:
    """Validate public schemas and exact equality only; never re-run trust/staging."""
    try:
        verified = validate_verified_image_plan(plan)
        staged = validate_staged_image_proof(proof)
    except (ImageContractError, StagingContractError):
        raise ActivationContractError() from None
    projection = verified.delivery_identity_projection.as_mapping()
    observed = staged.observed_identity
    if staged.plan_digest != verified.plan_digest \
            or staged.delivery_identity_projection != projection \
            or observed.get("repository") != verified.image.repository \
            or observed.get("repo_digest") != verified.image.repository_qualified_digest \
            or observed.get("config_digest") != verified.image.config_digest \
            or observed.get("platform") != verified.image.platform.as_mapping() \
            or observed.get("observed_topology") != verified.topology.as_mapping():
        raise ActivationContractError("artifact_mismatch")
    return verified, staged


LEGAL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "accepted": frozenset({"preflight", "refused", "failed", "cancelled", "uncertain"}),
    "preflight": frozenset({"init_pending", "runtime_pending", "runtime_proven", "refused", "failed", "uncertain"}),
    "init_pending": frozenset({"init_pending", "runtime_pending", "failed", "uncertain"}),
    "runtime_pending": frozenset({"runtime_proven", "failed", "uncertain"}),
    "runtime_proven": frozenset({"edge_pending", "committed", "failed", "uncertain"}),
    "edge_pending": frozenset({"edge_pending", "committed", "failed", "uncertain"}),
}


def validate_transition(current: str, candidate: str, *, effect_entered: bool,
                        terminal_receipt: bool = False) -> None:
    if current not in LEGAL_TRANSITIONS or candidate not in LEGAL_TRANSITIONS[current]:
        raise ActivationContractError("request_conflict")
    if current == "init_pending" and effect_entered \
            and candidate not in {"runtime_pending", "uncertain"} \
            and not (candidate == "init_pending" and terminal_receipt):
        raise ActivationContractError("init_uncertain")
    if candidate == "committed" and not terminal_receipt:
        raise ActivationContractError("effect_unknown")
