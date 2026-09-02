"""Closed values for secure, replay-safe private image staging.

These values contain no credential material and expose no staging effects.  Feature
049 remains the sole image-trust authority; this module only validates and projects
an already verified plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, ClassVar

from .models import (
    DeliveryIdentityProjection, ImageContractError, VerifiedImagePlan,
    validate_verified_image_plan,
)


MAX_PROOFS = 64
MAX_TOMBSTONES = 4096
MAX_LIVE_PROOF_LEASES = 64
MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_STAGE_FRAME_BYTES = 1024 * 1024
MAX_PERSISTED_LEDGER_COUNTER = 9007199254740991

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_UTC_DEADLINE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_HOST_ACCEPTANCE_RECEIPT = re.compile(r"host-acceptance/[0-9a-f]{64}\Z")
_RESULT_CLASSES = frozenset({"success", "in_progress", "refused", "failed", "cancelled", "uncertain"})
_RESULT_CODES = frozenset({
    "staged", "plan_invalid", "policy_mismatch", "target_mismatch", "helper_mismatch",
    "broker_mismatch", "capability_mismatch", "request_conflict", "generation_conflict",
    "retention_full", "proof_expired", "proof_invalid", "broker_unavailable",
    "helper_failed", "pull_failed", "cleanup_unproven", "observation_invalid",
    "process_unproven", "unknown_effect", "cancelled", "acceptance_unknown",
    "lease_conflict", "lease_capacity", "lease_expired", "acceptance_ambiguous",
    "holder_mismatch", "terminal_not_durable", "target_busy",
    "accepted", "in_progress",
})


class StagingContractError(ValueError):
    def __init__(self, code: str = "proof_invalid") -> None:
        self.code = code if code in _RESULT_CODES else "proof_invalid"
        super().__init__(self.code)


def _text(value: object, *, identity: bool = False) -> str:
    if type(value) is not str or not value or len(value) > 512:
        raise StagingContractError()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise StagingContractError()
    if identity and _ID.fullmatch(value) is None:
        raise StagingContractError()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise StagingContractError()
    return value


def canonical_bytes(value: object, *, maximum: int = MAX_STAGE_FRAME_BYTES) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise StagingContractError() from None
    if len(encoded) > maximum:
        raise StagingContractError()
    return encoded


def staging_digest(domain: str, value: object) -> str:
    _text(domain, identity=True)
    return "sha256:" + hashlib.sha256(domain.encode("ascii") + b"\0" + canonical_bytes(value)).hexdigest()


def _closed(raw: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != fields:
        raise StagingContractError()
    return raw


@dataclass(frozen=True, slots=True)
class StagingTarget:
    machine_identity: str
    target_identity: str
    daemon_identity: str

    def __post_init__(self) -> None:
        for value in (self.machine_identity, self.target_identity, self.daemon_identity):
            _text(value, identity=True)

    def as_mapping(self) -> dict[str, str]:
        return {"machine_identity": self.machine_identity, "target_identity": self.target_identity,
                "daemon_identity": self.daemon_identity}

    @classmethod
    def from_mapping(cls, value: object) -> "StagingTarget":
        raw = _closed(value, frozenset({"machine_identity", "target_identity", "daemon_identity"}))
        return cls(raw["machine_identity"], raw["target_identity"], raw["daemon_identity"])


@dataclass(frozen=True, slots=True)
class HelperIdentity:
    artifact_digest: str
    entry: str
    runtime_revision: str
    capability_revision: str

    def __post_init__(self) -> None:
        _digest(self.artifact_digest)
        for value in (self.entry, self.runtime_revision, self.capability_revision):
            _text(value, identity=True)

    def as_mapping(self) -> dict[str, str]:
        return {"artifact_digest": self.artifact_digest, "entry": self.entry,
                "runtime_revision": self.runtime_revision,
                "capability_revision": self.capability_revision}


@dataclass(frozen=True, slots=True)
class StagingPolicy:
    schema_version: int
    policy_digest: str
    plan_digest: str
    target: StagingTarget
    helper: HelperIdentity
    broker_recipient: str
    broker_binding_id: str
    broker_binding_version: int
    credential_reference_revision: str
    operation: str
    capability_revision: str
    projection: DeliveryIdentityProjection

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 \
                or type(self.target) is not StagingTarget \
                or type(self.helper) is not HelperIdentity \
                or type(self.projection) is not DeliveryIdentityProjection:
            raise StagingContractError("policy_mismatch")
        _digest(self.plan_digest)
        _digest(self.policy_digest)
        # The recipient is a closed namespace-specific capability string. Its
        # required ``:``, ``@``, and digest separator are not generic identity
        # characters, so validate it only by deriving the one exact authorized
        # value from the trusted projection. Other policy identities retain the
        # stricter generic identity grammar.
        _text(self.broker_recipient)
        for value in (self.broker_binding_id, self.credential_reference_revision,
                      self.capability_revision):
            _text(value, identity=True)
        if type(self.broker_binding_version) is not int or self.broker_binding_version < 1:
            raise StagingContractError("policy_mismatch")
        expected_recipient = (
            f"ghcr-repository-read:{self.projection.image.repository}@"
            f"{self.projection.image.manifest_digest}"
        )
        if self.broker_recipient != expected_recipient \
                or self.operation != "ghcr.repository.read":
            raise StagingContractError("policy_mismatch")
        if self.policy_digest != staging_digest(
                "sandbox.hosting.images.staging-policy.v1", self.identity_mapping()):
            raise StagingContractError("policy_mismatch")

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "plan_digest": self.plan_digest,
                "target": self.target.as_mapping(), "helper": self.helper.as_mapping(),
                "broker_recipient": self.broker_recipient,
                "broker_binding_id": self.broker_binding_id,
                "broker_binding_version": self.broker_binding_version,
                "credential_reference_revision": self.credential_reference_revision,
                "operation": self.operation, "capability_revision": self.capability_revision,
                "delivery_identity_projection": self.projection.as_mapping()}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "policy_digest": self.policy_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "StagingPolicy":
        fields = frozenset({"schema_version", "policy_digest", "plan_digest", "target", "helper",
                            "broker_recipient", "broker_binding_id", "broker_binding_version",
                            "credential_reference_revision", "operation", "capability_revision",
                            "delivery_identity_projection"})
        raw = _closed(value, fields)
        helper_raw = _closed(raw["helper"], frozenset({"artifact_digest", "entry", "runtime_revision",
                                                       "capability_revision"}))
        return cls(raw["schema_version"], raw["policy_digest"], raw["plan_digest"],
                   StagingTarget.from_mapping(raw["target"]), HelperIdentity(**helper_raw),
                   raw["broker_recipient"], raw["broker_binding_id"], raw["broker_binding_version"],
                   raw["credential_reference_revision"], raw["operation"],
                   raw["capability_revision"],
                   DeliveryIdentityProjection.from_mapping(raw["delivery_identity_projection"]))


@dataclass(frozen=True, slots=True)
class StageRequest:
    schema_version: int
    request_id: str
    request_digest: str
    expected_generation: int
    plan: VerifiedImagePlan
    staging_policy_digest: str
    target: StagingTarget
    confirmed: bool

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 \
                or type(self.plan) is not VerifiedImagePlan \
                or type(self.target) is not StagingTarget or self.confirmed is not True:
            raise StagingContractError("plan_invalid")
        _text(self.request_id, identity=True)
        _digest(self.request_digest)
        _digest(self.staging_policy_digest)
        if type(self.expected_generation) is not int \
                or not 0 <= self.expected_generation <= MAX_PERSISTED_LEDGER_COUNTER:
            raise StagingContractError("generation_conflict")
        if self.request_digest != staging_digest(
                "sandbox.hosting.images.stage-request.v1", self.identity_mapping()):
            raise StagingContractError("request_conflict")

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "request_id": self.request_id,
                "expected_generation": self.expected_generation, "plan": self.plan.as_mapping(),
                "staging_policy_digest": self.staging_policy_digest,
                "target": self.target.as_mapping(), "confirmed": self.confirmed}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "request_digest": self.request_digest}

    @classmethod
    def create(cls, *, request_id: str, expected_generation: int, plan: object,
               staging_policy_digest: str, target: StagingTarget, confirmed: bool) -> "StageRequest":
        try:
            verified = validate_verified_image_plan(plan)
        except ImageContractError:
            raise StagingContractError("plan_invalid") from None
        base = {"schema_version": 1, "request_id": request_id,
                "expected_generation": expected_generation, "plan": verified.as_mapping(),
                "staging_policy_digest": staging_policy_digest,
                "target": target.as_mapping(), "confirmed": confirmed}
        return cls(1, request_id, staging_digest("sandbox.hosting.images.stage-request.v1", base),
                   expected_generation, verified, staging_policy_digest, target, confirmed)


@dataclass(frozen=True, slots=True)
class LocalImageObservation:
    observation_id: str
    target_epoch_start: str
    target_epoch_end: str
    daemon_epoch_start: str
    daemon_epoch_end: str
    target: StagingTarget
    repository: str
    repo_digest: str
    config_digest: str
    platform: dict[str, str]
    local_image_id: str
    topology_digest: str
    observed_topology: dict[str, Any]
    anonymous_exact_manifest: str
    authenticated_exact_manifest: str
    observation_digest: str

    def __post_init__(self) -> None:
        _digest(self.observation_id); _digest(self.config_digest); _digest(self.local_image_id)
        _digest(self.topology_digest); _digest(self.observation_digest)
        for value in (self.target_epoch_start, self.target_epoch_end,
                      self.daemon_epoch_start, self.daemon_epoch_end):
            _text(value, identity=True)
        if self.target_epoch_start != self.target_epoch_end \
                or self.daemon_epoch_start != self.daemon_epoch_end \
                or self.local_image_id != self.config_digest \
                or type(self.platform) is not dict or type(self.observed_topology) is not dict:
            raise StagingContractError("observation_invalid")
        if self.topology_digest != staging_digest(
                "sandbox.hosting.images.topology.v1", self.observed_topology):
            raise StagingContractError("observation_invalid")
        registry = {"anonymous_exact_manifest": self.anonymous_exact_manifest,
                    "authenticated_exact_manifest": self.authenticated_exact_manifest}
        if self.observation_digest != staging_digest(
                "sandbox.hosting.images.registry-observation.v1", registry) \
                or self.anonymous_exact_manifest != "denied" \
                or self.authenticated_exact_manifest != "succeeded":
            raise StagingContractError("observation_invalid")
        if self.observation_id != staging_digest(
                "sandbox.hosting.images.local-observation.v1", self.body_mapping()):
            raise StagingContractError("observation_invalid")

    def body_mapping(self) -> dict[str, Any]:
        return {"target_epoch_start": self.target_epoch_start,
                "target_epoch_end": self.target_epoch_end,
                "daemon_epoch_start": self.daemon_epoch_start,
                "daemon_epoch_end": self.daemon_epoch_end,
                "target": self.target.as_mapping(), "repository": self.repository,
                "repo_digest": self.repo_digest, "config_digest": self.config_digest,
                "platform": self.platform, "local_image_id": self.local_image_id,
                "topology_digest": self.topology_digest,
                "observed_topology": self.observed_topology,
                "anonymous_exact_manifest": self.anonymous_exact_manifest,
                "authenticated_exact_manifest": self.authenticated_exact_manifest,
                "observation_digest": self.observation_digest}

    def observed_mapping(self) -> dict[str, Any]:
        return {"target_epoch_start": self.target_epoch_start,
                "target_epoch_end": self.target_epoch_end,
                "daemon_epoch_start": self.daemon_epoch_start,
                "daemon_epoch_end": self.daemon_epoch_end,
                "target": self.target.as_mapping(),
                "repository": self.repository, "repo_digest": self.repo_digest,
                "config_digest": self.config_digest, "platform": self.platform,
                "local_image_id": self.local_image_id, "topology_digest": self.topology_digest,
                "observed_topology": self.observed_topology}

    def registry_mapping(self) -> dict[str, str]:
        return {"anonymous_exact_manifest": self.anonymous_exact_manifest,
                "authenticated_exact_manifest": self.authenticated_exact_manifest,
                "observation_digest": self.observation_digest}


@dataclass(frozen=True, slots=True)
class StagedImageProof:
    schema_version: int
    request_id: str
    request_digest: str
    plan_digest: str
    staging_policy_digest: str
    target: StagingTarget
    helper: HelperIdentity
    delivery_identity_projection: dict[str, Any]
    observed_identity: dict[str, Any]
    registry_access_observation: dict[str, str]
    observation_id: str
    staging_generation: int
    proof_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({"schema_version", "request", "plan_digest",
        "staging_policy_digest", "target", "helper", "delivery_identity_projection",
        "observed_identity", "registry_access_observation", "observation_id",
        "staging_generation", "proof_digest"})

    def body_mapping(self) -> dict[str, Any]:
        helper = self.helper.as_mapping(); helper.pop("entry")
        return {"schema_version": self.schema_version,
                "request": {"request_id": self.request_id, "request_digest": self.request_digest},
                "plan_digest": self.plan_digest, "staging_policy_digest": self.staging_policy_digest,
                "target": self.target.as_mapping(), "helper": helper,
                "delivery_identity_projection": self.delivery_identity_projection,
                "observed_identity": self.observed_identity,
                "registry_access_observation": self.registry_access_observation,
                "observation_id": self.observation_id,
                "staging_generation": self.staging_generation}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "proof_digest": self.proof_digest}

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 \
                or type(self.target) is not StagingTarget \
                or type(self.helper) is not HelperIdentity or type(self.staging_generation) is not int \
                or not 1 <= self.staging_generation <= MAX_PERSISTED_LEDGER_COUNTER:
            raise StagingContractError()
        for value in (self.request_digest, self.plan_digest, self.staging_policy_digest,
                      self.observation_id, self.proof_digest): _digest(value)
        _text(self.request_id, identity=True)
        projection = DeliveryIdentityProjection.from_mapping(self.delivery_identity_projection)
        expected_observed = frozenset({"target_epoch_start", "target_epoch_end",
            "daemon_epoch_start", "daemon_epoch_end", "target", "repository", "repo_digest",
            "config_digest", "platform", "local_image_id", "topology_digest",
            "observed_topology"})
        observed = _closed(self.observed_identity, expected_observed)
        _closed(self.registry_access_observation, frozenset({"anonymous_exact_manifest",
                                                             "authenticated_exact_manifest",
                                                             "observation_digest"}))
        if observed["target"] != self.target.as_mapping() \
                or observed["target_epoch_start"] != observed["target_epoch_end"] \
                or observed["daemon_epoch_start"] != observed["daemon_epoch_end"] \
                or observed["target_epoch_start"] != self.target.machine_identity \
                or observed["daemon_epoch_start"] != self.target.daemon_identity \
                or observed["repository"] != projection.image.repository \
                or observed["repo_digest"] != projection.image.repository_qualified_digest \
                or observed["config_digest"] != projection.image.config_digest \
                or observed["local_image_id"] != observed["config_digest"] \
                or observed["platform"] != projection.image.platform.as_mapping() \
                or observed["observed_topology"] != projection.topology.as_mapping() \
                or observed["topology_digest"] != staging_digest(
                    "sandbox.hosting.images.topology.v1", projection.topology.as_mapping()) \
                or self.registry_access_observation["anonymous_exact_manifest"] != "denied" \
                or self.registry_access_observation["authenticated_exact_manifest"] != "succeeded":
            raise StagingContractError("observation_invalid")
        registry = {"anonymous_exact_manifest": "denied",
                    "authenticated_exact_manifest": "succeeded"}
        if self.registry_access_observation["observation_digest"] != staging_digest(
                "sandbox.hosting.images.registry-observation.v1", registry):
            raise StagingContractError("observation_invalid")
        observation_body = {**observed,
            "anonymous_exact_manifest": self.registry_access_observation["anonymous_exact_manifest"],
            "authenticated_exact_manifest": self.registry_access_observation["authenticated_exact_manifest"],
            "observation_digest": self.registry_access_observation["observation_digest"]}
        if self.observation_id != staging_digest(
                "sandbox.hosting.images.local-observation.v1", observation_body):
            raise StagingContractError("observation_invalid")
        if self.proof_digest != staging_digest(
                "sandbox.hosting.images.staged-image-proof.v1", self.body_mapping()):
            raise StagingContractError()

    @classmethod
    def create(cls, request: StageRequest, policy: StagingPolicy,
               observation: LocalImageObservation, generation: int) -> "StagedImageProof":
        projection = request.plan.delivery_identity_projection.as_mapping()
        values = dict(schema_version=1, request_id=request.request_id,
                      request_digest=request.request_digest, plan_digest=request.plan.plan_digest,
                      staging_policy_digest=policy.policy_digest, target=policy.target,
                      helper=policy.helper, delivery_identity_projection=projection,
                      observed_identity=observation.observed_mapping(),
                      registry_access_observation=observation.registry_mapping(),
                      observation_id=observation.observation_id,
                      staging_generation=generation, proof_digest="sha256:" + "0" * 64)
        provisional = cls.__new__(cls)
        for key, value in values.items(): object.__setattr__(provisional, key, value)
        values["proof_digest"] = staging_digest(
            "sandbox.hosting.images.staged-image-proof.v1", provisional.body_mapping())
        return cls(**values)

    @classmethod
    def from_mapping(cls, value: object) -> "StagedImageProof":
        raw = _closed(value, cls.FIELDS)
        request = _closed(raw["request"], frozenset({"request_id", "request_digest"}))
        helper = _closed(raw["helper"], frozenset({"artifact_digest", "runtime_revision",
                                                   "capability_revision"}))
        return cls(raw["schema_version"], request["request_id"], request["request_digest"],
                   raw["plan_digest"], raw["staging_policy_digest"],
                   StagingTarget.from_mapping(raw["target"]),
                   HelperIdentity(helper["artifact_digest"], "sandbox-image-stage-helper-v1",
                                  helper["runtime_revision"], helper["capability_revision"]),
                   raw["delivery_identity_projection"], raw["observed_identity"],
                   raw["registry_access_observation"], raw["observation_id"],
                   raw["staging_generation"], raw["proof_digest"])


def validate_staged_image_proof(value: object) -> StagedImageProof:
    try:
        if type(value) is StagedImageProof:
            return StagedImageProof.from_mapping(value.as_mapping())
        return StagedImageProof.from_mapping(value)
    except ImageContractError:
        raise StagingContractError("proof_invalid") from None


@dataclass(frozen=True, slots=True)
class StageResult:
    schema_version: int
    ok: bool
    result_class: str
    code: str
    request_id: str
    generation: int
    proof: StagedImageProof | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1 \
                or type(self.ok) is not bool \
                or self.result_class not in _RESULT_CLASSES or self.code not in _RESULT_CODES:
            raise StagingContractError()
        _text(self.request_id, identity=True)
        if type(self.generation) is not int \
                or not 0 <= self.generation <= MAX_PERSISTED_LEDGER_COUNTER:
            raise StagingContractError()
        if self.ok != (self.result_class == "success") \
                or (self.ok and type(self.proof) is not StagedImageProof) \
                or (not self.ok and self.proof is not None) \
                or (self.result_class == "in_progress" and self.code not in {"accepted", "in_progress"}):
            raise StagingContractError()

    def as_mapping(self) -> dict[str, Any]:
        result = {"schema_version": 1, "ok": self.ok, "result_class": self.result_class,
                  "code": self.code, "request_id": self.request_id, "generation": self.generation}
        if self.proof is not None: result["proof"] = self.proof.as_mapping()
        return result


@dataclass(frozen=True, slots=True)
class StageProofTombstone:
    request_id: str
    request_digest: str
    proof_digest: str
    result_code: str = "proof_expired"

    def as_mapping(self) -> dict[str, str]:
        return {"request_id": self.request_id, "request_digest": self.request_digest,
                "proof_digest": self.proof_digest, "result_code": self.result_code}


@dataclass(frozen=True, slots=True)
class StageProofActivationLease:
    lease_id: str
    holder: str
    phase: str
    admission_deadline: str
    activation_request_id: str
    activation_request_digest: str
    stage_request_id: str
    stage_request_digest: str
    proof_digest: str
    target_identity: str
    stage_generation: int
    ledger_authority: str
    ledger_revision: int
    acceptance_receipt: str | None = None

    def __post_init__(self) -> None:
        for value in (self.lease_id, self.holder, self.activation_request_id,
                      self.stage_request_id, self.target_identity,
                      self.ledger_authority): _text(value, identity=True)
        if not self.holder.startswith("activation-owner/"):
            raise StagingContractError("holder_mismatch")
        for value in (self.activation_request_digest, self.stage_request_digest,
                      self.proof_digest): _digest(value)
        if self.phase not in {"prepared", "accepted"} \
                or type(self.stage_generation) is not int \
                or not 1 <= self.stage_generation <= MAX_PERSISTED_LEDGER_COUNTER \
                or type(self.ledger_revision) is not int \
                or not 0 <= self.ledger_revision <= MAX_PERSISTED_LEDGER_COUNTER:
            raise StagingContractError("lease_conflict")
        try:
            if type(self.admission_deadline) is not str \
                    or _UTC_DEADLINE.fullmatch(self.admission_deadline) is None:
                raise ValueError
            deadline = datetime.fromisoformat(
                self.admission_deadline[:-1] + "+00:00")
            if deadline.tzinfo != timezone.utc \
                    or deadline.isoformat(timespec="seconds").replace("+00:00", "Z") \
                    != self.admission_deadline:
                raise ValueError
        except (TypeError, ValueError):
            raise StagingContractError("lease_conflict") from None
        if (self.phase == "prepared" and self.acceptance_receipt is not None) \
                or (self.phase == "accepted" and (
                    type(self.acceptance_receipt) is not str
                    or _HOST_ACCEPTANCE_RECEIPT.fullmatch(self.acceptance_receipt) is None)):
            raise StagingContractError("lease_conflict")

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(
            self.admission_deadline.replace("Z", "+00:00"))

    def as_mapping(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class AtomicHostStateEvidence:
    holder: str
    activation_request_id: str
    activation_request_digest: str
    proof_digest: str
    state: str
    acceptance_receipt: str | None
    evidence_digest: str

    def __post_init__(self) -> None:
        _text(self.holder, identity=True); _text(self.activation_request_id, identity=True)
        if not self.holder.startswith("activation-owner/"):
            raise StagingContractError("holder_mismatch")
        _digest(self.activation_request_digest); _digest(self.proof_digest)
        if self.state not in {"accepted", "absent", "ambiguous"}:
            raise StagingContractError("acceptance_ambiguous")
        if (self.state == "accepted") != isinstance(self.acceptance_receipt, str):
            raise StagingContractError("acceptance_ambiguous")
        if self.evidence_digest != staging_digest(
                "sandbox.hosting.images.atomic-host-state-evidence.v1", self.body_mapping()):
            raise StagingContractError("acceptance_ambiguous")

    def body_mapping(self) -> dict[str, Any]:
        return {"holder": self.holder, "activation_request_id": self.activation_request_id,
                "activation_request_digest": self.activation_request_digest,
                "proof_digest": self.proof_digest, "state": self.state,
                "acceptance_receipt": self.acceptance_receipt}


@dataclass(frozen=True, slots=True)
class DurableTerminalAuthorityEvidence:
    holder: str
    proof_digest: str
    acceptance_receipt: str
    terminal_receipt: str
    evidence_digest: str

    def __post_init__(self) -> None:
        _text(self.holder, identity=True); _digest(self.proof_digest)
        if not self.holder.startswith("activation-owner/"):
            raise StagingContractError("holder_mismatch")
        _text(self.acceptance_receipt, identity=True); _text(self.terminal_receipt, identity=True)
        if self.evidence_digest != staging_digest(
                "sandbox.hosting.images.durable-terminal-authority.v1", self.body_mapping()):
            raise StagingContractError("terminal_not_durable")

    def body_mapping(self) -> dict[str, str]:
        return {"holder": self.holder, "proof_digest": self.proof_digest,
                "acceptance_receipt": self.acceptance_receipt,
                "terminal_receipt": self.terminal_receipt}


class ProofCustodyPort:
    """Narrow Feature 051 repository capability; implementations stay private."""

    def lookup(self, lease_id: str) -> StageProofActivationLease | None: raise NotImplementedError
    def validate_retained_proof(self, **_binding: object) -> StagedImageProof: raise NotImplementedError
    def prepare(self, **_binding: object) -> StageProofActivationLease: raise NotImplementedError
    def promote(self, lease: StageProofActivationLease,
                evidence: AtomicHostStateEvidence) -> StageProofActivationLease: raise NotImplementedError
    def cancel(self, lease: StageProofActivationLease,
               evidence: AtomicHostStateEvidence) -> None: raise NotImplementedError
    def release(self, lease: StageProofActivationLease,
                evidence: DurableTerminalAuthorityEvidence) -> None: raise NotImplementedError
