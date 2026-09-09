"""Pure, content-free value objects for instance server configuration.

The repository, adapters, and runtime gateway own all I/O.  These models keep
durable identities deterministic, state transitions explicit, and routine
projections free of fragment bytes and private locators.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
import re
import secrets
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_INCARNATION = re.compile(r"^inc_[0-9a-f]{32}$")
_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_BOUNDED_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_FRAGMENT_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,63}$")
_SERVER_MARKER = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_JSON_INTEGER = 9_007_199_254_740_991


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError("canonical identity contains an unsupported value")


def _digest(kind: str, payload: Mapping[str, Any]) -> str:
    envelope = {"kind": kind, "schema": 1, "value": _canonical_value(payload)}
    encoded = json.dumps(
        envelope, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _require_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("%s must be a lowercase SHA-256 digest" % field_name)


def _require_optional_digest(value: Optional[str], field_name: str) -> None:
    if value is not None:
        _require_digest(value, field_name)


def _require_incarnation(value: str) -> None:
    if not isinstance(value, str) or _INCARNATION.fullmatch(value) is None:
        raise ValueError("instance_incarnation_id has an invalid format")


def _require_image_id(value: str, field_name: str = "image_id") -> None:
    if not isinstance(value, str) or _IMAGE_ID.fullmatch(value) is None:
        raise ValueError("%s must be a content-addressed SHA-256 image ID" % field_name)


def _require_timestamp(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("%s must be a timezone-aware timestamp" % field_name)


def _require_code(value: str, field_name: str = "code") -> None:
    if not isinstance(value, str) or _BOUNDED_CODE.fullmatch(value) is None:
        raise ValueError("%s is not a bounded code" % field_name)


class ServerType(str, Enum):
    NGINX = "nginx"
    LITESPEED = "litespeed"


class RuntimeMode(str, Enum):
    LOCAL_COMPOSE = "local_compose"


class Readiness(str, Enum):
    READY = "ready"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class Operation(str, Enum):
    APPLY = "apply"
    REVERT = "revert"


class TransactionPhase(str, Enum):
    REQUESTED = "requested"
    PREPARED = "prepared"
    VALIDATED = "validated"
    ACTIVATING = "activating"
    RELOADING = "reloading"
    OBSERVING_READY = "observing_ready"
    COMMITTED = "committed"
    RESTORING_PRIOR = "restoring_prior"
    RECOVERY_RELOADING = "recovery_reloading"
    RECOVERY_OBSERVING_READY = "recovery_observing_ready"


class TerminalOutcome(str, Enum):
    ACTIVE = "active"
    NO_OP = "no_op"
    REFUSED = "refused"
    ROLLED_BACK = "rolled_back"
    CONFLICT = "conflict"
    RECOVERY_NEEDED = "recovery_needed"


class InspectionState(str, Enum):
    HEALTHY = "healthy"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    RECOVERY_NEEDED = "recovery_needed"
    UNSUPPORTED = "unsupported"
    ABSENT = "absent"


@dataclass(frozen=True)
class InstanceIdentityProjection:
    """Opaque registry projection, including a reversible mount candidate."""

    instance_incarnation_id: Optional[str]
    server_config_mount_id: Optional[str]
    prior_server_config_mount_id: Optional[str] = None
    mount_update_staged: bool = False

    def __post_init__(self) -> None:
        if self.instance_incarnation_id is not None:
            _require_incarnation(self.instance_incarnation_id)
        _require_optional_digest(self.server_config_mount_id, "server_config_mount_id")
        _require_optional_digest(
            self.prior_server_config_mount_id, "prior_server_config_mount_id"
        )
        if self.instance_incarnation_id is None and (
            self.server_config_mount_id is not None
            or self.prior_server_config_mount_id is not None
            or self.mount_update_staged
        ):
            raise ValueError("legacy identity cannot own a server-config mount")
        if not isinstance(self.mount_update_staged, bool):
            raise ValueError("mount_update_staged must be boolean")

    @classmethod
    def for_new_instance(
        cls, *, random_bytes: Callable[[int], bytes] = secrets.token_bytes
    ) -> "InstanceIdentityProjection":
        token = random_bytes(16)
        if not isinstance(token, bytes) or len(token) != 16:
            raise ValueError("incarnation entropy source must return exactly 16 bytes")
        return cls("inc_" + token.hex(), None)

    @classmethod
    def from_existing_record(cls, record: Mapping[str, Any]) -> "InstanceIdentityProjection":
        if not isinstance(record, Mapping):
            raise ValueError("instance identity record must be a mapping")
        return cls(
            record.get("instance_incarnation_id"),
            record.get("server_config_mount_id"),
        )

    @property
    def is_legacy(self) -> bool:
        return self.instance_incarnation_id is None

    @property
    def is_attached(self) -> bool:
        return self.instance_incarnation_id is not None and self.server_config_mount_id is not None

    @property
    def can_mutate(self) -> bool:
        return self.is_attached

    def preserve_for_update(self) -> "InstanceIdentityProjection":
        return replace(self)

    def stage_mount(self, mount_id: str) -> "InstanceIdentityProjection":
        if self.is_legacy:
            raise ValueError("legacy instance cannot adopt a server-config identity")
        _require_digest(mount_id, "server_config_mount_id")
        return InstanceIdentityProjection(
            self.instance_incarnation_id,
            mount_id,
            self.server_config_mount_id,
            True,
        )

    def commit_mount(self) -> "InstanceIdentityProjection":
        return InstanceIdentityProjection(
            self.instance_incarnation_id, self.server_config_mount_id
        )

    def rollback_mount(self) -> "InstanceIdentityProjection":
        if not self.mount_update_staged:
            raise ValueError("no staged mount can be rolled back")
        return InstanceIdentityProjection(
            self.instance_incarnation_id, self.prior_server_config_mount_id
        )

    def for_recreated_instance(
        self, *, random_bytes: Callable[[int], bytes] = secrets.token_bytes
    ) -> "InstanceIdentityProjection":
        return type(self).for_new_instance(random_bytes=random_bytes)


@dataclass(frozen=True)
class InstanceConfigAuthority:
    instance_name: str
    instance_incarnation_id: Optional[str]
    project_identity: str
    server_type: Optional[ServerType]
    runtime_mode: Optional[RuntimeMode]
    server_config_mount_id: Optional[str]
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.instance_name, str) or not self.instance_name:
            raise ValueError("instance_name is required")
        if self.instance_incarnation_id is not None:
            _require_incarnation(self.instance_incarnation_id)
        if self.server_type is not None and not isinstance(self.server_type, ServerType):
            raise ValueError("server_type is unsupported")
        if self.runtime_mode is not None and not isinstance(self.runtime_mode, RuntimeMode):
            raise ValueError("runtime_mode is unsupported")
        _require_optional_digest(self.server_config_mount_id, "server_config_mount_id")
        if not isinstance(self.project_identity, str) or not self.project_identity:
            raise ValueError("project_identity is required")
        _require_code(self.status, "status")

    @property
    def supports_mutation(self) -> bool:
        return (
            self.instance_incarnation_id is not None
            and self.server_config_mount_id is not None
            and self.server_type in (ServerType.NGINX, ServerType.LITESPEED)
            and self.runtime_mode is RuntimeMode.LOCAL_COMPOSE
            and self.status == "ready"
        )


@dataclass(frozen=True, repr=False)
class ServerConfigFragment:
    name: str
    authority: str
    server_type: ServerType
    content_id: str
    content_size: int
    content_locator: str = field(repr=False)
    instance_incarnation_id: str
    created_at: datetime
    activated_at: Optional[datetime]
    policy_revision: str
    _raw_content: Optional[bytes] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _FRAGMENT_NAME.fullmatch(self.name) is None:
            raise ValueError("fragment name is invalid")
        if not isinstance(self.server_type, ServerType):
            raise ValueError("fragment server is unsupported")
        _require_digest(self.content_id, "content_id")
        _require_incarnation(self.instance_incarnation_id)
        _require_timestamp(self.created_at, "created_at")
        if self.activated_at is not None:
            _require_timestamp(self.activated_at, "activated_at")
        if not isinstance(self.content_size, int) or isinstance(self.content_size, bool):
            raise ValueError("content_size must be an integer")
        if not 1 <= self.content_size <= 262144:
            raise ValueError("content_size is outside the supported bound")
        if self.authority != "wordpress-cache-v1":
            raise ValueError("fragment authority is unsupported")
        if not isinstance(self.content_locator, str) or not self.content_locator:
            raise ValueError("content_locator is required")
        locator = PurePosixPath(self.content_locator)
        expected_locator = (
            "fragments/" + self.content_id.removeprefix("sha256:") + ".fragment"
        )
        if (
            locator.is_absolute()
            or not locator.parts
            or locator.parts[0] != "fragments"
            or any(part in {"", ".", ".."} for part in locator.parts)
            or "\\" in self.content_locator
            or self.content_locator != expected_locator
        ):
            raise ValueError("content_locator is required")
        if (
            not isinstance(self.policy_revision, str)
            or not self.policy_revision
            or len(self.policy_revision) > 128
        ):
            raise ValueError("policy_revision is required")
        if self._raw_content is not None:
            if not isinstance(self._raw_content, bytes) or len(self._raw_content) != self.content_size:
                raise ValueError("fragment content must match content_size")

    @classmethod
    def create(
        cls, *, name: str, authority: str, server_type: ServerType, content: bytes,
        content_locator: str, instance_incarnation_id: str, created_at: datetime,
        policy_revision: str, activated_at: Optional[datetime] = None
    ) -> "ServerConfigFragment":
        if not isinstance(content, bytes):
            raise ValueError("fragment content must be exact bytes")
        return cls(
            name=name, authority=authority, server_type=server_type,
            content_id="sha256:" + hashlib.sha256(content).hexdigest(),
            content_size=len(content),
            content_locator=content_locator,
            instance_incarnation_id=instance_incarnation_id, created_at=created_at,
            activated_at=activated_at, policy_revision=policy_revision,
            _raw_content=content,
        )

    def __repr__(self) -> str:
        return (
            "ServerConfigFragment(name=%r, authority=%r, server_type=%r, "
            "content_id=%r, content_size=%r, instance_incarnation_id=%r, "
            "created_at=%r, activated_at=%r, policy_revision=%r)"
            % (
                self.name, self.authority, self.server_type, self.content_id,
                self.content_size, self.instance_incarnation_id, self.created_at,
                self.activated_at, self.policy_revision,
            )
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "authority": self.authority,
            "server_type": self.server_type.value,
            "content_id": self.content_id,
            "content_size": self.content_size,
            "instance_incarnation_id": self.instance_incarnation_id,
            "created_at": self.created_at.isoformat(),
            "activated_at": (
                self.activated_at.isoformat() if self.activated_at is not None else None
            ),
            "policy_revision": self.policy_revision,
        }


@dataclass(frozen=True)
class FragmentSet:
    fragment_set_id: str
    instance_incarnation_id: str
    server_type: ServerType
    fragments: Tuple[ServerConfigFragment, ...]
    renderer_revision: str
    rendered_generation_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        _require_digest(self.fragment_set_id, "fragment_set_id")
        _require_incarnation(self.instance_incarnation_id)
        _require_digest(self.rendered_generation_id, "rendered_generation_id")
        _require_timestamp(self.created_at, "created_at")
        if not isinstance(self.server_type, ServerType):
            raise ValueError("fragment set server is unsupported")
        if not isinstance(self.renderer_revision, str) or not self.renderer_revision:
            raise ValueError("renderer_revision is required")
        if not isinstance(self.fragments, tuple):
            object.__setattr__(self, "fragments", tuple(self.fragments))
        names = [item.name for item in self.fragments]
        if names != sorted(names):
            raise ValueError("fragments must be ordered by normalized name")
        if len(names) != len(set(names)):
            raise ValueError("duplicate fragment name")
        for fragment in self.fragments:
            if fragment.instance_incarnation_id != self.instance_incarnation_id:
                raise ValueError("fragment owner does not match set owner")
            if fragment.server_type is not self.server_type:
                raise ValueError("fragment server does not match set server")
        expected_identity = _digest(
            "server-config-fragment-set",
            {
                "instance_incarnation_id": self.instance_incarnation_id,
                "server_type": self.server_type,
                "fragments": [
                    {
                        "name": item.name,
                        "authority": item.authority,
                        "content_id": item.content_id,
                        "policy_revision": item.policy_revision,
                    }
                    for item in self.fragments
                ],
            },
        )
        if self.fragment_set_id != expected_identity:
            raise ValueError("fragment_set_id does not match canonical fragments")

    @classmethod
    def create(
        cls, *, instance_incarnation_id: str, server_type: ServerType,
        fragments: Sequence[ServerConfigFragment], renderer_revision: str,
        rendered_generation_id: str, created_at: datetime
    ) -> "FragmentSet":
        ordered = tuple(sorted(fragments, key=lambda item: item.name))
        names = [item.name for item in ordered]
        if len(names) != len(set(names)):
            raise ValueError("duplicate fragment name")
        identity = _digest(
            "server-config-fragment-set",
            {
                "instance_incarnation_id": instance_incarnation_id,
                "server_type": server_type,
                "fragments": [
                    {
                        "name": item.name,
                        "authority": item.authority,
                        "content_id": item.content_id,
                        "policy_revision": item.policy_revision,
                    }
                    for item in ordered
                ],
            },
        )
        return cls(
            fragment_set_id=identity,
            instance_incarnation_id=instance_incarnation_id,
            server_type=server_type, fragments=ordered,
            renderer_revision=renderer_revision,
            rendered_generation_id=rendered_generation_id, created_at=created_at,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "fragment_set_id": self.fragment_set_id,
            "instance_incarnation_id": self.instance_incarnation_id,
            "server_type": self.server_type.value,
            "fragments": [fragment.to_public_dict() for fragment in self.fragments],
            "renderer_revision": self.renderer_revision,
            "rendered_generation_id": self.rendered_generation_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class RuntimeObservation:
    instance_incarnation_id: Optional[str]
    server_type: Optional[ServerType]
    runtime_id: Optional[str]
    image_id: Optional[str]
    mount_id: Optional[str]
    observed_generation_id: Optional[str]
    readiness: Readiness
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.instance_incarnation_id is not None:
            _require_incarnation(self.instance_incarnation_id)
        _require_optional_digest(self.mount_id, "mount_id")
        _require_optional_digest(self.observed_generation_id, "observed_generation_id")
        _require_timestamp(self.observed_at, "observed_at")
        if self.server_type is not None and not isinstance(self.server_type, ServerType):
            raise ValueError("observed server is unsupported")
        if not isinstance(self.readiness, Readiness):
            raise ValueError("readiness is unsupported")
        if self.runtime_id is not None and (
            not isinstance(self.runtime_id, str) or _OPAQUE_ID.fullmatch(self.runtime_id) is None
        ):
            raise ValueError("runtime_id is not a bounded opaque ID")
        if self.image_id is not None:
            _require_image_id(self.image_id)

    def precondition_digest(self) -> str:
        return _digest(
            "server-config-runtime-precondition",
            {
                "instance_incarnation_id": self.instance_incarnation_id,
                "server_type": self.server_type,
                "runtime_id": self.runtime_id,
                "image_id": self.image_id,
                "mount_id": self.mount_id,
                "observed_generation_id": self.observed_generation_id,
            },
        )

    def authorizes(
        self, *, instance_incarnation_id: str, server_type: ServerType,
        mount_id: str, generation_id: str, not_before: datetime
    ) -> bool:
        return (
            self.readiness is Readiness.READY
            and self.instance_incarnation_id == instance_incarnation_id
            and self.server_type is server_type
            and self.runtime_id is not None
            and self.image_id is not None
            and self.mount_id == mount_id
            and self.observed_generation_id == generation_id
            and self.observed_at >= not_before
        )


@dataclass(frozen=True)
class PhaseResult:
    code: str
    evidence_id: Optional[str]
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_code(self.code)
        _require_optional_digest(self.evidence_id, "evidence_id")
        _require_timestamp(self.observed_at, "observed_at")

    @property
    def ok(self) -> bool:
        return self.code in {
            "active", "activated", "ready", "authority_accepted", "accepted", "ok", "passed",
            "reloaded", "restored", "included",
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "evidence_id": self.evidence_id,
            "observed_at": self.observed_at.isoformat(),
        }


@dataclass(frozen=True)
class ValidationEvidence:
    adapter: ServerType
    candidate_generation_id: str
    runtime_precondition_digest: str
    policy: PhaseResult
    native_validation: PhaseResult
    inclusion_proof: PhaseResult
    started_at: datetime
    ended_at: datetime
    evidence_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.adapter, ServerType):
            raise ValueError("validation adapter is unsupported")
        _require_digest(self.candidate_generation_id, "candidate_generation_id")
        _require_digest(self.runtime_precondition_digest, "runtime_precondition_digest")
        _require_digest(self.evidence_digest, "evidence_digest")
        _require_timestamp(self.started_at, "started_at")
        _require_timestamp(self.ended_at, "ended_at")
        if any(
            not isinstance(item, PhaseResult)
            for item in (self.policy, self.native_validation, self.inclusion_proof)
        ):
            raise ValueError("validation phase evidence is invalid")
        expected_digest = _digest(
            "server-config-validation-evidence",
            {
                "adapter": self.adapter,
                "candidate_generation_id": self.candidate_generation_id,
                "runtime_precondition_digest": self.runtime_precondition_digest,
                "policy": self.policy.to_public_dict(),
                "native_validation": self.native_validation.to_public_dict(),
                "inclusion_proof": self.inclusion_proof.to_public_dict(),
                "started_at": self.started_at,
                "ended_at": self.ended_at,
            },
        )
        if self.evidence_digest != expected_digest:
            raise ValueError("evidence_digest does not match canonical evidence")
        if self.ended_at < self.started_at:
            raise ValueError("validation evidence ends before it starts")
        if (self.ended_at - self.started_at).total_seconds() > 60:
            raise ValueError("validation evidence exceeds the phase deadline")

    @classmethod
    def create(
        cls, *, adapter: ServerType, candidate_generation_id: str,
        runtime_precondition_digest: str, policy: PhaseResult,
        native_validation: PhaseResult, inclusion_proof: PhaseResult,
        started_at: datetime, ended_at: datetime
    ) -> "ValidationEvidence":
        evidence_id = _digest(
            "server-config-validation-evidence",
            {
                "adapter": adapter,
                "candidate_generation_id": candidate_generation_id,
                "runtime_precondition_digest": runtime_precondition_digest,
                "policy": policy.to_public_dict(),
                "native_validation": native_validation.to_public_dict(),
                "inclusion_proof": inclusion_proof.to_public_dict(),
                "started_at": started_at,
                "ended_at": ended_at,
            },
        )
        return cls(
            adapter=adapter, candidate_generation_id=candidate_generation_id,
            runtime_precondition_digest=runtime_precondition_digest, policy=policy,
            native_validation=native_validation, inclusion_proof=inclusion_proof,
            started_at=started_at, ended_at=ended_at, evidence_digest=evidence_id,
        )

    @property
    def ok(self) -> bool:
        return (
            self.policy.ok
            and self.native_validation.ok
            and self.inclusion_proof.ok
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter.value,
            "candidate_generation_id": self.candidate_generation_id,
            "runtime_precondition_digest": self.runtime_precondition_digest,
            "policy": self.policy.to_public_dict(),
            "native_validation": self.native_validation.to_public_dict(),
            "inclusion_proof": self.inclusion_proof.to_public_dict(),
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True)
class KnownGoodReceipt:
    schema: int
    instance_incarnation_id: str
    server_type: ServerType
    fragment_set_id: str
    generation_id: str
    runtime_image_id: str
    mount_id: str
    validation_evidence_id: str
    readiness_evidence_id: str
    committed_at: datetime

    def __post_init__(self) -> None:
        if self.schema != 1:
            raise ValueError("known-good receipt schema is unsupported")
        _require_incarnation(self.instance_incarnation_id)
        if not isinstance(self.server_type, ServerType):
            raise ValueError("receipt server is unsupported")
        for field_name in (
            "fragment_set_id", "generation_id", "mount_id",
            "validation_evidence_id", "readiness_evidence_id",
        ):
            _require_digest(getattr(self, field_name), field_name)
        _require_image_id(self.runtime_image_id, "runtime_image_id")
        _require_timestamp(self.committed_at, "committed_at")

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "instance_incarnation_id": self.instance_incarnation_id,
            "server_type": self.server_type.value,
            "fragment_set_id": self.fragment_set_id,
            "generation_id": self.generation_id,
            "runtime_image_id": self.runtime_image_id,
            "mount_id": self.mount_id,
            "validation_evidence_id": self.validation_evidence_id,
            "readiness_evidence_id": self.readiness_evidence_id,
            "committed_at": self.committed_at.isoformat(),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "KnownGoodReceipt":
        if not isinstance(record, Mapping) or record.get("schema") != 1:
            raise ValueError("known-good receipt schema is unsupported")
        return cls(
            schema=1,
            instance_incarnation_id=record["instance_incarnation_id"],
            server_type=ServerType(record["server_type"]),
            fragment_set_id=record["fragment_set_id"],
            generation_id=record["generation_id"],
            runtime_image_id=record["runtime_image_id"],
            mount_id=record["mount_id"],
            validation_evidence_id=record["validation_evidence_id"],
            readiness_evidence_id=record["readiness_evidence_id"],
            committed_at=datetime.fromisoformat(record["committed_at"]),
        )


@dataclass(frozen=True)
class PhaseEvidence:
    phase: TransactionPhase
    code: str
    evidence_id: Optional[str]
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.phase, TransactionPhase):
            raise ValueError("phase is unsupported")
        _require_code(self.code)
        _require_optional_digest(self.evidence_id, "evidence_id")
        _require_timestamp(self.observed_at, "observed_at")


_TRANSITIONS = {
    TransactionPhase.REQUESTED: (TransactionPhase.PREPARED,),
    TransactionPhase.PREPARED: (TransactionPhase.VALIDATED,),
    TransactionPhase.VALIDATED: (TransactionPhase.ACTIVATING,),
    TransactionPhase.ACTIVATING: (
        TransactionPhase.RELOADING, TransactionPhase.RESTORING_PRIOR,
    ),
    TransactionPhase.RELOADING: (
        TransactionPhase.OBSERVING_READY, TransactionPhase.RESTORING_PRIOR,
    ),
    TransactionPhase.OBSERVING_READY: (
        TransactionPhase.COMMITTED, TransactionPhase.RESTORING_PRIOR,
    ),
    TransactionPhase.COMMITTED: (),
    TransactionPhase.RESTORING_PRIOR: (TransactionPhase.RECOVERY_RELOADING,),
    TransactionPhase.RECOVERY_RELOADING: (
        TransactionPhase.RECOVERY_OBSERVING_READY,
    ),
    TransactionPhase.RECOVERY_OBSERVING_READY: (),
}


@dataclass(frozen=True)
class ActivationTransaction:
    transaction_id: str
    operation: Operation
    fragment_name: str
    instance_incarnation_id: str
    server_type: ServerType
    prior_set_id: str
    prior_generation_id: str
    candidate_set_id: str
    candidate_generation_id: str
    runtime_precondition_digest: str
    phase: TransactionPhase
    phase_evidence: Tuple[PhaseEvidence, ...]
    deadline_at: datetime
    rollback_attempted: bool = False
    terminal: Optional[TerminalOutcome] = None

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_id, str) or _OPAQUE_ID.fullmatch(self.transaction_id) is None:
            raise ValueError("transaction_id is not a bounded opaque ID")
        _require_incarnation(self.instance_incarnation_id)
        if not isinstance(self.operation, Operation):
            raise ValueError("operation is unsupported")
        if not isinstance(self.server_type, ServerType):
            raise ValueError("transaction server is unsupported")
        if not isinstance(self.phase, TransactionPhase):
            raise ValueError("transaction phase is unsupported")
        if self.terminal is not None and not isinstance(self.terminal, TerminalOutcome):
            raise ValueError("terminal outcome is unsupported")
        if not isinstance(self.fragment_name, str) or _FRAGMENT_NAME.fullmatch(
            self.fragment_name
        ) is None:
            raise ValueError("fragment name is invalid")
        for field_name in (
            "prior_set_id", "prior_generation_id", "candidate_set_id",
            "candidate_generation_id", "runtime_precondition_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        _require_timestamp(self.deadline_at, "deadline_at")
        if not isinstance(self.phase_evidence, tuple):
            object.__setattr__(self, "phase_evidence", tuple(self.phase_evidence))
        if any(not isinstance(item, PhaseEvidence) for item in self.phase_evidence):
            raise ValueError("phase evidence is invalid")
        if not isinstance(self.rollback_attempted, bool):
            raise ValueError("rollback_attempted must be boolean")
        if self.terminal is not None:
            self._validate_terminal(self.terminal)

    @classmethod
    def requested(cls, **values: Any) -> "ActivationTransaction":
        return cls(
            phase=TransactionPhase.REQUESTED, phase_evidence=(),
            rollback_attempted=False, terminal=None, **values
        )

    @property
    def is_terminal(self) -> bool:
        return self.terminal is not None

    def transition(
        self, target: TransactionPhase, *, evidence: Optional[PhaseEvidence] = None
    ) -> "ActivationTransaction":
        if self.is_terminal:
            raise ValueError("terminal transaction cannot transition")
        if target not in _TRANSITIONS[self.phase]:
            raise ValueError(
                "invalid transaction transition: %s -> %s"
                % (self.phase.value, target.value)
            )
        if target is TransactionPhase.RESTORING_PRIOR:
            raise ValueError("rollback must begin through begin_rollback")
        evidence_items = self.phase_evidence
        if evidence is not None:
            if evidence.phase is not target:
                raise ValueError("phase evidence does not match transition")
            evidence_items += (evidence,)
        return replace(self, phase=target, phase_evidence=evidence_items)

    def begin_rollback(self, *, code: str, at: datetime) -> "ActivationTransaction":
        if self.is_terminal:
            raise ValueError("terminal transaction cannot roll back")
        if self.rollback_attempted:
            raise ValueError("rollback already attempted")
        if TransactionPhase.RESTORING_PRIOR not in _TRANSITIONS[self.phase]:
            raise ValueError("rollback cannot begin before possible live mutation")
        evidence = PhaseEvidence(
            TransactionPhase.RESTORING_PRIOR, code, None, at
        )
        return replace(
            self, phase=TransactionPhase.RESTORING_PRIOR,
            phase_evidence=self.phase_evidence + (evidence,),
            rollback_attempted=True,
        )

    def _validate_terminal(self, outcome: TerminalOutcome) -> None:
        allowed = {
            TerminalOutcome.ACTIVE: (TransactionPhase.COMMITTED,),
            TerminalOutcome.NO_OP: (TransactionPhase.REQUESTED,),
            TerminalOutcome.REFUSED: (
                TransactionPhase.REQUESTED, TransactionPhase.PREPARED,
                TransactionPhase.VALIDATED,
            ),
            TerminalOutcome.CONFLICT: (TransactionPhase.REQUESTED,),
            TerminalOutcome.ROLLED_BACK: (
                TransactionPhase.RECOVERY_OBSERVING_READY,
            ),
            TerminalOutcome.RECOVERY_NEEDED: (
                TransactionPhase.RESTORING_PRIOR,
                TransactionPhase.RECOVERY_RELOADING,
                TransactionPhase.RECOVERY_OBSERVING_READY,
            ),
        }
        if self.phase not in allowed[outcome]:
            raise ValueError("terminal outcome is invalid for transaction phase")

    def finish(self, outcome: TerminalOutcome) -> "ActivationTransaction":
        if self.is_terminal:
            raise ValueError("terminal transaction cannot finish twice")
        self._validate_terminal(outcome)
        return replace(self, terminal=outcome)

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "transaction_id": self.transaction_id,
            "operation": self.operation.value,
            "fragment_name": self.fragment_name,
            "instance_incarnation_id": self.instance_incarnation_id,
            "server_type": self.server_type.value,
            "prior_set_id": self.prior_set_id,
            "prior_generation_id": self.prior_generation_id,
            "candidate_set_id": self.candidate_set_id,
            "candidate_generation_id": self.candidate_generation_id,
            "runtime_precondition_digest": self.runtime_precondition_digest,
            "phase": self.phase.value,
            "phase_evidence": [
                {
                    "phase": pe.phase.value,
                    "code": pe.code,
                    "evidence_id": pe.evidence_id,
                    "observed_at": pe.observed_at.isoformat(),
                }
                for pe in self.phase_evidence
            ],
            "deadline_at": self.deadline_at.isoformat(),
            "rollback_attempted": self.rollback_attempted,
            "terminal": self.terminal.value if self.terminal is not None else None,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ActivationTransaction":
        if not isinstance(record, Mapping) or record.get("schema") != 1:
            raise ValueError("transaction record schema is unsupported")
        evidence = tuple(
            PhaseEvidence(
                phase=TransactionPhase(pe["phase"]),
                code=pe["code"],
                evidence_id=pe.get("evidence_id"),
                observed_at=datetime.fromisoformat(pe["observed_at"]),
            )
            for pe in record.get("phase_evidence", ())
        )
        terminal = (
            TerminalOutcome(record["terminal"]) if record.get("terminal") is not None else None
        )
        return cls(
            transaction_id=record["transaction_id"],
            operation=Operation(record["operation"]),
            fragment_name=record["fragment_name"],
            instance_incarnation_id=record["instance_incarnation_id"],
            server_type=ServerType(record["server_type"]),
            prior_set_id=record["prior_set_id"],
            prior_generation_id=record["prior_generation_id"],
            candidate_set_id=record["candidate_set_id"],
            candidate_generation_id=record["candidate_generation_id"],
            runtime_precondition_digest=record["runtime_precondition_digest"],
            phase=TransactionPhase(record["phase"]),
            phase_evidence=evidence,
            deadline_at=datetime.fromisoformat(record["deadline_at"]),
            rollback_attempted=bool(record.get("rollback_attempted", False)),
            terminal=terminal,
        )


@dataclass(frozen=True)
class OperationResult:
    outcome: TerminalOutcome
    code: str
    mutated: Optional[bool]
    instance_incarnation_id: Optional[str]
    fragment_name: Optional[str]
    fragment_set_id: Optional[str]
    phase_codes: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, TerminalOutcome):
            raise ValueError("outcome is unsupported")
        _require_code(self.code)
        if self.instance_incarnation_id is not None:
            _require_incarnation(self.instance_incarnation_id)
        _require_optional_digest(self.fragment_set_id, "fragment_set_id")
        if self.fragment_name is not None and (
            not isinstance(self.fragment_name, str)
            or _FRAGMENT_NAME.fullmatch(self.fragment_name) is None
        ):
            raise ValueError("fragment name is invalid")
        if self.mutated is not None and not isinstance(self.mutated, bool):
            raise ValueError("mutated must be boolean or null")
        expected_mutation = {
            TerminalOutcome.ACTIVE: True,
            TerminalOutcome.NO_OP: False,
            TerminalOutcome.REFUSED: False,
            TerminalOutcome.ROLLED_BACK: True,
            TerminalOutcome.CONFLICT: False,
            TerminalOutcome.RECOVERY_NEEDED: None,
        }
        if self.mutated is not expected_mutation[self.outcome]:
            raise ValueError("mutated is invalid for outcome")
        codes = tuple(self.phase_codes)
        for code in codes:
            _require_code(code, "phase code")
        object.__setattr__(self, "phase_codes", codes)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "code": self.code,
            "mutated": self.mutated,
            "instance_incarnation_id": self.instance_incarnation_id,
            "fragment_name": self.fragment_name,
            "fragment_set_id": self.fragment_set_id,
            "phase_codes": list(self.phase_codes),
        }


@dataclass(frozen=True)
class BehaviorEvidence:
    instance_incarnation_id: str
    runtime_id: str
    image_id: str
    fragment_set_id: str
    request_id: str
    response_status: int
    server_marker: Optional[str]
    php_sentinel_before: int | str
    php_sentinel_after: int | str
    readiness: Readiness
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_incarnation(self.instance_incarnation_id)
        _require_digest(self.fragment_set_id, "fragment_set_id")
        _require_timestamp(self.observed_at, "observed_at")
        if not isinstance(self.readiness, Readiness):
            raise ValueError("readiness is unsupported")
        if not 100 <= self.response_status <= 599:
            raise ValueError("response_status is outside the HTTP bound")
        for field_name in ("runtime_id", "request_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or _OPAQUE_ID.fullmatch(value) is None:
                raise ValueError("%s is not a bounded opaque ID" % field_name)
        _require_image_id(self.image_id)
        if self.server_marker is not None and (
            not isinstance(self.server_marker, str)
            or _SERVER_MARKER.fullmatch(self.server_marker) is None
        ):
            raise ValueError("server_marker is not a bounded static token")
        for field_name in ("php_sentinel_before", "php_sentinel_after"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not (
                (isinstance(value, int) and 0 <= value <= _MAX_JSON_INTEGER)
                or (isinstance(value, str) and _DIGEST.fullmatch(value) is not None)
            ):
                raise ValueError("%s must be an integer or digest" % field_name)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "instance_incarnation_id": self.instance_incarnation_id,
            "runtime_id": self.runtime_id,
            "image_id": self.image_id,
            "fragment_set_id": self.fragment_set_id,
            "request_id": self.request_id,
            "response_status": self.response_status,
            "server_marker": self.server_marker,
            "php_sentinel_before": self.php_sentinel_before,
            "php_sentinel_after": self.php_sentinel_after,
            "readiness": self.readiness.value,
            "observed_at": self.observed_at.isoformat(),
        }


__all__ = [
    "ActivationTransaction", "BehaviorEvidence", "FragmentSet",
    "InspectionState", "InstanceConfigAuthority", "InstanceIdentityProjection",
    "KnownGoodReceipt", "Operation", "OperationResult", "PhaseEvidence",
    "PhaseResult", "Readiness", "RuntimeMode", "RuntimeObservation",
    "ServerConfigFragment", "ServerType", "TerminalOutcome",
    "TransactionPhase", "ValidationEvidence",
]

# Closed creation ownership values. These contain no runtime configuration or URLs.
_CREATION_CONTEXT_KEYS = frozenset({'schema_version', 'operation_id', 'request_id', 'job_id', 'intent_digest', 'intent_fields', 'project_identity', 'project_root_digest', 'label'})
_CREATION_INTENT_KEYS = frozenset({'schema_version', 'delivery_intent_digest', 'target_scope_digest', 'project_identity', 'project_root_digest', 'label', 'instance_config_digest', 'create_allowed'})
_CREATION_RECEIPT_KEYS = frozenset({'schema_version', 'operation_id', 'request_id', 'job_id', 'intent_digest', 'project_identity', 'project_root_digest', 'label', 'instance_id', 'instance_incarnation_id', 'relation', 'owner_commit_at', 'completion', 'completed_at', 'result_code'})


def creation_digest(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _creation_text(value, *, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('creation_context_invalid')


def _creation_request_text(value):
    """Keep the original owner's bounded, opaque request namespace."""
    if value is None:
        return
    from sandbox.services.redaction import redact_text
    if (not isinstance(value, str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}', value) is None
            or redact_text(value) != value):
        raise ValueError('creation_context_invalid')


