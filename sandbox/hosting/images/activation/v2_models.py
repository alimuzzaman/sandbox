"""Additive, closed values for multi-image activation.

Version 1 remains the only meaning of the classes in ``models.py``.  These
values use separate schema and digest domains so persisted generations can be
dispatched without guessing or conversion.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, ClassVar

from ..plan_set import VerifiedImagePlanSet, validate_verified_image_plan_set
from .models import (
    MAX_SERVICES, ActivationContractError, SECRET_FIELDS,
    VerifiedActivationGeneration, _closed, _digest, _integer, _safe_mapping,
    _text, activation_digest,
)


_ENVIRONMENT_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY_DIGEST = re.compile(
    r"[a-z0-9.]+/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}\Z")


def _local_image_id(value: object, image_ref: object) -> str:
    """Accept the receipt config digest or Docker 29's manifest image ID."""
    if (type(image_ref) is not str
            or _REPOSITORY_DIGEST.fullmatch(image_ref) is None
            or type(value) is not str
            or (value != image_ref and _DIGEST.fullmatch(value) is None)):
        raise ActivationContractError("local_image_mismatch")
    return value


def _target(value: object) -> dict[str, str]:
    raw = _closed(value, frozenset({
        "machine_identity", "target_identity", "daemon_identity"}))
    return {name: _text(raw[name], identity=True) for name in sorted(raw)}


@dataclass(frozen=True, slots=True)
class PrivateComposeInputSnapshotV2:
    """Secret-free authority for one private Compose render.

    ``snapshot_id`` is an opaque machine-local broker identity, not a path or a
    credential.  Only the provider may resolve it.  Secret names and values are
    deliberately absent from this contract.
    """

    schema_version: int
    snapshot_id: str
    provider_revision: str
    target: dict[str, str]
    plan_set_digest: str
    selected_services: tuple[str, ...]
    configuration_digest: str
    expires_at: int
    snapshot_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "snapshot_id", "provider_revision", "target",
        "plan_set_digest", "selected_services", "configuration_digest",
        "expires_at", "snapshot_digest",
    })

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ActivationContractError("policy_mismatch")
        _text(self.snapshot_id, identity=True)
        if not self.snapshot_id.startswith("compose-snapshot/"):
            raise ActivationContractError("policy_mismatch")
        _text(self.provider_revision, identity=True)
        object.__setattr__(self, "target", _target(self.target))
        _digest(self.plan_set_digest); _digest(self.configuration_digest)
        _integer(self.expires_at, minimum=1)
        services = tuple(sorted(self.selected_services))
        if (not services or len(services) > 64
                or services != self.selected_services or len(services) != len(set(services))):
            raise ActivationContractError("policy_mismatch")
        for service in services:
            _text(service, identity=True)
        if self.snapshot_digest != activation_digest(
                "sandbox.hosting.images.private-compose-input-snapshot.v2",
                self.body_mapping()):
            raise ActivationContractError("policy_mismatch")

    def body_mapping(self) -> dict[str, Any]:
        return {"schema_version": 2, "snapshot_id": self.snapshot_id,
                "provider_revision": self.provider_revision, "target": self.target,
                "plan_set_digest": self.plan_set_digest,
                "selected_services": list(self.selected_services),
                "configuration_digest": self.configuration_digest,
                "expires_at": self.expires_at}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "snapshot_digest": self.snapshot_digest}

    @classmethod
    def create(cls, **values: object) -> "PrivateComposeInputSnapshotV2":
        body = {"schema_version": 2, **values}
        return cls(**body, snapshot_digest=activation_digest(
            "sandbox.hosting.images.private-compose-input-snapshot.v2", {
                **body, "selected_services": list(body["selected_services"])}))

    @classmethod
    def from_mapping(cls, value: object) -> "PrivateComposeInputSnapshotV2":
        raw = _closed(value, cls.FIELDS)
        return cls(**{**raw, "selected_services": tuple(raw["selected_services"])})


