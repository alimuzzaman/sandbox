"""Closed immutable values for effect-free OCI delivery trust decisions.

This module intentionally imports only standard-library value primitives.  It
does not know how to read config files, credentials, registries, hosts, clocks,
processes, Docker, or persistent state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, ClassVar


MAX_DOCUMENT_BYTES = 128 * 1024
MAX_STRING_LENGTH = 512
MAX_SERVICES = 64
MAX_NESTING_DEPTH = 5
MAX_CANONICAL_NODES = 4096
MAX_INTEGER_ABS = 2**63 - 1

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REPOSITORY = re.compile(
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*\Z"
)
_SERVICE = re.compile(r"[a-z0-9](?:[a-z0-9_.-]{0,62}[a-z0-9])?\Z")
_AUTHORITY_ID = re.compile(
    r"machine-policy/[a-z0-9]+(?:[._-][a-z0-9]+)*\Z"
)
_POLICY_SELECTOR = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SCOPE_COMPONENT = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_CANONICAL_DOMAIN = re.compile(r"sandbox(?:\.[a-z0-9-]+)+\.v[0-9]+\Z")
_SOURCE_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_PLATFORM_VALUE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,31}\Z")
OCI_IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


class ImageContractError(ValueError):
    """Stable refusal raised by closed value parsing.

    Only an allowlisted code and schema location are retained.  Raw input and
    parser diagnostics never cross the public boundary.
    """

    def __init__(self, code: str = "input_invalid", location: str = "input") -> None:
        self.code = code if code in REFUSAL_CLASSES else "input_invalid"
        self.location = location if location in SAFE_LOCATIONS else "input"
        super().__init__(self.code)


REFUSAL_CLASSES = frozenset({
    "input_invalid", "input_too_large", "authority_substitution",
    "policy_mismatch", "receipt_mismatch", "provenance_mismatch",
    "image_invalid", "platform_mismatch", "topology_mismatch",
    "signature_mode_unsupported", "plan_invalid",
})

SAFE_LOCATIONS = frozenset({
    "input", "policy", "policy.schema_version", "policy.authority_id",
    "policy.policy_digest", "policy.target_scope", "policy.repository",
    "policy.receipt_payload_digest", "policy.image", "policy.platform",
    "policy.provenance", "policy.topology", "policy.signature_mode",
    "project", "project.schema_version", "project.policy_selector",
    "project.declared_services", "project.persistent_services",
    "project.one_shot_services", "receipt", "receipt.schema_version",
    "receipt.payload_digest", "receipt.repository", "receipt.manifest_digest",
    "receipt.config_digest", "receipt.platform", "receipt.source_repository",
    "receipt.source_revision", "receipt.build_identity", "receipt.provenance",
    "receipt.signature_mode", "plan", "plan.schema_version", "plan.plan_digest",
    "plan.authority", "plan.receipt", "plan.image",
    "plan.delivery_identity_projection", "plan.topology", "plan.signature_mode",
    "legacy",
})


def _fail(code: str = "input_invalid", location: str = "input") -> None:
    raise ImageContractError(code, location)


def _mapping(value: object, location: str) -> dict[str, Any]:
    # Raw channels are JSON-shaped built-ins only.  Refuse subclasses before
    # iteration so adversarial Mapping implementations cannot execute code.
    if type(value) is not dict:
        _fail("input_invalid", location)
    return value


def _closed(value: dict[str, Any], fields: frozenset[str], location: str) -> None:
    if set(value) != fields:
        _fail("input_invalid", location)


def _text(value: object, location: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if (type(value) is not str or not value or len(value) > MAX_STRING_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        _fail("input_invalid", location)
    if pattern is not None and pattern.fullmatch(value) is None:
        _fail("input_invalid", location)
    return value


def _digest(value: object, location: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail("image_invalid", location)
    return value


@dataclass(slots=True)
class _TraversalBudget:
    nodes: int = 0
    byte_count: int = 0

    def add(self, *, nodes: int = 1, byte_count: int = 0) -> None:
        self.nodes += nodes
        self.byte_count += byte_count
        if self.nodes > MAX_CANONICAL_NODES or self.byte_count > MAX_DOCUMENT_BYTES:
            _fail("input_too_large", "input")


def _canonical_value(value: object, *, depth: int = 0,
                     budget: _TraversalBudget | None = None) -> Any:
    budget = _TraversalBudget() if budget is None else budget
    if depth > MAX_NESTING_DEPTH:
        _fail("input_too_large", "input")
    if value is None:
        budget.add(byte_count=4)
        return value
    if type(value) is bool:
        budget.add(byte_count=5)
        return value
    if type(value) is int:
        if not -MAX_INTEGER_ABS <= value <= MAX_INTEGER_ABS:
            _fail("input_too_large", "input")
        text = str(value)
        budget.add(byte_count=len(text))
        return value
    if type(value) is str:
        text = _text(value, "input")
        budget.add(byte_count=len(json.dumps(text, ensure_ascii=False).encode("utf-8")))
        return text
    if type(value) is dict:
        if len(value) > MAX_SERVICES:
            _fail("input_too_large", "input")
        budget.add(byte_count=2 + max(0, len(value) - 1))
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail("input_invalid", "input")
            normalized_key = _text(key, "input")
            budget.add(
                nodes=1,
                byte_count=len(json.dumps(normalized_key, ensure_ascii=False).encode("utf-8")) + 1,
            )
            result[normalized_key] = _canonical_value(
                item, depth=depth + 1, budget=budget)
        return result
    if type(value) is list:
        if len(value) > MAX_SERVICES:
            _fail("input_too_large", "input")
        budget.add(byte_count=2 + max(0, len(value) - 1))
        return [_canonical_value(item, depth=depth + 1, budget=budget) for item in value]
    _fail("input_invalid", "input")


def canonical_json(value: object) -> bytes:
    """Return bounded RFC-8259-compatible canonical JSON bytes."""
    try:
        encoded = json.dumps(
            _canonical_value(value), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
    except ImageContractError:
        raise
    except (TypeError, ValueError, RecursionError):
        raise ImageContractError("input_invalid", "input") from None
    if len(encoded) > MAX_DOCUMENT_BYTES:
        _fail("input_too_large", "input")
    return encoded


def canonical_digest(domain: str, value: object) -> str:
    """Hash canonical bytes with an explicit, non-interchangeable domain."""
    domain_bytes = _text(domain, "input", pattern=_CANONICAL_DOMAIN).encode("ascii")
    return "sha256:" + hashlib.sha256(domain_bytes + b"\0" + canonical_json(value)).hexdigest()


def receipt_payload_digest(value: object) -> str:
    raw = _mapping(value, "receipt")
    if "payload_digest" in raw:
        _fail("authority_substitution", "receipt.payload_digest")
    return canonical_digest("sandbox.hosting.images.release-receipt.v1", raw)


def machine_policy_digest(value: object) -> str:
    raw = _mapping(value, "policy")
    # Traverse and charge the complete raw policy before producing even a
    # shallow digest copy.  The normalized value is bounded and contains only
    # exact built-ins.
    normalized = _canonical_value(raw)
    normalized.pop("policy_digest", None)
    return canonical_digest("sandbox.hosting.images.machine-policy.v1", normalized)


@dataclass(frozen=True, slots=True)
class ProvenanceIdentity:
    """Closed provenance containing only fixed-form opaque SHA-256 identities."""

    builder_id: str
    workflow_id: str
    invocation_id: str
    materials_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "builder_id", "workflow_id", "invocation_id", "materials_digest",
    })

    def __post_init__(self) -> None:
        _digest(self.builder_id, "receipt.provenance")
        _digest(self.workflow_id, "receipt.provenance")
        _digest(self.invocation_id, "receipt.provenance")
        _digest(self.materials_digest, "receipt.provenance")

    @classmethod
    def from_mapping(cls, value: object, location: str = "receipt.provenance") -> "ProvenanceIdentity":
        raw = _mapping(value, location)
        _closed(raw, cls.FIELDS, location)
        return cls(raw["builder_id"], raw["workflow_id"], raw["invocation_id"],
                   raw["materials_digest"])

    def as_mapping(self) -> dict[str, str]:
        return {
            "builder_id": self.builder_id, "workflow_id": self.workflow_id,
            "invocation_id": self.invocation_id,
            "materials_digest": self.materials_digest,
        }


@dataclass(frozen=True, slots=True)
class TargetScope:
    remote: str
    project: str
    environment: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({"remote", "project", "environment"})

    def __post_init__(self) -> None:
        _text(self.remote, "policy.target_scope", pattern=_SCOPE_COMPONENT)
        _text(self.project, "policy.target_scope", pattern=_SCOPE_COMPONENT)
        _text(self.environment, "policy.target_scope", pattern=_SCOPE_COMPONENT)

    @classmethod
    def from_mapping(cls, value: object) -> "TargetScope":
        raw = _mapping(value, "policy.target_scope")
        _closed(raw, cls.FIELDS, "policy.target_scope")
        return cls(raw["remote"], raw["project"], raw["environment"])

    def as_mapping(self) -> dict[str, str]:
        return {"remote": self.remote, "project": self.project, "environment": self.environment}


@dataclass(frozen=True, slots=True)
class Platform:
    os: str
    architecture: str
    variant: str | None = None

    def __post_init__(self) -> None:
        if type(self.os) is not str or self.os != "linux":
            _fail("platform_mismatch", "receipt.platform")
        _text(self.architecture, "receipt.platform", pattern=_PLATFORM_VALUE)
        if self.architecture not in {"amd64", "arm64"}:
            _fail("platform_mismatch", "receipt.platform")
        if self.variant is not None:
            _text(self.variant, "receipt.platform", pattern=_PLATFORM_VALUE)
            if self.architecture != "arm64" or self.variant != "v8":
                _fail("platform_mismatch", "receipt.platform")

    @classmethod
    def from_mapping(cls, value: object, location: str = "receipt.platform") -> "Platform":
        raw = _mapping(value, location)
        fields = frozenset(raw)
        if fields not in {frozenset({"os", "architecture"}), frozenset({"os", "architecture", "variant"})}:
            _fail("input_invalid", location)
        return cls(raw["os"], raw["architecture"], raw.get("variant"))

    def as_mapping(self) -> dict[str, str]:
        result = {"os": self.os, "architecture": self.architecture}
        if self.variant is not None:
            result["variant"] = self.variant
        return result


@dataclass(frozen=True, slots=True)
class OCIImageIdentity:
    registry: str
    repository: str
    manifest_digest: str
    config_digest: str
    platform: Platform
    manifest_media_type: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "registry", "repository", "manifest_digest", "config_digest", "platform",
        "manifest_media_type",
    })

    def __post_init__(self) -> None:
        if type(self.registry) is not str or self.registry != "ghcr.io":
            _fail("image_invalid", "receipt.repository")
        _text(self.repository, "receipt.repository", pattern=_REPOSITORY)
        _digest(self.manifest_digest, "receipt.manifest_digest")
        _digest(self.config_digest, "receipt.config_digest")
        if type(self.platform) is not Platform:
            _fail("platform_mismatch", "receipt.platform")
        if (type(self.manifest_media_type) is not str
                or self.manifest_media_type != OCI_IMAGE_MANIFEST):
            _fail("image_invalid", "receipt.manifest_digest")

    @property
    def repository_qualified_digest(self) -> str:
        return f"{self.registry}/{self.repository}@{self.manifest_digest}"

    @classmethod
    def from_mapping(cls, value: object, location: str = "receipt") -> "OCIImageIdentity":
        raw = _mapping(value, location)
        _closed(raw, cls.FIELDS, location)
        return cls(
            raw["registry"], raw["repository"], raw["manifest_digest"],
            raw["config_digest"], Platform.from_mapping(raw["platform"], f"{location}.platform"),
            raw["manifest_media_type"],
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "registry": self.registry, "repository": self.repository,
            "manifest_digest": self.manifest_digest, "config_digest": self.config_digest,
            "platform": self.platform.as_mapping(), "manifest_media_type": self.manifest_media_type,
        }


def _services(value: object, location: str, *, non_empty: bool) -> tuple[str, ...]:
    if type(value) not in {list, tuple}:
        _fail("topology_mismatch", location)
    if (non_empty and not value) or len(value) > MAX_SERVICES:
        _fail("topology_mismatch", location)
    result = tuple(_text(item, location, pattern=_SERVICE) for item in value)
    if len(set(result)) != len(result):
        _fail("topology_mismatch", location)
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True)
class ApplicationTopology:
    persistent_services: tuple[str, ...]
    one_shot_services: tuple[str, ...]

    FIELDS: ClassVar[frozenset[str]] = frozenset({"persistent_services", "one_shot_services"})

    def __post_init__(self) -> None:
        persistent = _services(self.persistent_services, "project.persistent_services", non_empty=True)
        one_shot = _services(self.one_shot_services, "project.one_shot_services", non_empty=False)
        if persistent != self.persistent_services or one_shot != self.one_shot_services:
            _fail("topology_mismatch", "project")
        if set(persistent) & set(one_shot) or len(persistent) + len(one_shot) > MAX_SERVICES:
            _fail("topology_mismatch", "project")

    @classmethod
    def from_mapping(cls, value: object, location: str = "project") -> "ApplicationTopology":
        raw = _mapping(value, location)
        _closed(raw, cls.FIELDS, location)
        return cls(
            _services(raw["persistent_services"], f"{location}.persistent_services", non_empty=True),
            _services(raw["one_shot_services"], f"{location}.one_shot_services", non_empty=False),
        )

    def as_mapping(self) -> dict[str, list[str]]:
        return {
            "persistent_services": list(self.persistent_services),
            "one_shot_services": list(self.one_shot_services),
        }


@dataclass(frozen=True, slots=True)
class ProjectImageIntent:
    schema_version: int
    policy_selector: str
    declared_services: tuple[str, ...]
    topology: ApplicationTopology

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "policy_selector", "declared_services",
        "persistent_services", "one_shot_services",
    })
    FORBIDDEN_AUTHORITY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "authority_id", "policy_digest", "policy_revision", "target_scope",
        "repository", "receipt_payload_digest", "image", "platform", "provenance",
        "signature_mode", "primary_service", "allowed_persistent_services",
        "allowed_one_shot_services",
    })

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            _fail("input_invalid", "project.schema_version")
        _text(self.policy_selector, "project.policy_selector", pattern=_POLICY_SELECTOR)
        if type(self.topology) is not ApplicationTopology:
            _fail("topology_mismatch", "project")
        declared = _services(self.declared_services, "project.declared_services", non_empty=True)
        if declared != self.declared_services:
            _fail("topology_mismatch", "project.declared_services")
        if (set(self.topology.persistent_services) | set(self.topology.one_shot_services)) - set(declared):
            _fail("topology_mismatch", "project.declared_services")

    @classmethod
    def from_mapping(cls, value: object) -> "ProjectImageIntent":
        raw = _mapping(value, "project")
        if set(raw) & cls.FORBIDDEN_AUTHORITY_FIELDS:
            _fail("authority_substitution", "project")
        _closed(raw, cls.FIELDS, "project")
        topology = ApplicationTopology.from_mapping({
            "persistent_services": raw["persistent_services"],
            "one_shot_services": raw["one_shot_services"],
        })
        return cls(
            raw["schema_version"], raw["policy_selector"],
            _services(raw["declared_services"], "project.declared_services", non_empty=True),
            topology,
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "policy_selector": self.policy_selector,
            "declared_services": list(self.declared_services), **self.topology.as_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ReleaseReceiptPayload:
    schema_version: int
    repository: str
    manifest_digest: str
    config_digest: str
    platform: Platform
    manifest_media_type: str
    source_repository: str
    source_revision: str
    build_identity: str
    provenance: ProvenanceIdentity
    signature_mode: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "repository", "manifest_digest", "config_digest", "platform",
        "manifest_media_type",
        "source_repository", "source_revision", "build_identity", "provenance",
        "signature_mode",
    })
    FORBIDDEN_AUTHORITY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "authority_id", "policy_digest", "policy_revision", "target_scope",
        "allowed_persistent_services", "allowed_one_shot_services", "primary_service",
        "policy_selector", "payload_digest", "plan_digest", "signature", "publisher",
    })

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            _fail("input_invalid", "receipt.schema_version")
        if type(self.platform) is not Platform or type(self.provenance) is not ProvenanceIdentity:
            _fail("input_invalid", "receipt")
        _text(self.repository, "receipt.repository", pattern=_REPOSITORY)
        _digest(self.manifest_digest, "receipt.manifest_digest")
        _digest(self.config_digest, "receipt.config_digest")
        if (type(self.manifest_media_type) is not str
                or self.manifest_media_type != OCI_IMAGE_MANIFEST):
            _fail("image_invalid", "receipt.manifest_digest")
        _text(self.source_repository, "receipt.source_repository", pattern=_REPOSITORY)
        _text(self.source_revision, "receipt.source_revision", pattern=_SOURCE_REVISION)
        _digest(self.build_identity, "receipt.build_identity")
        if type(self.signature_mode) is not str or self.signature_mode != "not_required":
            _fail("signature_mode_unsupported", "receipt.signature_mode")

    @classmethod
    def from_mapping(cls, value: object) -> "ReleaseReceiptPayload":
        raw = _mapping(value, "receipt")
        if set(raw) & cls.FORBIDDEN_AUTHORITY_FIELDS:
            _fail("authority_substitution", "receipt")
        _closed(raw, cls.FIELDS, "receipt")
        return cls(
            raw["schema_version"], raw["repository"], raw["manifest_digest"],
            raw["config_digest"], Platform.from_mapping(raw["platform"]),
            raw["manifest_media_type"],
            raw["source_repository"], raw["source_revision"], raw["build_identity"],
            ProvenanceIdentity.from_mapping(raw["provenance"]), raw["signature_mode"],
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "repository": self.repository,
            "manifest_digest": self.manifest_digest, "config_digest": self.config_digest,
            "platform": self.platform.as_mapping(), "manifest_media_type": self.manifest_media_type,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision, "build_identity": self.build_identity,
            "provenance": self.provenance.as_mapping(), "signature_mode": self.signature_mode,
        }

    @property
    def payload_digest(self) -> str:
        return receipt_payload_digest(self.as_mapping())


@dataclass(frozen=True, slots=True)
class ReleaseReceipt:
    payload: ReleaseReceiptPayload
    claimed_payload_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({"payload", "payload_digest"})

    def __post_init__(self) -> None:
        if type(self.payload) is not ReleaseReceiptPayload:
            _fail("input_invalid", "receipt")
        _digest(self.claimed_payload_digest, "receipt.payload_digest")

    @classmethod
    def from_mapping(cls, value: object) -> "ReleaseReceipt":
        raw = _mapping(value, "receipt")
        outer_forbidden = ReleaseReceiptPayload.FORBIDDEN_AUTHORITY_FIELDS - {"payload_digest"}
        if set(raw) & outer_forbidden:
            _fail("authority_substitution", "receipt")
        _closed(raw, cls.FIELDS, "receipt")
        return cls(ReleaseReceiptPayload.from_mapping(raw["payload"]), raw["payload_digest"])

    def as_mapping(self) -> dict[str, Any]:
        return {"payload": self.payload.as_mapping(), "payload_digest": self.claimed_payload_digest}


@dataclass(frozen=True, slots=True)
class MachineTrustPolicy:
    schema_version: int
    authority_id: str
    policy_selector: str
    policy_revision: int
    policy_digest: str
    target_scope: TargetScope
    repository: str
    approved_receipt_payload_digest: str
    image: OCIImageIdentity
    source_repository: str
    source_revision: str
    build_identity: str
    provenance: ProvenanceIdentity
    signature_mode: str
    primary_service: str
    allowed_persistent_services: tuple[str, ...]
    allowed_one_shot_services: tuple[str, ...]

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "authority_id", "policy_selector", "policy_revision",
        "policy_digest", "target_scope", "repository", "approved_receipt_payload_digest",
        "image", "source_repository", "source_revision", "build_identity", "provenance",
        "signature_mode", "primary_service", "allowed_persistent_services",
        "allowed_one_shot_services",
    })

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            _fail("input_invalid", "policy.schema_version")
        if (type(self.target_scope) is not TargetScope
                or type(self.image) is not OCIImageIdentity
                or type(self.provenance) is not ProvenanceIdentity):
            _fail("input_invalid", "policy")
        _text(self.authority_id, "policy.authority_id", pattern=_AUTHORITY_ID)
        _text(self.policy_selector, "policy", pattern=_POLICY_SELECTOR)
        if type(self.policy_revision) is not int or not 1 <= self.policy_revision <= 2**31 - 1:
            _fail("input_invalid", "policy")
        _digest(self.policy_digest, "policy.policy_digest")
        _text(self.repository, "policy.repository", pattern=_REPOSITORY)
        _digest(self.approved_receipt_payload_digest, "policy.receipt_payload_digest")
        if self.repository != self.image.repository:
            _fail("policy_mismatch", "policy.repository")
        _text(self.source_repository, "policy.provenance", pattern=_REPOSITORY)
        _text(self.source_revision, "policy.provenance", pattern=_SOURCE_REVISION)
        _digest(self.build_identity, "policy.provenance")
        if type(self.signature_mode) is not str or self.signature_mode != "not_required":
            _fail("signature_mode_unsupported", "policy.signature_mode")
        primary = _text(self.primary_service, "policy.topology", pattern=_SERVICE)
        persistent = _services(self.allowed_persistent_services, "policy.topology", non_empty=True)
        one_shot = _services(self.allowed_one_shot_services, "policy.topology", non_empty=False)
        if persistent != self.allowed_persistent_services or one_shot != self.allowed_one_shot_services:
            _fail("topology_mismatch", "policy.topology")
        if primary not in persistent or set(persistent) & set(one_shot):
            _fail("topology_mismatch", "policy.topology")
        if self.policy_digest != machine_policy_digest(self.identity_mapping()):
            _fail("policy_mismatch", "policy.policy_digest")

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "authority_id": self.authority_id,
            "policy_selector": self.policy_selector, "policy_revision": self.policy_revision,
            "target_scope": self.target_scope.as_mapping(), "repository": self.repository,
            "approved_receipt_payload_digest": self.approved_receipt_payload_digest,
            "image": self.image.as_mapping(), "source_repository": self.source_repository,
            "source_revision": self.source_revision, "build_identity": self.build_identity,
            "provenance": self.provenance.as_mapping(), "signature_mode": self.signature_mode,
            "primary_service": self.primary_service,
            "allowed_persistent_services": list(self.allowed_persistent_services),
            "allowed_one_shot_services": list(self.allowed_one_shot_services),
        }

    @classmethod
    def from_mapping(cls, value: object) -> "MachineTrustPolicy":
        raw = _mapping(value, "policy")
        _closed(raw, cls.FIELDS, "policy")
        return cls(
            raw["schema_version"], raw["authority_id"], raw["policy_selector"],
            raw["policy_revision"], raw["policy_digest"], TargetScope.from_mapping(raw["target_scope"]),
            raw["repository"], raw["approved_receipt_payload_digest"],
            OCIImageIdentity.from_mapping(raw["image"], "policy.image"),
            raw["source_repository"], raw["source_revision"], raw["build_identity"],
            ProvenanceIdentity.from_mapping(raw["provenance"], "policy.provenance"),
            raw["signature_mode"], raw["primary_service"],
            _services(raw["allowed_persistent_services"], "policy.topology", non_empty=True),
            _services(raw["allowed_one_shot_services"], "policy.topology", non_empty=False),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "policy_digest": self.policy_digest}


def _machine_policy_channel_contract():
    """Create a private token type whose construction key remains closure-owned."""
    construction_capability = object()

    @dataclass(frozen=True, slots=True, init=False)
    class _TrustedMachinePolicy:
        policy: MachineTrustPolicy

        def __init__(self, policy: MachineTrustPolicy, capability: object = None) -> None:
            if capability is not construction_capability or type(policy) is not MachineTrustPolicy:
                _fail("input_invalid", "policy")
            object.__setattr__(self, "policy", policy)

    def issue(value: object) -> _TrustedMachinePolicy:
        return _TrustedMachinePolicy(
            MachineTrustPolicy.from_mapping(value), construction_capability)

    return _TrustedMachinePolicy, issue


_TrustedMachinePolicy, _issue_trusted_machine_policy = _machine_policy_channel_contract()


@dataclass(frozen=True, slots=True)
class DeliveryIdentityProjection:
    target_scope: TargetScope
    image: OCIImageIdentity
    topology: ApplicationTopology
    intended_visibility: str = "private"

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "target_scope", "registry", "repository", "repository_qualified_digest",
        "manifest_digest", "config_digest", "manifest_media_type", "platform", "topology",
        "intended_visibility",
    })

    def __post_init__(self) -> None:
        if (type(self.target_scope) is not TargetScope
                or type(self.image) is not OCIImageIdentity
                or type(self.topology) is not ApplicationTopology):
            _fail("plan_invalid", "plan.delivery_identity_projection")
        if (type(self.intended_visibility) is not str
                or self.intended_visibility != "private"):
            _fail("plan_invalid", "plan.delivery_identity_projection")

    @classmethod
    def from_mapping(cls, value: object) -> "DeliveryIdentityProjection":
        raw = _mapping(value, "plan.delivery_identity_projection")
        _closed(raw, cls.FIELDS, "plan.delivery_identity_projection")
        image = OCIImageIdentity.from_mapping({
            "registry": raw["registry"], "repository": raw["repository"],
            "manifest_digest": raw["manifest_digest"], "config_digest": raw["config_digest"],
            "platform": raw["platform"], "manifest_media_type": raw["manifest_media_type"],
        }, "plan.delivery_identity_projection")
        if raw["repository_qualified_digest"] != image.repository_qualified_digest:
            _fail("plan_invalid", "plan.delivery_identity_projection")
        return cls(
            TargetScope.from_mapping(raw["target_scope"]), image,
            ApplicationTopology.from_mapping(raw["topology"], "plan.delivery_identity_projection"),
            raw["intended_visibility"],
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "target_scope": self.target_scope.as_mapping(), "registry": self.image.registry,
            "repository": self.image.repository,
            "repository_qualified_digest": self.image.repository_qualified_digest,
            "manifest_digest": self.image.manifest_digest, "config_digest": self.image.config_digest,
            "manifest_media_type": self.image.manifest_media_type,
            "platform": self.image.platform.as_mapping(), "topology": self.topology.as_mapping(),
            "intended_visibility": self.intended_visibility,
        }

    @property
    def service_image_bindings(self) -> tuple[tuple[str, str], ...]:
        """Derived exact binding of every selected service to the one image."""
        services = self.topology.persistent_services + self.topology.one_shot_services
        return tuple(
            (service, self.image.repository_qualified_digest)
            for service in sorted(services)
        )


@dataclass(frozen=True, slots=True)
class VerifiedImagePlan:
    schema_version: int
    authority_id: str
    policy_revision: int
    policy_digest: str
    target_scope: TargetScope
    delivery_identity_projection: DeliveryIdentityProjection
    receipt_payload_digest: str
    source_repository: str
    source_revision: str
    build_identity: str
    provenance: ProvenanceIdentity
    image: OCIImageIdentity
    topology: ApplicationTopology
    signature_mode: str
    plan_digest: str

    FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema_version", "authority", "receipt", "image",
        "delivery_identity_projection", "topology", "signature_mode", "plan_digest",
    })
    AUTHORITY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "authority_id", "policy_revision", "policy_digest", "target_scope",
    })
    RECEIPT_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "payload_digest", "source_repository", "source_revision", "build_identity", "provenance",
    })

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            _fail("plan_invalid", "plan.schema_version")
        if (type(self.target_scope) is not TargetScope
                or type(self.delivery_identity_projection) is not DeliveryIdentityProjection
                or type(self.provenance) is not ProvenanceIdentity
                or type(self.image) is not OCIImageIdentity
                or type(self.topology) is not ApplicationTopology):
            _fail("plan_invalid", "plan")
        _text(self.authority_id, "plan.authority", pattern=_AUTHORITY_ID)
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            _fail("plan_invalid", "plan.authority")
        _digest(self.policy_digest, "plan.authority")
        _digest(self.receipt_payload_digest, "plan.receipt")
        _text(self.source_repository, "plan.receipt", pattern=_REPOSITORY)
        _text(self.source_revision, "plan.receipt", pattern=_SOURCE_REVISION)
        _digest(self.build_identity, "plan.receipt")
        if type(self.signature_mode) is not str or self.signature_mode != "not_required":
            _fail("plan_invalid", "plan.signature_mode")
        if (self.delivery_identity_projection.target_scope != self.target_scope
                or self.delivery_identity_projection.image != self.image
                or self.delivery_identity_projection.topology != self.topology):
            _fail("plan_invalid", "plan.delivery_identity_projection")
        _digest(self.plan_digest, "plan.plan_digest")
        if self.plan_digest != canonical_digest(
                "sandbox.hosting.images.verified-plan.v1", self.identity_mapping()):
            _fail("plan_invalid", "plan.plan_digest")

    def identity_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authority": {
                "authority_id": self.authority_id, "policy_revision": self.policy_revision,
                "policy_digest": self.policy_digest, "target_scope": self.target_scope.as_mapping(),
            },
            "receipt": {
                "payload_digest": self.receipt_payload_digest,
                "source_repository": self.source_repository,
                "source_revision": self.source_revision, "build_identity": self.build_identity,
                "provenance": self.provenance.as_mapping(),
            },
            "image": self.image.as_mapping(),
            "delivery_identity_projection": self.delivery_identity_projection.as_mapping(),
            "topology": self.topology.as_mapping(), "signature_mode": self.signature_mode,
        }

    def as_mapping(self) -> dict[str, Any]:
        return {**self.identity_mapping(), "plan_digest": self.plan_digest}

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.as_mapping())

    @classmethod
    def verified(
        cls, *, policy: MachineTrustPolicy, receipt: ReleaseReceiptPayload,
        topology: ApplicationTopology,
    ) -> "VerifiedImagePlan":
        projection = DeliveryIdentityProjection(policy.target_scope, policy.image, topology)
        values = {
            "schema_version": 1, "authority_id": policy.authority_id,
            "policy_revision": policy.policy_revision, "policy_digest": policy.policy_digest,
            "target_scope": policy.target_scope, "delivery_identity_projection": projection,
            "receipt_payload_digest": receipt.payload_digest,
            "source_repository": receipt.source_repository,
            "source_revision": receipt.source_revision, "build_identity": receipt.build_identity,
            "provenance": receipt.provenance, "image": policy.image, "topology": topology,
            "signature_mode": "not_required",
        }
        identity = {
            "schema_version": 1,
            "authority": {
                "authority_id": policy.authority_id, "policy_revision": policy.policy_revision,
                "policy_digest": policy.policy_digest, "target_scope": policy.target_scope.as_mapping(),
            },
            "receipt": {
                "payload_digest": receipt.payload_digest,
                "source_repository": receipt.source_repository,
                "source_revision": receipt.source_revision, "build_identity": receipt.build_identity,
                "provenance": receipt.provenance.as_mapping(),
            },
            "image": policy.image.as_mapping(),
            "delivery_identity_projection": projection.as_mapping(),
            "topology": topology.as_mapping(), "signature_mode": "not_required",
        }
        return cls(**values, plan_digest=canonical_digest(
            "sandbox.hosting.images.verified-plan.v1", identity))

    @classmethod
    def from_mapping(cls, value: object) -> "VerifiedImagePlan":
        raw = _mapping(value, "plan")
        presented = canonical_json(raw)
        _closed(raw, cls.FIELDS, "plan")
        authority = _mapping(raw["authority"], "plan.authority")
        receipt = _mapping(raw["receipt"], "plan.receipt")
        _closed(authority, cls.AUTHORITY_FIELDS, "plan.authority")
        _closed(receipt, cls.RECEIPT_FIELDS, "plan.receipt")
        plan = cls(
            raw["schema_version"], authority["authority_id"], authority["policy_revision"],
            authority["policy_digest"], TargetScope.from_mapping(authority["target_scope"]),
            DeliveryIdentityProjection.from_mapping(raw["delivery_identity_projection"]),
            receipt["payload_digest"], receipt["source_repository"], receipt["source_revision"],
            receipt["build_identity"], ProvenanceIdentity.from_mapping(receipt["provenance"]),
            OCIImageIdentity.from_mapping(raw["image"], "plan.image"),
            ApplicationTopology.from_mapping(raw["topology"], "plan.topology"),
            raw["signature_mode"], raw["plan_digest"],
        )
        if canonical_json(plan.as_mapping()) != presented:
            _fail("plan_invalid", "plan")
        return plan


def validate_verified_image_plan(value: object) -> VerifiedImagePlan:
    """Validate a complete consumer envelope without re-running trust policy."""
    if type(value) is VerifiedImagePlan:
        # Reparse the canonical public envelope so callers cannot bypass the
        # same whole-contract validation by handing us an object instance.
        value = value.as_mapping()
    return VerifiedImagePlan.from_mapping(value)


__all__ = (
    "ApplicationTopology", "DeliveryIdentityProjection", "ProvenanceIdentity",
    "ImageContractError", "OCIImageIdentity", "Platform",
    "ProjectImageIntent", "ReleaseReceipt", "ReleaseReceiptPayload", "TargetScope",
    "VerifiedImagePlan", "canonical_digest", "canonical_json", "machine_policy_digest",
    "receipt_payload_digest",
    "validate_verified_image_plan",
)