def _creation_timestamp(value, *, nullable=False):
    if nullable and value is None:
        return
    if (not isinstance(value, str) or len(value) > 40
            or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', value) is None):
        raise ValueError('creation_timestamp_invalid')
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('creation_timestamp_invalid') from None


def validate_creation_context(value) -> dict:
    if not isinstance(value, dict) or set(value) - _CREATION_CONTEXT_KEYS or not (_CREATION_CONTEXT_KEYS - {'job_id'}) <= set(value):
        raise ValueError('creation_context_invalid')
    value = dict(value, job_id=value.get('job_id'))
    fields = value['intent_fields']
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or not isinstance(fields, dict) or set(fields) != _CREATION_INTENT_KEYS or type(fields['schema_version']) is not int or fields['schema_version'] != 1 or type(fields['create_allowed']) is not bool:
        raise ValueError('creation_context_invalid')
    for key in ('operation_id', 'project_identity', 'label'):
        _creation_text(value[key])
    _creation_request_text(value['request_id'])
    _creation_text(value['job_id'], nullable=True)
    for key in ('intent_digest', 'project_root_digest'):
        if not isinstance(value[key], str) or not _DIGEST.fullmatch(value[key]):
            raise ValueError('creation_context_invalid')
    for key in ('delivery_intent_digest', 'target_scope_digest', 'instance_config_digest'):
        if not isinstance(fields[key], str) or not _DIGEST.fullmatch(fields[key]):
            raise ValueError('creation_context_invalid')
    if any(fields[key] != value[key] for key in ('project_identity', 'project_root_digest', 'label')) or creation_digest(fields) != value['intent_digest']:
        raise ValueError('creation_request_conflict')
    if len(json.dumps(value).encode()) > 8192:
        raise ValueError('creation_context_invalid')
    return json.loads(json.dumps(value))


def creation_intent_fields(pconf, *, project_identity, target_scope_digest, delivery_intent_digest, label='default', create_allowed=False, config_label=None):
    # Hash only the closed declarative configuration subset; never secret maps,
    # environment values, plugin URLs, credentials or arbitrary extension data.
    from pathlib import Path
    safe = {key: pconf.get(key) for key in ('kind', 'server', 'phpVersion', 'wpVersion', 'service', 'internal_port', 'http_port', 'startup_timeout_seconds', 'recreate_on_ensure', 'multisite')}
    runtime = pconf.get('wordpressRuntime')
    if isinstance(runtime, Mapping):
        safe['wordpressRuntime'] = {key: runtime.get(key) for key in ('mode', 'adapter', 'explicit')}
    from urllib.parse import urlsplit, urlunsplit
    from sandbox.config.instance_lifecycle import normalize_instance_lifecycle
    safe['instanceLifecycle'] = normalize_instance_lifecycle(pconf.get('instanceLifecycle'))
    from sandbox.config.php_extensions import normalize_php_extensions
    from sandbox.php_extensions.models import PhpExtensionsConfig
    extensions = pconf.get('phpExtensions')
    if extensions is not None:
        normalized = normalize_php_extensions(
            extensions.to_dict() if isinstance(extensions, PhpExtensionsConfig) else extensions)
        safe['phpExtensions'] = normalized.to_dict()
    else:
        safe['phpExtensions'] = None

    # These runtime constants have fixed, nonsecret scalar semantics. Never
    # hash arbitrary config values or a WP_DEBUG_LOG pathname.
    constants = pconf.get('config') or {}
    if not isinstance(constants, Mapping):
        raise ValueError('creation_config_unsupported')
    bool_constants = {'WP_DEBUG', 'WP_DEBUG_DISPLAY', 'WP_DEBUG_LOG', 'SCRIPT_DEBUG',
                      'SAVEQUERIES', 'DISALLOW_FILE_EDIT', 'DISALLOW_FILE_MODS', 'WP_CACHE'}
    safe_constants = {}
    for name in bool_constants:
        if name in constants:
            if type(constants[name]) is not bool:
                raise ValueError('creation_config_unsupported')
            safe_constants[name] = constants[name]
    for name, minimum in (('WP_POST_REVISIONS', -1), ('AUTOSAVE_INTERVAL', 1),
                          ('EMPTY_TRASH_DAYS', 0)):
        if name not in constants:
            continue
        value = constants[name]
        if name == 'WP_POST_REVISIONS' and type(value) is bool:
            safe_constants[name] = value
            continue
        if type(value) is not int or not minimum <= value <= 2147483647:
            raise ValueError('creation_config_unsupported')
        safe_constants[name] = value
    safe['wordpress_constants'] = safe_constants

    from sandbox.config.domains import normalize_hostname, normalize_tld
    for name in ('domain', 'hostname'):
        if name in pconf:
            safe[name] = None if pconf[name] is None else normalize_hostname(pconf[name])
    if 'tld' in pconf:
        safe['tld'] = None if pconf['tld'] is None else normalize_tld(pconf['tld'])
    domains = pconf.get('domains')
    if domains is not None:
        if not isinstance(domains, Mapping):
            raise ValueError('creation_config_unsupported')
        policy = {}
        for name in ('enabled', 'wildcard'):
            if name in domains:
                if type(domains[name]) is not bool:
                    raise ValueError('creation_config_unsupported')
                policy[name] = domains[name]
        for name, normalize in (('hostname', normalize_hostname), ('tld', normalize_tld)):
            if name in domains:
                policy[name] = None if domains[name] is None else normalize(domains[name])
        for name in ('strategy', 'ingress'):
            if name in domains:
                value = domains[name]
                if value is not None and (not isinstance(value, str) or re.fullmatch(r'[a-z][a-z0-9-]{0,62}', value) is None):
                    raise ValueError('creation_config_unsupported')
                policy[name] = value
        safe['domains'] = policy
    if 'aliases' in pconf and pconf['aliases'] is not None:
        aliases = [pconf['aliases']] if isinstance(pconf['aliases'], str) else pconf['aliases']
        if not isinstance(aliases, (list, tuple)) or len(aliases) > 20:
            raise ValueError('creation_config_unsupported')
        safe['aliases'] = list(dict.fromkeys(normalize_hostname(value) for value in aliases
                                            if not isinstance(value, str) or value.strip()))
    for key in ('resources', 'node_store', 'slug', 'wpDebug', 'wpCron', 'locale'):
        if key in pconf:
            safe[key] = pconf[key]
    def public_locator(raw):
        if not isinstance(raw, str):
            return None
        parsed = urlsplit(raw)
        if parsed.scheme in {'http', 'https'}:
            return urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', ''))
        if '://' in raw:
            return None
        return parsed.path if raw.startswith('/') else raw
    safe['compose_file'] = public_locator(pconf.get('compose_file'))
    safe['health_path'] = public_locator(pconf.get('health_path'))
    plugins = pconf.get('plugins_resolved')
    if isinstance(plugins, Mapping):
        safe['plugins'] = {}
        for slug, entry in plugins.items():
            if not isinstance(entry, Mapping):
                continue
            source = entry.get('source')
            source = ({'kind': source.get('kind'), 'value': public_locator(source.get('value'))}
                      if isinstance(source, Mapping) else public_locator(source))
            safe['plugins'][slug] = {'source': source, 'active': entry.get('active'),
                                    'on_demand': entry.get('on_demand')}
    if pconf.get('kind') == 'compose':
        safe['startup_timeout_seconds'] = float(pconf.get('startup_timeout_seconds', 120.0))
        safe['recreate_on_ensure'] = pconf.get('recreate_on_ensure', False)
    safe['config_label'] = config_label if config_label is not None else label
    return {'schema_version': 1, 'delivery_intent_digest': delivery_intent_digest,
            'target_scope_digest': target_scope_digest, 'project_identity': project_identity,
            'project_root_digest': creation_digest(str(Path(pconf['root']).resolve())),
            'label': label, 'instance_config_digest': creation_digest(safe), 'create_allowed': create_allowed}


def validate_creation_receipt(value) -> dict:
    if not isinstance(value, dict) or set(value) != _CREATION_RECEIPT_KEYS or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('creation_receipt_invalid')
    for key in ('operation_id', 'job_id', 'project_identity', 'label', 'instance_id', 'result_code'):
        _creation_text(value[key], nullable=key in {'job_id', 'result_code'})
    _creation_request_text(value['request_id'])
    for key in ('intent_digest', 'project_root_digest'):
        if not isinstance(value[key], str) or not _DIGEST.fullmatch(value[key]):
            raise ValueError('creation_receipt_invalid')
    _require_incarnation(value['instance_incarnation_id'])
    if not isinstance(value['relation'], str) or not isinstance(value['completion'], str) or value['relation'] not in {'created', 'reused', 'unknown'} or value['completion'] not in {'pending', 'succeeded', 'failed', 'unknown'}:
        raise ValueError('creation_receipt_invalid')
    for key in ('owner_commit_at', 'completed_at'):
        _creation_timestamp(value[key], nullable=key == 'completed_at')
    if (value['completion'] in {'succeeded', 'failed'}) != (value['completed_at'] is not None):
        raise ValueError('creation_receipt_invalid')
    if len(json.dumps(value).encode()) > 4096:
        raise ValueError('creation_receipt_invalid')
    return dict(value)


def validate_url_mutation_result(value) -> dict:
    keys = {'operation_id', 'request_id', 'target_digest', 'expected_incarnation',
            'observed_incarnation', 'before_observed_at', 'writes', 'readback',
            'finished_at', 'result_code'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('creation_url_result_invalid')
    _creation_text(value['operation_id'])
    _creation_request_text(value['request_id'])
    if not isinstance(value['target_digest'], str) or not _DIGEST.fullmatch(value['target_digest']):
        raise ValueError('creation_url_result_invalid')
    _require_incarnation(value['expected_incarnation'])
    if value['observed_incarnation'] is not None:
        _require_incarnation(value['observed_incarnation'])
    for key in ('before_observed_at', 'finished_at'):
        _creation_timestamp(value[key], nullable=True)
    if not isinstance(value['writes'], dict) or set(value['writes']) != {'home', 'siteurl'} or any(not isinstance(state, str) or state not in {'attempted', 'succeeded', 'failed', 'unknown', 'not_applicable'} for state in value['writes'].values()):
        raise ValueError('creation_url_result_invalid')
    if not isinstance(value['readback'], dict) or set(value['readback']) != {'home', 'siteurl'} or any(type(state) not in {bool, type(None)} for state in value['readback'].values()):
        raise ValueError('creation_url_result_invalid')
    if not isinstance(value['result_code'], str) or value['result_code'] not in {'remote_instance_url_verified', 'remote_instance_url_incomplete', 'instance_incarnation_changed'}:
        raise ValueError('creation_url_result_invalid')
    if value['result_code'] == 'remote_instance_url_verified' and (value['observed_incarnation'] != value['expected_incarnation'] or not all(state == 'succeeded' for state in value['writes'].values()) or not all(value['readback'].values()) or value['finished_at'] is None):
        raise ValueError('creation_url_result_invalid')
    if len(json.dumps(value).encode()) > 4096:
        raise ValueError('creation_url_result_invalid')
    return json.loads(json.dumps(value))