@dataclass(frozen=True, slots=True)
class ReplacementIntentV2:
    """Secret-free durable identity of the one pending Compose replacement."""

    schema_version: int
    request_digest: str
    generation: int
    plan_set_digest: str
    proof_set_digest: str
    policy_digest: str
    prior_generation_digest: str
    target: dict[str, str]
    compose_project: str
    topology_digest: str
    configuration_digest: str
    compose_snapshot: dict[str, Any]
    images: tuple[dict[str, Any], ...]
    service_image_bindings: tuple[dict[str, Any], ...]
    compose_projection: tuple[dict[str, Any], ...]
    route_digest: str
    replacement_intent_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 2:
            raise ActivationContractError()
        _integer(self.generation, minimum=1)
        object.__setattr__(self, "target", _target(self.target))
        for value in (self.request_digest, self.plan_set_digest, self.proof_set_digest,
                      self.policy_digest, self.prior_generation_digest,
                      self.topology_digest, self.configuration_digest,
                      self.route_digest, self.replacement_intent_digest):
            _digest(value)
        _text(self.compose_project, identity=True)
        snapshot = PrivateComposeInputSnapshotV2.from_mapping(self.compose_snapshot)
        object.__setattr__(self, "compose_snapshot", snapshot.as_mapping())
        if (snapshot.target != self.target
                or snapshot.configuration_digest != self.configuration_digest):
            raise ActivationContractError("topology_mismatch")
        images = {}
        if not 1 <= len(self.images) <= 64:
            raise ActivationContractError()
        for item in self.images:
            row = _closed(item, frozenset({
                "name", "image_ref", "config_digest", "platform", "local_image_id"}))
            _text(row["name"], identity=True); _text(row["image_ref"])
            _digest(row["config_digest"])
            _local_image_id(row["local_image_id"], row["image_ref"])
            if row["platform"] != {"os": "linux", "architecture": "amd64"} \
                    or row["local_image_id"] not in {
                        row["config_digest"], row["image_ref"]}:
                raise ActivationContractError("local_image_mismatch")
            images[row["name"]] = row
        if len(images) != len(self.images):
            raise ActivationContractError()
        bindings = {}
        for item in self.service_image_bindings:
            row = _closed(item, frozenset({
                "service", "image", "image_ref", "environment_variable"}))
            image = images.get(row["image"])
            _text(row["service"], identity=True); _text(row["image"], identity=True)
            if (image is None or row["image_ref"] != image["image_ref"]
                    or type(row["environment_variable"]) is not str
                    or _ENVIRONMENT_VARIABLE.fullmatch(row["environment_variable"]) is None):
                raise ActivationContractError("topology_mismatch")
            bindings[row["service"]] = row
        if (tuple(bindings) != snapshot.selected_services
                or len(bindings) != len(self.service_image_bindings)):
            raise ActivationContractError("topology_mismatch")
        compose = {}
        required = frozenset({
            "service", "image", "build", "pull_policy", "platform", "dependencies",
            "topology_identity", "compose_config_hash", "configuration_digest"})
        for item in self.compose_projection:
            row = _closed(item, required); binding = bindings.get(row["service"])
            if (binding is None or row["image"] != binding["image_ref"]
                    or row["build"] is not None or row["pull_policy"] != "never"
                    or row["platform"] != {"os": "linux", "architecture": "amd64"}
                    or type(row["dependencies"]) is not list
                    or row["dependencies"] != sorted(row["dependencies"])
                    or len(row["dependencies"]) != len(set(row["dependencies"]))
                    or row["topology_identity"] != self.topology_digest):
                raise ActivationContractError("topology_mismatch")
            for dependency in row["dependencies"]:
                _text(dependency, identity=True)
            _digest(row["compose_config_hash"]); _digest(row["configuration_digest"])
            compose[row["service"]] = row
        if tuple(compose) != snapshot.selected_services \
                or len(compose) != len(self.compose_projection):
            raise ActivationContractError("topology_mismatch")
        if self.replacement_intent_digest != activation_digest(
                "sandbox.hosting.images.replacement-intent.v2", self.body_mapping()):
            raise ActivationContractError()

    def body_mapping(self) -> dict[str, Any]:
        return {name: (list(value) if isinstance(value, tuple) else value)
                for name, value in ((key, getattr(self, key))
                    for key in self.__dataclass_fields__
                    if key != "replacement_intent_digest")}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(),
                "replacement_intent_digest": self.replacement_intent_digest}

    @classmethod
    def create(cls, **values: object) -> "ReplacementIntentV2":
        body = {"schema_version": 2, **values}
        canonical = {key: (list(value) if isinstance(value, tuple) else value)
                     for key, value in body.items()}
        return cls(**body, replacement_intent_digest=activation_digest(
            "sandbox.hosting.images.replacement-intent.v2", canonical))

    @classmethod
    def from_mapping(cls, value: object) -> "ReplacementIntentV2":
        raw = _closed(value, frozenset(cls.__dataclass_fields__))
        return cls(**{**raw, "images": tuple(raw["images"]),
                      "service_image_bindings": tuple(raw["service_image_bindings"]),
                      "compose_projection": tuple(raw["compose_projection"])})


