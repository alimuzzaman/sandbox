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
