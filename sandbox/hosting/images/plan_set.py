"""Additive v2 trust contract for one release containing multiple OCI images.

The v1 single-image contract is deliberately not imported or changed here.  This
module accepts the closed hosted-production receipt shape, verifies every offline
signature through an injected verifier, and emits one canonical, credential-free
plan set.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, ClassVar, Protocol

from .models import TargetScope, canonical_digest, canonical_json


MAX_V2_DOCUMENT_BYTES = 128 * 1024
MAX_V2_BUNDLE_BYTES = 1024 * 1024
MAX_V2_BUNDLE_SET_BYTES = 5 * 1024 * 1024
MAX_SIGSTORE_BUNDLE_DEPTH = 16
MAX_SIGSTORE_BUNDLE_NODES = 16 * 1024
MAX_SIGSTORE_BUNDLE_KEY_BYTES = 256
MAX_SIGSTORE_BUNDLE_STRING_BYTES = 256 * 1024
IMAGE_NAMES = ("queue", "web", "worker")
OCI_MEDIA_TYPES = frozenset({
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
})
SIGNATURE_MODE = "cosign_keyless_offline_bundle_v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SERVICE = re.compile(r"[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?\Z")
_AUTHORITY = re.compile(r"machine-policy/[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_ENVIRONMENT_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_REPOSITORY = re.compile(
    r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+\Z"
)
_SOURCE_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_WORKFLOW_IDENTITY = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/"
    r"[A-Za-z0-9_.-]+\.ya?ml@refs/heads/[A-Za-z0-9._/-]+\Z"
)


class PlanSetContractError(ValueError):
    """Stable refusal; input, subprocess output, and paths never escape."""

    CODES = frozenset({
        "input_invalid", "input_too_large", "policy_mismatch", "receipt_mismatch",
        "signature_invalid", "signature_verifier_unavailable", "plan_set_invalid",
    })

    def __init__(self, code: str = "input_invalid") -> None:
        self.code = code if code in self.CODES else "input_invalid"
        super().__init__(self.code)


def _closed(value: object, fields: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise PlanSetContractError()
    return value


def _text(value: object, *, pattern: re.Pattern[str] | None = None) -> str:
    if (type(value) is not str or not value or len(value) > 512
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise PlanSetContractError()
    if pattern is not None and pattern.fullmatch(value) is None:
        raise PlanSetContractError()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise PlanSetContractError()
    return value


def _load_json_bytes(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes or not data or len(data) > MAX_V2_DOCUMENT_BYTES:
        raise PlanSetContractError("input_too_large")
    try:
        def no_duplicates(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise PlanSetContractError()
                result[key] = item
            return result
        value = json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise PlanSetContractError() from None
    canonical_json(value)
    if type(value) is not dict:
        raise PlanSetContractError()
    return value


def _load_sigstore_bundle_json(data: bytes) -> dict[str, Any]:
    """Parse a bounded Sigstore bundle without policy-document limits.

    Sigstore v0.3 bundles contain certificate and transparency-proof values
    that legitimately exceed the canonical policy model's depth and 512-byte
    string limits. The raw bundle byte cap remains the primary bound; this
    traversal adds structural limits before the offline cosign verifier makes
    the cryptographic trust decision.
    """
    if type(data) is not bytes or not data or len(data) > MAX_V2_BUNDLE_BYTES:
        raise PlanSetContractError("input_too_large")
    try:
        def no_duplicates(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise PlanSetContractError()
                result[key] = item
            return result

        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(PlanSetContractError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise PlanSetContractError() from None
    if type(value) is not dict:
        raise PlanSetContractError()

    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SIGSTORE_BUNDLE_NODES or depth > MAX_SIGSTORE_BUNDLE_DEPTH:
            raise PlanSetContractError("input_too_large")
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if not -(2**63 - 1) <= item <= 2**63 - 1:
                raise PlanSetContractError("input_too_large")
            continue
        if type(item) is str:
            try:
                item_bytes = item.encode("utf-8")
            except UnicodeEncodeError:
                raise PlanSetContractError() from None
            if len(item_bytes) > MAX_SIGSTORE_BUNDLE_STRING_BYTES:
                raise PlanSetContractError("input_too_large")
            continue
        if type(item) is list:
            stack.extend((child, depth + 1) for child in item)
            continue
        if type(item) is dict:
            for key, child in item.items():
                try:
                    key_bytes = key.encode("utf-8") if type(key) is str else b""
                except UnicodeEncodeError:
                    raise PlanSetContractError() from None
                if (type(key) is not str or not key
                        or len(key_bytes) > MAX_SIGSTORE_BUNDLE_KEY_BYTES
                        or any(ord(char) < 32 or ord(char) == 127 for char in key)):
                    raise PlanSetContractError()
                stack.append((child, depth + 1))
            continue
        raise PlanSetContractError()
    return value


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkflowIdentityV2:
    issuer: str
    identity: str
    repository: str
    ref: str
    sha: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({"issuer", "identity", "repository", "ref", "sha"})

    def __post_init__(self) -> None:
        if self.issuer != "https://token.actions.githubusercontent.com":
            raise PlanSetContractError("policy_mismatch")
        _text(self.identity, pattern=_WORKFLOW_IDENTITY)
        _text(self.repository, pattern=_SOURCE_REPOSITORY)
        if not self.ref.startswith("refs/heads/"):
            raise PlanSetContractError()
        _text(self.ref)
        if _SHA.fullmatch(self.sha) is None:
            raise PlanSetContractError()
        expected_identity = (
            f"https://github.com/{self.repository}/.github/workflows/"
            f"prepare-hosted-production-images.yml@{self.ref}"
        )
        if self.identity != expected_identity:
            raise PlanSetContractError("policy_mismatch")

    @classmethod
    def from_mapping(cls, value: object) -> "WorkflowIdentityV2":
        raw = _closed(value, cls.FIELDS)
        return cls(raw["issuer"], raw["identity"], raw["repository"], raw["ref"], raw["sha"])

    def as_mapping(self) -> dict[str, str]:
        return {"issuer": self.issuer, "identity": self.identity,
                "repository": self.repository, "ref": self.ref, "sha": self.sha}


@dataclass(frozen=True, slots=True)
class MachineImagePlanSetPolicy:
    schema_version: int
    authority_id: str
    policy_revision: int
    target_scope: TargetScope
    approved_receipt_digest: str
    source_repository: str
    source_ref: str
    source_revision: str
    platform: str
    workflow: WorkflowIdentityV2
    persistent_services: tuple[str, ...]
    one_shot_services: tuple[str, ...]
    service_image_bindings: tuple[tuple[str, str], ...]
    activation_environment_bindings: tuple[tuple[str, str], ...]
    signature_mode: str
    policy_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "authority_id", "policy_revision", "target_scope",
        "approved_receipt_digest", "source_repository", "source_ref", "source_revision",
        "platform", "workflow", "persistent_services", "one_shot_services",
        "service_image_bindings", "activation_environment_bindings", "signature_mode",
        "policy_digest",
    })

    def __post_init__(self) -> None:
        if (type(self.schema_version) is not int or self.schema_version != 2
                or type(self.policy_revision) is not int or self.policy_revision < 1):
            raise PlanSetContractError("policy_mismatch")
        if type(self.target_scope) is not TargetScope or type(self.workflow) is not WorkflowIdentityV2:
            raise PlanSetContractError("policy_mismatch")
        _text(self.authority_id, pattern=_AUTHORITY)
        _digest(self.approved_receipt_digest)
        _text(self.source_repository, pattern=_SOURCE_REPOSITORY)
        _text(self.source_ref)
        if _SHA.fullmatch(self.source_revision) is None or self.platform != "linux/amd64":
            raise PlanSetContractError("policy_mismatch")
        if (self.workflow.repository != self.source_repository
                or self.workflow.ref != self.source_ref or self.workflow.sha != self.source_revision
                or self.signature_mode != SIGNATURE_MODE):
            raise PlanSetContractError("policy_mismatch")
        persistent = self._services(self.persistent_services, non_empty=True)
        one_shot = self._services(self.one_shot_services, non_empty=False)
        if persistent != self.persistent_services or one_shot != self.one_shot_services \
                or set(persistent) & set(one_shot):
            raise PlanSetContractError("policy_mismatch")
        bindings = tuple(sorted(self.service_image_bindings))
        if (bindings != self.service_image_bindings or len(bindings) != len(set(bindings))
                or len({service for service, _ in bindings}) != len(bindings)):
            raise PlanSetContractError("policy_mismatch")
        if {service for service, _ in bindings} != set(persistent + one_shot):
            raise PlanSetContractError("policy_mismatch")
        if {image for _, image in bindings} != set(IMAGE_NAMES):
            raise PlanSetContractError("policy_mismatch")
        for service, image in bindings:
            _text(service, pattern=_SERVICE)
            if image not in IMAGE_NAMES:
                raise PlanSetContractError("policy_mismatch")
        activation = tuple(sorted(self.activation_environment_bindings))
        if (activation != self.activation_environment_bindings
                or len(activation) != len(set(activation))
                or {image for image, _ in activation} != set(IMAGE_NAMES)
                or len({variable for _, variable in activation}) != len(activation)):
            raise PlanSetContractError("policy_mismatch")
        for image, variable in activation:
            if image not in IMAGE_NAMES:
                raise PlanSetContractError("policy_mismatch")
            _text(variable, pattern=_ENVIRONMENT_VARIABLE)
        if self.policy_digest != canonical_digest(
                "sandbox.hosting.images.machine-plan-set-policy.v2", self.identity_mapping()):
            raise PlanSetContractError("policy_mismatch")

    @staticmethod
    def _services(value: object, *, non_empty: bool) -> tuple[str, ...]:
        if type(value) not in {list, tuple} or (non_empty and not value) or len(value) > 64:
            raise PlanSetContractError("policy_mismatch")
        result = tuple(sorted(_text(item, pattern=_SERVICE) for item in value))
        if len(result) != len(set(result)):
            raise PlanSetContractError("policy_mismatch")
        return result

    def identity_mapping(self) -> dict[str, Any]:
        return {"schema_version": 2, "authority_id": self.authority_id,
                "policy_revision": self.policy_revision, "target_scope": self.target_scope.as_mapping(),
                "approved_receipt_digest": self.approved_receipt_digest,
                "source_repository": self.source_repository, "source_ref": self.source_ref,
                "source_revision": self.source_revision, "platform": self.platform,
                "workflow": self.workflow.as_mapping(),
                "persistent_services": list(self.persistent_services),
                "one_shot_services": list(self.one_shot_services),
                "service_image_bindings": [
                    {"service": service, "image": image} for service, image in self.service_image_bindings],
                "activation_environment_bindings": [
                    {"image": image, "environment_variable": variable}
                    for image, variable in self.activation_environment_bindings],
                "signature_mode": self.signature_mode}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "policy_digest": self.policy_digest}

    @classmethod
    def from_mapping(cls, value: object) -> "MachineImagePlanSetPolicy":
        raw = _closed(value, cls.FIELDS)
        bindings = raw["service_image_bindings"]
        if type(bindings) is not list or len(bindings) > 64:
            raise PlanSetContractError("policy_mismatch")
        pairs = []
        for item in bindings:
            row = _closed(item, frozenset({"service", "image"}))
            pairs.append((row["service"], row["image"]))
        activation_raw = raw["activation_environment_bindings"]
        if type(activation_raw) is not list or len(activation_raw) != len(IMAGE_NAMES):
            raise PlanSetContractError("policy_mismatch")
        activation = []
        for item in activation_raw:
            row = _closed(item, frozenset({"image", "environment_variable"}))
            activation.append((row["image"], row["environment_variable"]))
        return cls(raw["schema_version"], raw["authority_id"], raw["policy_revision"],
                   TargetScope.from_mapping(raw["target_scope"]), raw["approved_receipt_digest"],
                   raw["source_repository"], raw["source_ref"], raw["source_revision"],
                   raw["platform"], WorkflowIdentityV2.from_mapping(raw["workflow"]),
                   cls._services(raw["persistent_services"], non_empty=True),
                   cls._services(raw["one_shot_services"], non_empty=False), tuple(sorted(pairs)),
                   tuple(sorted(activation)), raw["signature_mode"], raw["policy_digest"])


@dataclass(frozen=True, slots=True)
class HostedImageV2:
    name: str
    repository: str
    image_ref: str
    manifest_digest: str
    config_digest: str
    platform: str
    media_type: str
    signature_payload_digest: str
    signature_bundle_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "name", "repository", "image_ref", "manifest_digest", "config_digest", "platform",
        "media_type", "signature_payload_digest", "signature_bundle_digest",
    })

    def __post_init__(self) -> None:
        if self.name not in IMAGE_NAMES or _REPOSITORY.fullmatch(self.repository) is None:
            raise PlanSetContractError("receipt_mismatch")
        for value in (self.manifest_digest, self.config_digest,
                      self.signature_payload_digest, self.signature_bundle_digest):
            _digest(value)
        if self.image_ref != f"{self.repository}@{self.manifest_digest}" \
                or self.platform != "linux/amd64" or self.media_type not in OCI_MEDIA_TYPES:
            raise PlanSetContractError("receipt_mismatch")

    @classmethod
    def from_mapping(cls, value: object) -> "HostedImageV2":
        raw = _closed(value, cls.FIELDS)
        return cls(*(raw[name] for name in (
            "name", "repository", "image_ref", "manifest_digest", "config_digest", "platform",
            "media_type", "signature_payload_digest", "signature_bundle_digest")))

    def as_mapping(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in (
            "name", "repository", "image_ref", "manifest_digest", "config_digest", "platform",
            "media_type", "signature_payload_digest", "signature_bundle_digest")}


@dataclass(frozen=True, slots=True)
class HostedProductionReceiptV1:
    target: str
    platform: str
    source_sha: str
    source_ref: str
    sentry_sha: str
    workflow: WorkflowIdentityV2
    images: tuple[HostedImageV2, ...]

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "target", "platform", "source_sha", "source_ref",
        "sentry_sha", "workflow", "images",
    })

    @classmethod
    def from_mapping(cls, value: object) -> "HostedProductionReceiptV1":
        raw = _closed(value, cls.FIELDS)
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 \
                or raw["target"] != "production" \
                or raw["platform"] != "linux/amd64":
            raise PlanSetContractError("receipt_mismatch")
        workflow = WorkflowIdentityV2.from_mapping(raw["workflow"])
        images_raw = raw["images"]
        if type(images_raw) is not list:
            raise PlanSetContractError("receipt_mismatch")
        images = tuple(HostedImageV2.from_mapping(item) for item in images_raw)
        if tuple(item.name for item in images) != IMAGE_NAMES:
            raise PlanSetContractError("receipt_mismatch")
        if (_SHA.fullmatch(raw["source_sha"]) is None or raw["sentry_sha"] != raw["source_sha"]
                or workflow.sha != raw["source_sha"] or workflow.ref != raw["source_ref"]):
            raise PlanSetContractError("receipt_mismatch")
        return cls(raw["target"], raw["platform"], raw["source_sha"], raw["source_ref"],
                   raw["sentry_sha"], workflow, images)


class OfflineSignatureVerifier(Protocol):
    def verify(self, blob: bytes, bundle: bytes, workflow: WorkflowIdentityV2) -> bool: ...


class CosignOfflineVerifier:
    """No-network, no-shell verifier.  It persists no output or environment."""

    def __init__(self, executable: str | None = None, *, timeout_seconds: int = 30) -> None:
        self.executable = executable or shutil.which("cosign") or ""
        self.timeout_seconds = timeout_seconds

    def verify(self, blob: bytes, bundle: bytes, workflow: WorkflowIdentityV2) -> bool:
        if not self.executable or not Path(self.executable).is_file():
            raise PlanSetContractError("signature_verifier_unavailable")
        try:
            with tempfile.TemporaryDirectory(prefix="sandbox-cosign-") as temp:
                root = Path(temp)
                blob_path = root / "blob"
                bundle_path = root / "bundle.json"
                blob_path.write_bytes(blob)
                bundle_path.write_bytes(bundle)
                argv = [self.executable, "verify-blob", "--offline", "--new-bundle-format",
                        "--bundle", str(bundle_path), "--certificate-identity", workflow.identity,
                        "--certificate-oidc-issuer", workflow.issuer,
                        "--certificate-github-workflow-repository", workflow.repository,
                        "--certificate-github-workflow-ref", workflow.ref,
                        "--certificate-github-workflow-sha", workflow.sha, str(blob_path)]
                completed = subprocess.run(argv, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=self.timeout_seconds, check=False,
                    env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LC_ALL": "C"})
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0


@dataclass(frozen=True, slots=True)
class VerifiedImagePlanSet:
    policy: MachineImagePlanSetPolicy
    receipt_digest: str
    receipt: HostedProductionReceiptV1
    receipt_bundle_digest: str
    plan_set_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "authority", "receipt", "images", "service_image_bindings",
        "activation_environment_bindings", "signature", "plan_set_digest",
    })

    def identity_mapping(self) -> dict[str, Any]:
        images = [item.as_mapping() for item in self.receipt.images]
        by_name = {item.name: item for item in self.receipt.images}
        kinds = {name: "persistent" for name in self.policy.persistent_services}
        kinds.update({name: "one_shot" for name in self.policy.one_shot_services})
        bindings = [{"service": service, "kind": kinds[service], "image": image,
                     "image_ref": by_name[image].image_ref}
                    for service, image in self.policy.service_image_bindings]
        return {"schema_version": 2,
                "authority": {"authority_id": self.policy.authority_id,
                    "policy_revision": self.policy.policy_revision,
                    "policy_digest": self.policy.policy_digest,
                    "target_scope": self.policy.target_scope.as_mapping()},
                "receipt": {"payload_digest": self.receipt_digest,
                    "source_repository": self.receipt.workflow.repository,
                    "source_ref": self.receipt.source_ref,
                    "source_revision": self.receipt.source_sha,
                    "workflow": self.receipt.workflow.as_mapping()},
                "images": images, "service_image_bindings": bindings,
                "activation_environment_bindings": [
                    {"image": image, "environment_variable": variable}
                    for image, variable in self.policy.activation_environment_bindings],
                "signature": {"mode": SIGNATURE_MODE, "receipt_bundle_digest": self.receipt_bundle_digest,
                    "receipt_verified": True, "all_image_payloads_verified": True}}

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "plan_set_digest": self.plan_set_digest}

    @classmethod
    def create(cls, policy: MachineImagePlanSetPolicy, receipt_digest: str,
               receipt: HostedProductionReceiptV1, receipt_bundle_digest: str) -> "VerifiedImagePlanSet":
        provisional = cls(policy, receipt_digest, receipt, receipt_bundle_digest, "sha256:" + "0" * 64)
        return cls(policy, receipt_digest, receipt, receipt_bundle_digest, canonical_digest(
            "sandbox.hosting.images.verified-plan-set.v2", provisional.identity_mapping()))

    @classmethod
    def from_mapping(cls, value: object) -> "VerifiedImagePlanSet":
        raw = _closed(value, cls.FIELDS)
        if raw["schema_version"] != 2:
            raise PlanSetContractError("plan_set_invalid")
        authority = _closed(raw["authority"], frozenset({
            "authority_id", "policy_revision", "policy_digest", "target_scope"}))
        receipt_claim = _closed(raw["receipt"], frozenset({
            "payload_digest", "source_repository", "source_ref", "source_revision", "workflow"}))
        signature = _closed(raw["signature"], frozenset({
            "mode", "receipt_bundle_digest", "receipt_verified", "all_image_payloads_verified"}))
        bindings_raw = raw["service_image_bindings"]
        if type(bindings_raw) is not list:
            raise PlanSetContractError("plan_set_invalid")
        persistent, one_shot, pairs = [], [], []
        for item in bindings_raw:
            row = _closed(item, frozenset({"service", "kind", "image", "image_ref"}))
            if row["kind"] == "persistent":
                persistent.append(row["service"])
            elif row["kind"] == "one_shot":
                one_shot.append(row["service"])
            else:
                raise PlanSetContractError("plan_set_invalid")
            pairs.append((row["service"], row["image"]))
        activation_raw = raw["activation_environment_bindings"]
        if type(activation_raw) is not list:
            raise PlanSetContractError("plan_set_invalid")
        workflow = WorkflowIdentityV2.from_mapping(receipt_claim["workflow"])
        policy_identity = {"schema_version": 2, "authority_id": authority["authority_id"],
            "policy_revision": authority["policy_revision"], "target_scope": authority["target_scope"],
            "approved_receipt_digest": receipt_claim["payload_digest"],
            "source_repository": receipt_claim["source_repository"], "source_ref": receipt_claim["source_ref"],
            "source_revision": receipt_claim["source_revision"], "platform": "linux/amd64",
            "workflow": workflow.as_mapping(), "persistent_services": sorted(persistent),
            "one_shot_services": sorted(one_shot),
            "service_image_bindings": [{"service": s, "image": i} for s, i in sorted(pairs)],
            "activation_environment_bindings": activation_raw,
            "signature_mode": signature["mode"]}
        policy = MachineImagePlanSetPolicy.from_mapping({**policy_identity,
            "policy_digest": authority["policy_digest"]})
        receipt_mapping = {"schema_version": 1, "target": "production", "platform": "linux/amd64",
            "source_sha": receipt_claim["source_revision"], "source_ref": receipt_claim["source_ref"],
            "sentry_sha": receipt_claim["source_revision"], "workflow": workflow.as_mapping(),
            "images": raw["images"]}
        receipt = HostedProductionReceiptV1.from_mapping(receipt_mapping)
        if signature["receipt_verified"] is not True or signature["all_image_payloads_verified"] is not True:
            raise PlanSetContractError("plan_set_invalid")
        plan = cls.create(policy, receipt_claim["payload_digest"], receipt,
                          _digest(signature["receipt_bundle_digest"]))
        if plan.plan_set_digest != raw["plan_set_digest"] or plan.as_mapping() != raw:
            raise PlanSetContractError("plan_set_invalid")
        return plan


def _validate_sigstore_bundle(data: bytes) -> None:
    raw = _load_sigstore_bundle_json(data)
    if set(raw) != {"mediaType", "verificationMaterial", "messageSignature"} \
            or raw["mediaType"] != "application/vnd.dev.sigstore.bundle.v0.3+json" \
            or type(raw["verificationMaterial"]) is not dict or not raw["verificationMaterial"] \
            or type(raw["messageSignature"]) is not dict or not raw["messageSignature"]:
        raise PlanSetContractError("signature_invalid")


def _validate_image_payload(data: bytes, image: HostedImageV2) -> None:
    raw = _load_json_bytes(data)
    _closed(raw, frozenset({"critical", "optional"}))
    critical = _closed(raw["critical"], frozenset({"identity", "image", "type"}))
    identity = _closed(critical["identity"], frozenset({"docker-reference"}))
    image_claim = _closed(critical["image"], frozenset({"docker-manifest-digest"}))
    if (critical["type"] != "cosign container image signature"
            or identity["docker-reference"] != image.repository
            or image_claim["docker-manifest-digest"] != image.manifest_digest
            or type(raw["optional"]) is not dict):
        raise PlanSetContractError("signature_invalid")


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns, value.st_mode, value.st_uid, value.st_nlink)


def _read_fd(fd: int, maximum: int, *, owner_only: bool) -> bytes:
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size < 1 or before.st_size > maximum):
        raise PlanSetContractError("input_invalid" if before.st_size <= maximum
                                   else "input_too_large")
    if owner_only and (before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077):
        raise PlanSetContractError("policy_mismatch")
    chunks, remaining = [], before.st_size
    while remaining:
        chunk = os.read(fd, min(remaining, 64 * 1024))
        if not chunk:
            raise PlanSetContractError()
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1):
        raise PlanSetContractError("input_too_large")
    after = os.fstat(fd)
    if _stat_identity(before) != _stat_identity(after):
        raise PlanSetContractError()
    return b"".join(chunks)


def read_stable_file(path: Path, maximum: int, *, owner_only: bool = False) -> bytes:
    """Read one single-link regular file without following a final symlink."""
    if not isinstance(path, Path):
        raise PlanSetContractError()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise PlanSetContractError() from None
    try:
        return _read_fd(fd, maximum, owner_only=owner_only)
    finally:
        os.close(fd)


def verify_release_bundle(policy_mapping: object, directory: Path,
                          verifier: OfflineSignatureVerifier) -> VerifiedImagePlanSet:
    """Verify one exact directory without credentials, network, or partial success."""
    policy = MachineImagePlanSetPolicy.from_mapping(policy_mapping)
    if not isinstance(directory, Path):
        raise PlanSetContractError()
    directory_flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                       | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0))
    try:
        directory_fd = os.open(directory, directory_flags)
    except OSError:
        raise PlanSetContractError() from None
    before_directory = os.fstat(directory_fd)
    if not stat.S_ISDIR(before_directory.st_mode):
        os.close(directory_fd)
        raise PlanSetContractError()
    expected = {"receipt.json", "receipt.sha256", "receipt.bundle"}
    expected.update(f"{name}.{suffix}" for name in IMAGE_NAMES for suffix in ("payload.json", "bundle"))
    try:
        if set(os.listdir(directory_fd)) != expected:
            raise PlanSetContractError()

        total_bytes = 0

        def read(name: str, maximum: int) -> bytes:
            nonlocal total_bytes
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(name, flags, dir_fd=directory_fd)
            except OSError:
                raise PlanSetContractError() from None
            try:
                data = _read_fd(fd, maximum, owner_only=False)
            finally:
                os.close(fd)
            total_bytes += len(data)
            if total_bytes > MAX_V2_BUNDLE_SET_BYTES:
                raise PlanSetContractError("input_too_large")
            return data

        receipt_bytes = read("receipt.json", MAX_V2_DOCUMENT_BYTES)
        checksum = read("receipt.sha256", 256)
        expected_checksum = f"{hashlib.sha256(receipt_bytes).hexdigest()}  receipt.json\n".encode("ascii")
        receipt_digest = _sha256(receipt_bytes)
        if checksum != expected_checksum or receipt_digest != policy.approved_receipt_digest:
            raise PlanSetContractError("receipt_mismatch")
        receipt = HostedProductionReceiptV1.from_mapping(_load_json_bytes(receipt_bytes))
        if (receipt.workflow != policy.workflow or receipt.source_sha != policy.source_revision
                or receipt.source_ref != policy.source_ref or receipt.platform != policy.platform):
            raise PlanSetContractError("policy_mismatch")
        receipt_bundle = read("receipt.bundle", MAX_V2_BUNDLE_BYTES)
        _validate_sigstore_bundle(receipt_bundle)
        if verifier.verify(receipt_bytes, receipt_bundle, receipt.workflow) is not True:
            raise PlanSetContractError("signature_invalid")
        for image in receipt.images:
            payload = read(f"{image.name}.payload.json", MAX_V2_DOCUMENT_BYTES)
            bundle = read(f"{image.name}.bundle", MAX_V2_BUNDLE_BYTES)
            if _sha256(payload) != image.signature_payload_digest \
                    or _sha256(bundle) != image.signature_bundle_digest:
                raise PlanSetContractError("receipt_mismatch")
            _validate_image_payload(payload, image)
            _validate_sigstore_bundle(bundle)
            if verifier.verify(payload, bundle, receipt.workflow) is not True:
                raise PlanSetContractError("signature_invalid")
        if (_stat_identity(before_directory) != _stat_identity(os.fstat(directory_fd))
                or set(os.listdir(directory_fd)) != expected):
            raise PlanSetContractError()
        return VerifiedImagePlanSet.create(policy, receipt_digest, receipt, _sha256(receipt_bundle))
    finally:
        os.close(directory_fd)


def validate_verified_image_plan_set(value: object) -> VerifiedImagePlanSet:
    if type(value) is VerifiedImagePlanSet:
        value = value.as_mapping()
    return VerifiedImagePlanSet.from_mapping(value)


__all__ = ("CosignOfflineVerifier", "MachineImagePlanSetPolicy", "PlanSetContractError",
           "SIGNATURE_MODE", "VerifiedImagePlanSet", "read_stable_file", "verify_release_bundle",
           "validate_verified_image_plan_set")