def validate_staged_image_proof_set_v2(value: object, plan: VerifiedImagePlanSet) -> dict[str, Any]:
    """Validate Feature 050's canonical v2 proof with no implicit conversion."""
    try:
        from ..staging_v2 import StagedImageProofSet
        proof = value if type(value) is StagedImageProofSet \
            else StagedImageProofSet.from_mapping(value)
    except (ImportError, TypeError, ValueError):
        raise ActivationContractError("artifact_mismatch") from None
    if proof.plan_set_digest != plan.plan_set_digest \
            or proof.verified_plan_set != plan.as_mapping():
        raise ActivationContractError("artifact_mismatch")
    return proof.as_mapping()


@dataclass(frozen=True, slots=True)
class ActivationRequestV2:
    schema_version: int
    request_id: str
    operation: str
    expected_generation: int
    policy_digest: str
    plan_set: VerifiedImagePlanSet
    proof_set: dict[str, Any]
    compose_snapshot: PrivateComposeInputSnapshotV2
    rollback_grant_digest: str
    confirmed: bool
    request_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "request_id", "operation", "expected_generation",
        "policy_digest", "plan_set", "proof_set", "compose_snapshot",
        "rollback_grant_digest", "confirmed", "request_digest",
    })

    def __post_init__(self) -> None:
        if self.schema_version != 2 or self.operation not in {"activate", "rollback"}:
            raise ActivationContractError("request_conflict")
        _text(self.request_id, identity=True); _integer(self.expected_generation)
        _digest(self.policy_digest); _digest(self.rollback_grant_digest); _digest(self.request_digest)
        if type(self.plan_set) is not VerifiedImagePlanSet \
                or type(self.compose_snapshot) is not PrivateComposeInputSnapshotV2 \
                or self.confirmed is not True:
            raise ActivationContractError("confirmation_required")
        proof = validate_staged_image_proof_set_v2(self.proof_set, self.plan_set)
        object.__setattr__(self, "proof_set", proof)
        persistent = tuple(self.plan_set.policy.persistent_services)
        proof_target = self.proof_set["target"]
        if (self.compose_snapshot.plan_set_digest != self.plan_set.plan_set_digest
                or self.compose_snapshot.target != proof_target
                or self.compose_snapshot.selected_services != persistent):
            raise ActivationContractError("artifact_mismatch")
        if self.request_digest != activation_digest(
                "sandbox.hosting.images.activation-request.v2", self.body_mapping()):
            raise ActivationContractError("request_conflict")

    def body_mapping(self) -> dict[str, Any]:
        return {"schema_version": 2, "request_id": self.request_id,
                "operation": self.operation, "expected_generation": self.expected_generation,
                "policy_digest": self.policy_digest, "plan_set": self.plan_set.as_mapping(),
                "proof_set": self.proof_set,
                "compose_snapshot": self.compose_snapshot.as_mapping(),
                "rollback_grant_digest": self.rollback_grant_digest,
                "confirmed": self.confirmed}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "request_digest": self.request_digest}

    @classmethod
    def create(cls, *, request_id: str, operation: str, expected_generation: int,
               policy_digest: str, plan_set: object, proof_set: object,
               compose_snapshot: object, rollback_grant_digest: str,
               confirmed: bool) -> "ActivationRequestV2":
        plan = validate_verified_image_plan_set(plan_set)
        snapshot = (compose_snapshot if type(compose_snapshot) is PrivateComposeInputSnapshotV2
                    else PrivateComposeInputSnapshotV2.from_mapping(compose_snapshot))
        proof = validate_staged_image_proof_set_v2(proof_set, plan)
        body = {"schema_version": 2, "request_id": request_id, "operation": operation,
                "expected_generation": expected_generation, "policy_digest": policy_digest,
                "plan_set": plan.as_mapping(), "proof_set": proof,
                "compose_snapshot": snapshot.as_mapping(), "confirmed": confirmed}
        body["rollback_grant_digest"] = rollback_grant_digest
        return cls(2, request_id, operation, expected_generation, policy_digest, plan,
                   proof, snapshot, rollback_grant_digest, confirmed, activation_digest(
                       "sandbox.hosting.images.activation-request.v2", body))

    @classmethod
    def from_mapping(cls, value: object) -> "ActivationRequestV2":
        raw = _closed(value, cls.FIELDS)
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 2:
            raise ActivationContractError("request_conflict")
        candidate = cls.create(
            request_id=raw["request_id"], operation=raw["operation"],
            expected_generation=raw["expected_generation"],
            policy_digest=raw["policy_digest"], plan_set=raw["plan_set"],
            proof_set=raw["proof_set"], compose_snapshot=raw["compose_snapshot"],
            rollback_grant_digest=raw["rollback_grant_digest"],
            confirmed=raw["confirmed"])
        if candidate.request_digest != raw["request_digest"]:
            raise ActivationContractError("request_conflict")
        return candidate


