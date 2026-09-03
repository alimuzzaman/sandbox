"""Additive batch-staging contract for a verified multi-image plan set.

The v1 staging values stay closed and unchanged.  This module is selected only
when the public input has ``schema_version == 2``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from .plan_set import PlanSetContractError, VerifiedImagePlanSet
from .staging_models import (
    HelperIdentity, MAX_PERSISTED_LEDGER_COUNTER, StagingContractError,
    StagingTarget, _closed, _digest, _local_image_id, _text, staging_digest,
)


@dataclass(frozen=True, slots=True)
class StagingPolicySet:
    schema_version: int
    policy_digest: str
    plan_set_digest: str
    target: StagingTarget
    helper: HelperIdentity
    broker_recipient: str
    broker_binding_id: str
    broker_binding_version: int
    credential_reference_revision: str
    operation: str
    capability_revision: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "policy_digest", "plan_set_digest", "target", "helper",
        "broker_recipient", "broker_binding_id", "broker_binding_version",
        "credential_reference_revision", "operation", "capability_revision",
    })

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2 \
                or type(self.target) is not StagingTarget \
                or type(self.helper) is not HelperIdentity:
            raise StagingContractError("policy_mismatch")
        _digest(self.plan_set_digest); _digest(self.policy_digest)
        for value in (self.broker_recipient, self.broker_binding_id,
                      self.credential_reference_revision, self.capability_revision):
            _text(value, identity=value != self.broker_recipient)
        if self.broker_recipient != f"ghcr-plan-set-read:{self.plan_set_digest}" \
                or self.operation != "ghcr.plan-set.read" \
                or self.capability_revision != "systemd-cgroup-v2-batch-stage-v2" \
                or self.helper.entry != "sandbox-image-stage-helper-v2" \
                or self.helper.capability_revision != self.capability_revision \
                or type(self.broker_binding_version) is not int \
                or self.broker_binding_version < 1:
            raise StagingContractError("policy_mismatch")
        if self.policy_digest != staging_digest(
                "sandbox.hosting.images.staging-policy-set.v2", self.identity_mapping()):
            raise StagingContractError("policy_mismatch")

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": 2, "plan_set_digest": self.plan_set_digest,
                "target": self.target.as_mapping(), "helper": self.helper.as_mapping(),
                "broker_recipient": self.broker_recipient,
                "broker_binding_id": self.broker_binding_id,
                "broker_binding_version": self.broker_binding_version,
                "credential_reference_revision": self.credential_reference_revision,
                "operation": self.operation,
                "capability_revision": self.capability_revision}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "policy_digest": self.policy_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "StagingPolicySet":
        raw = _closed(value, cls.FIELDS)
        helper = _closed(raw["helper"], frozenset({
            "artifact_digest", "entry", "runtime_revision", "capability_revision"}))
        return cls(raw["schema_version"], raw["policy_digest"], raw["plan_set_digest"],
                   StagingTarget.from_mapping(raw["target"]), HelperIdentity(**helper),
                   raw["broker_recipient"], raw["broker_binding_id"],
                   raw["broker_binding_version"], raw["credential_reference_revision"],
                   raw["operation"], raw["capability_revision"])


@dataclass(frozen=True, slots=True)
class StageRequestSet:
    schema_version: int
    request_id: str
    request_digest: str
    expected_generation: int
    plan_set: VerifiedImagePlanSet
    staging_policy_digest: str
    target: StagingTarget
    confirmed: bool

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2 \
                or type(self.plan_set) is not VerifiedImagePlanSet \
                or type(self.target) is not StagingTarget or self.confirmed is not True:
            raise StagingContractError("plan_invalid")
        _text(self.request_id, identity=True); _digest(self.request_digest)
        _digest(self.staging_policy_digest)
        if type(self.expected_generation) is not int \
                or not 0 <= self.expected_generation <= MAX_PERSISTED_LEDGER_COUNTER:
            raise StagingContractError("generation_conflict")
        if self.request_digest != staging_digest(
                "sandbox.hosting.images.stage-request-set.v2", self.identity_mapping()):
            raise StagingContractError("request_conflict")

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": 2, "request_id": self.request_id,
                "expected_generation": self.expected_generation,
                "plan_set": self.plan_set.as_mapping(),
                "staging_policy_digest": self.staging_policy_digest,
                "target": self.target.as_mapping(), "confirmed": self.confirmed}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "request_digest": self.request_digest}

    @classmethod
    def create(cls, *, request_id: str, expected_generation: int, plan_set: object,
               staging_policy_digest: str, target: StagingTarget,
               confirmed: bool) -> "StageRequestSet":
        try:
            plan = plan_set if type(plan_set) is VerifiedImagePlanSet \
                else VerifiedImagePlanSet.from_mapping(plan_set)
        except PlanSetContractError:
            raise StagingContractError("plan_invalid") from None
        identity = {"schema_version": 2, "request_id": request_id,
                    "expected_generation": expected_generation, "plan_set": plan.as_mapping(),
                    "staging_policy_digest": staging_policy_digest,
                    "target": target.as_mapping(), "confirmed": confirmed}
        return cls(2, request_id, staging_digest(
            "sandbox.hosting.images.stage-request-set.v2", identity), expected_generation,
            plan, staging_policy_digest, target, confirmed)


@dataclass(frozen=True, slots=True)
class BatchImageObservation:
    name: str
    repository: str
    repo_digest: str
    config_digest: str
    platform: str
    local_image_id: str
    anonymous_exact_manifest: str
    authenticated_exact_manifest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "name", "repository", "repo_digest", "config_digest", "platform",
        "local_image_id", "anonymous_exact_manifest", "authenticated_exact_manifest",
    })

    def __post_init__(self) -> None:
        for value in (self.name, self.repository, self.repo_digest, self.platform,
                      self.anonymous_exact_manifest, self.authenticated_exact_manifest):
            _text(value)
        _digest(self.config_digest)
        _local_image_id(self.local_image_id, self.repo_digest)
        # Docker 29's containerd image store may expose the pulled manifest
        # digest (``sha256:…``) as ``.Id``.  Keep the receipt-bound config
        # digest and qualified RepoDigest checks, while accepting that
        # equivalent engine identity without widening the repository ref.
        manifest_digest = self.repo_digest.rsplit("@", 1)[-1]
        if self.local_image_id not in {self.config_digest, manifest_digest, self.repo_digest} \
                or self.anonymous_exact_manifest != "denied" \
                or self.authenticated_exact_manifest != "succeeded":
            raise StagingContractError("observation_invalid")

    def as_mapping(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.FIELDS}

    @classmethod
    def from_mapping(cls, value: object) -> "BatchImageObservation":
        raw = _closed(value, cls.FIELDS)
        return cls(*(raw[name] for name in (
            "name", "repository", "repo_digest", "config_digest", "platform",
            "local_image_id", "anonymous_exact_manifest", "authenticated_exact_manifest")))


@dataclass(frozen=True, slots=True)
class BatchObservation:
    target_epoch_start: str
    target_epoch_end: str
    daemon_epoch_start: str
    daemon_epoch_end: str
    target: StagingTarget
    images: tuple[BatchImageObservation, ...]
    observation_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "target_epoch_start", "target_epoch_end", "daemon_epoch_start", "daemon_epoch_end",
        "target", "images", "observation_digest",
    })

    def __post_init__(self) -> None:
        for value in (self.target_epoch_start, self.target_epoch_end,
                      self.daemon_epoch_start, self.daemon_epoch_end):
            _text(value, identity=True)
        if type(self.target) is not StagingTarget or len(self.images) != 3 \
                or tuple(item.name for item in self.images) != ("queue", "web", "worker") \
                or self.target_epoch_start != self.target_epoch_end \
                or self.daemon_epoch_start != self.daemon_epoch_end \
                or self.target_epoch_start != self.target.machine_identity \
                or self.daemon_epoch_start != self.target.daemon_identity:
            raise StagingContractError("observation_invalid")
        _digest(self.observation_digest)
        if self.observation_digest != staging_digest(
                "sandbox.hosting.images.batch-observation.v2", self.body_mapping()):
            raise StagingContractError("observation_invalid")

    def body_mapping(self) -> dict[str, Any]:
        return {"target_epoch_start": self.target_epoch_start,
                "target_epoch_end": self.target_epoch_end,
                "daemon_epoch_start": self.daemon_epoch_start,
                "daemon_epoch_end": self.daemon_epoch_end,
                "target": self.target.as_mapping(),
                "images": [item.as_mapping() for item in self.images]}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "observation_digest": self.observation_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "BatchObservation":
        raw = _closed(value, cls.FIELDS)
        if type(raw["images"]) is not list:
            raise StagingContractError("observation_invalid")
        return cls(raw["target_epoch_start"], raw["target_epoch_end"],
                   raw["daemon_epoch_start"], raw["daemon_epoch_end"],
                   StagingTarget.from_mapping(raw["target"]),
                   tuple(BatchImageObservation.from_mapping(item) for item in raw["images"]),
                   raw["observation_digest"])


@dataclass(frozen=True, slots=True)
class StagedImageProofSet:
    schema_version: int
    request_id: str
    request_digest: str
    plan_set_digest: str
    staging_policy_digest: str
    target: StagingTarget
    helper: HelperIdentity
    verified_plan_set: dict[str, Any]
    observation: BatchObservation
    staging_generation: int
    proof_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "request", "plan_set_digest", "staging_policy_digest",
        "target", "helper", "verified_plan_set", "observation",
        "staging_generation", "proof_digest",
    })

    def body_mapping(self) -> dict[str, Any]:
        helper = self.helper.as_mapping(); helper.pop("entry")
        return {"schema_version": 2,
                "request": {"request_id": self.request_id,
                            "request_digest": self.request_digest},
                "plan_set_digest": self.plan_set_digest,
                "staging_policy_digest": self.staging_policy_digest,
                "target": self.target.as_mapping(), "helper": helper,
                "verified_plan_set": self.verified_plan_set,
                "observation": self.observation.as_mapping(),
                "staging_generation": self.staging_generation}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "proof_digest": self.proof_digest}

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2 \
                or type(self.target) is not StagingTarget \
                or type(self.helper) is not HelperIdentity \
                or type(self.observation) is not BatchObservation \
                or type(self.staging_generation) is not int \
                or not 1 <= self.staging_generation <= MAX_PERSISTED_LEDGER_COUNTER:
            raise StagingContractError("proof_invalid")
        _text(self.request_id, identity=True)
        for value in (self.request_digest, self.plan_set_digest,
                      self.staging_policy_digest, self.proof_digest):
            _digest(value)
        try:
            plan = VerifiedImagePlanSet.from_mapping(self.verified_plan_set)
        except PlanSetContractError:
            raise StagingContractError("proof_invalid") from None
        expected = {item.name: item for item in plan.receipt.images}
        observed = {item.name: item for item in self.observation.images}
        if plan.plan_set_digest != self.plan_set_digest \
                or self.observation.target != self.target \
                or set(expected) != set(observed):
            raise StagingContractError("proof_invalid")
        for name, image in expected.items():
            item = observed[name]
            if (item.repository, item.repo_digest, item.config_digest, item.platform) != (
                    image.repository, image.image_ref, image.config_digest, image.platform):
                raise StagingContractError("observation_invalid")
        if self.proof_digest != staging_digest(
                "sandbox.hosting.images.staged-image-proof-set.v2", self.body_mapping()):
            raise StagingContractError("proof_invalid")

    @classmethod
    def create(cls, request: StageRequestSet, policy: StagingPolicySet,
               observation: BatchObservation, generation: int) -> "StagedImageProofSet":
        values = dict(schema_version=2, request_id=request.request_id,
            request_digest=request.request_digest, plan_set_digest=request.plan_set.plan_set_digest,
            staging_policy_digest=policy.policy_digest, target=policy.target, helper=policy.helper,
            verified_plan_set=request.plan_set.as_mapping(), observation=observation,
            staging_generation=generation, proof_digest="sha256:" + "0" * 64)
        provisional = cls.__new__(cls)
        for key, value in values.items(): object.__setattr__(provisional, key, value)
        values["proof_digest"] = staging_digest(
            "sandbox.hosting.images.staged-image-proof-set.v2", provisional.body_mapping())
        return cls(**values)

    @classmethod
    def from_mapping(cls, value: object) -> "StagedImageProofSet":
        raw = _closed(value, cls.FIELDS)
        request = _closed(raw["request"], frozenset({"request_id", "request_digest"}))
        helper = _closed(raw["helper"], frozenset({
            "artifact_digest", "runtime_revision", "capability_revision"}))
        return cls(2, request["request_id"], request["request_digest"],
            raw["plan_set_digest"], raw["staging_policy_digest"],
            StagingTarget.from_mapping(raw["target"]),
            HelperIdentity(helper["artifact_digest"], "sandbox-image-stage-helper-v2",
                           helper["runtime_revision"], helper["capability_revision"]),
            raw["verified_plan_set"], BatchObservation.from_mapping(raw["observation"]),
            raw["staging_generation"], raw["proof_digest"])


@dataclass(frozen=True, slots=True)
class StageResultSet:
    schema_version: int
    ok: bool
    result_class: str
    code: str
    request_id: str
    generation: int
    proof: StagedImageProofSet | None = None

    def __post_init__(self) -> None:
        from .staging_models import _RESULT_CLASSES, _RESULT_CODES
        if type(self.schema_version) is not int or self.schema_version != 2 \
                or type(self.ok) is not bool \
                or self.result_class not in _RESULT_CLASSES or self.code not in _RESULT_CODES \
                or type(self.generation) is not int \
                or not 0 <= self.generation <= MAX_PERSISTED_LEDGER_COUNTER \
                or self.ok != (self.result_class == "success") \
                or (self.ok and type(self.proof) is not StagedImageProofSet) \
                or (not self.ok and self.proof is not None):
            raise StagingContractError()
        _text(self.request_id, identity=True)

    def as_mapping(self) -> dict[str, Any]:
        value = {"schema_version": 2, "ok": self.ok,
                 "result_class": self.result_class, "code": self.code,
                 "request_id": self.request_id, "generation": self.generation}
        if self.proof is not None: value["proof"] = self.proof.as_mapping()
        return value


def admit_stage_request_set(request: object, machine_policy: object):
    try:
        if type(request) is not StageRequestSet:
            return None, "plan_invalid"
        policy = machine_policy if type(machine_policy) is StagingPolicySet \
            else StagingPolicySet.from_mapping(machine_policy)
        plan = VerifiedImagePlanSet.from_mapping(request.plan_set.as_mapping())
        if request.staging_policy_digest != policy.policy_digest \
                or plan.plan_set_digest != policy.plan_set_digest:
            return None, "policy_mismatch"
        if request.target != policy.target:
            return None, "target_mismatch"
        scope = plan.policy.target_scope
        if not scope.remote or not scope.environment:
            return None, "target_mismatch"
        if any(image.repository.split("/", 1)[0] != "ghcr.io" for image in plan.receipt.images):
            return None, "policy_mismatch"
        return policy, "admitted"
    except (PlanSetContractError, StagingContractError, TypeError, ValueError):
        return None, "plan_invalid"