@dataclass(frozen=True, slots=True)
class RollbackCompatibilityGrantV2:
    schema_version: int
    authority_id: str
    authority_revision: str
    target: dict[str, str]
    expected_generation: int
    prior_generation_digest: str
    candidate_plan_set_digest: str
    candidate_proof_set_digest: str
    policy_digest: str
    issued_at: int
    expires_at: int
    authority_proof: str
    grant_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ActivationContractError("rollback_grant_mismatch")
        _text(self.authority_id, identity=True); _text(self.authority_revision, identity=True)
        object.__setattr__(self, "target", _target(self.target))
        _integer(self.expected_generation); _integer(self.issued_at); _integer(self.expires_at)
        if self.expires_at <= self.issued_at:
            raise ActivationContractError("rollback_grant_mismatch")
        for value in (self.prior_generation_digest, self.candidate_plan_set_digest,
                      self.candidate_proof_set_digest, self.policy_digest, self.grant_digest):
            _digest(value)
        _text(self.authority_proof)
        if self.grant_digest != activation_digest(
                "sandbox.hosting.images.rollback-grant.v2", self.body_mapping()):
            raise ActivationContractError("rollback_grant_mismatch")

    def unsigned_mapping(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__
                if name not in {"authority_proof", "grant_digest"}}

    def body_mapping(self) -> dict[str, Any]:
        return {**self.unsigned_mapping(), "authority_proof": self.authority_proof}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "grant_digest": self.grant_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "RollbackCompatibilityGrantV2":
        return cls(**_closed(value, frozenset(cls.__dataclass_fields__)))


@dataclass(frozen=True, slots=True)
class GenerationBoundEdgeReceiptV2:
    schema_version: int
    request_digest: str
    target: dict[str, str]
    generation: int
    generation_subject_digest: str
    route_digest: str
    observation_digest: str
    terminal: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != 2 or self.terminal is not True:
            raise ActivationContractError("edge_incomplete")
        object.__setattr__(self, "target", _target(self.target))
        _integer(self.generation, minimum=1)
        for value in (self.request_digest, self.generation_subject_digest,
                      self.route_digest, self.observation_digest, self.receipt_digest):
            _digest(value)
        if self.receipt_digest != activation_digest(
                "sandbox.hosting.images.generation-bound-edge-receipt.v2",
                self.body_mapping()):
            raise ActivationContractError("edge_incomplete")

    def body_mapping(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__
                if name != "receipt_digest"}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "receipt_digest": self.receipt_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "GenerationBoundEdgeReceiptV2":
        raw = _closed(value, frozenset(cls.__dataclass_fields__))
        return cls(**raw)


@dataclass(frozen=True, slots=True)
class VerifiedActivationGenerationV2:
    schema_version: int
    generation: int
    plan_set_digest: str
    proof_set_digest: str
    policy_digest: str
    request_digest: str
    target: dict[str, str]
    topology_digest: str
    configuration_digest: str
    compose_snapshot_digest: str
    compose_project: str
    images: tuple[dict[str, Any], ...]
    service_image_bindings: tuple[dict[str, Any], ...]
    compose_projection: tuple[dict[str, Any], ...]
    service_projection: tuple[dict[str, Any], ...]
    running_observation_digest: str
    edge_receipt: dict[str, Any]
    rollback_from_generation_digest: str
    generation_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise ActivationContractError()
        _integer(self.generation, minimum=1)
        object.__setattr__(self, "target", _target(self.target))
        for value in (self.plan_set_digest, self.proof_set_digest, self.policy_digest,
                      self.request_digest, self.topology_digest, self.configuration_digest,
                      self.compose_snapshot_digest, self.running_observation_digest,
                      self.rollback_from_generation_digest, self.generation_digest):
            _digest(value)
        _text(self.compose_project, identity=True)
        if not self.images or len(self.images) > 64:
            raise ActivationContractError()
        images = {}
        for item in self.images:
            row = _closed(item, frozenset({
                "name", "image_ref", "config_digest", "platform", "local_image_id"}))
            _text(row["name"], identity=True); _text(row["image_ref"])
            _digest(row["config_digest"])
            _local_image_id(row["local_image_id"], row["image_ref"])
            if row["local_image_id"] not in {row["config_digest"], row["image_ref"]} \
                    or row["platform"] != {
                    "os": "linux", "architecture": "amd64"}:
                raise ActivationContractError("local_image_mismatch")
            images[row["name"]] = row
        if len(images) != len(self.images):
            raise ActivationContractError()
        bindings = {}
        for item in self.service_image_bindings:
            row = _closed(item, frozenset({
                "service", "image", "image_ref", "environment_variable"}))
            _text(row["service"], identity=True); _text(row["image"], identity=True)
            if _ENVIRONMENT_VARIABLE.fullmatch(row["environment_variable"]) is None \
                    or row["image"] not in images \
                    or row["image_ref"] != images[row["image"]]["image_ref"]:
                raise ActivationContractError("topology_mismatch")
            bindings[row["service"]] = row
        if len(bindings) != len(self.service_image_bindings):
            raise ActivationContractError("topology_mismatch")
        compose = {}
        for item in self.compose_projection:
            row = _safe_mapping(item, forbidden=SECRET_FIELDS)
            service = row.get("service")
            binding = bindings.get(service)
            if (binding is None or row.get("image") != binding["image_ref"]
                    or row.get("build") is not None or row.get("pull_policy") != "never"
                    or row.get("platform") != {"os": "linux", "architecture": "amd64"}
                    or row.get("topology_identity") != self.topology_digest):
                raise ActivationContractError("topology_mismatch")
            _digest(row.get("compose_config_hash")); _digest(row.get("configuration_digest"))
            compose[service] = row
        if set(compose) != set(bindings):
            raise ActivationContractError("topology_mismatch")
        services = {}
        for item in self.service_projection:
            row = _safe_mapping(item, forbidden=SECRET_FIELDS)
            service = row.get("service"); binding = bindings.get(service); image = images.get(
                binding["image"] if binding else None)
            if (binding is None or image is None or row.get("declared_image") != binding["image_ref"]
                    or row.get("repository_digest") != binding["image_ref"]
                    or row.get("local_image_id") != image["local_image_id"]
                    or row.get("config_digest") != image["config_digest"]
                    or row.get("platform") != image["platform"]
                    or row.get("topology_identity") != self.topology_digest
                    or row.get("compose_project") != self.compose_project
                    or row.get("compose_config_hash") != compose[service]["compose_config_hash"]
                    or row.get("healthy") is not True):
                raise ActivationContractError("runtime_mismatch")
            services[service] = row
        if set(services) != set(bindings):
            raise ActivationContractError("runtime_mismatch")
        receipt = GenerationBoundEdgeReceiptV2.from_mapping(self.edge_receipt)
        subject = self.subject_mapping()
        if (receipt.request_digest != self.request_digest or receipt.target != self.target
                or receipt.generation != self.generation
                or receipt.generation_subject_digest != activation_digest(
                    "sandbox.hosting.images.activation-generation-subject.v2", subject)
                or receipt.observation_digest != self.running_observation_digest):
            raise ActivationContractError("edge_incomplete")
        if self.generation_digest != activation_digest(
                "sandbox.hosting.images.activation-generation.v2", self.body_mapping()):
            raise ActivationContractError()

    def subject_mapping(self) -> dict[str, Any]:
        return {name: (list(value) if isinstance(value, tuple) else value)
                for name, value in ((key, getattr(self, key)) for key in (
                    "schema_version", "generation", "plan_set_digest", "proof_set_digest",
                    "policy_digest", "request_digest", "target", "topology_digest",
                    "configuration_digest", "compose_snapshot_digest", "compose_project",
                    "images", "service_image_bindings", "compose_projection",
                    "service_projection", "running_observation_digest",
                    "rollback_from_generation_digest"))}

    def body_mapping(self) -> dict[str, Any]:
        return {**self.subject_mapping(), "edge_receipt": self.edge_receipt}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.body_mapping(), "generation_digest": self.generation_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "VerifiedActivationGenerationV2":
        raw = _closed(value, frozenset(cls.__dataclass_fields__))
        return cls(**{**raw, "images": tuple(raw["images"]),
                      "service_image_bindings": tuple(raw["service_image_bindings"]),
                      "compose_projection": tuple(raw["compose_projection"]),
                      "service_projection": tuple(raw["service_projection"])})


def validate_activation_generation(value: object) -> VerifiedActivationGeneration | VerifiedActivationGenerationV2:
    """Strict persisted-schema dispatch; never guesses and never converts."""
    if type(value) is not dict:
        raise ActivationContractError()
    version = value.get("schema_version", 1)
    if version == 1 and "schema_version" not in value:
        return VerifiedActivationGeneration.from_mapping(value)
    if version == 2:
        return VerifiedActivationGenerationV2.from_mapping(value)
    raise ActivationContractError()


__all__ = (
    "ActivationRequestV2", "GenerationBoundEdgeReceiptV2",
    "PrivateComposeInputSnapshotV2", "ReplacementIntentV2",
    "VerifiedActivationGenerationV2",
    "RollbackCompatibilityGrantV2",
    "validate_activation_generation", "validate_staged_image_proof_set_v2",
)
