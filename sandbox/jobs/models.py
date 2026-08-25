"""Validated value objects for the durable remote job runtime."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_DEADLINE_SECONDS = 604_800
MAX_OUTPUT_PAGE_BYTES = 262_144
MAX_ARTIFACT_PAGE_BYTES = 1_048_576
MIN_OUTPUT_WAIT_SECONDS = 0
MAX_OUTPUT_WAIT_SECONDS = 20
_JOB_ID = re.compile(r"^(?:[a-f0-9]{16}|[a-f0-9]{32})$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _safe_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError(f"{label} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} is invalid")
    return value


def _safe_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return value


def _positive_seconds(value: object, label: str, *, maximum: int = MAX_DEADLINE_SECONDS) -> int:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or value <= 0 or value > maximum or int(value) != value):
        raise ValueError(f"{label} must be a finite positive whole number no greater than {maximum}")
    return int(value)


def normalize_output_wait_seconds(value: object) -> int:
    """Validate the bounded retained-output long-poll interval.

    This is intentionally stricter than the generic positive-duration helper:
    output waits are whole seconds, may be disabled with zero, and must remain
    short enough that a single observation cannot pin a control-plane request.
    Keeping the check here gives local services, MCP, and remote transports one
    exact contract before any target lookup or I/O occurs.
    """
    if (isinstance(value, bool) or not isinstance(value, int)
            or not MIN_OUTPUT_WAIT_SECONDS <= value <= MAX_OUTPUT_WAIT_SECONDS):
        raise ValueError(
            f"output wait must be between 0 and {MAX_OUTPUT_WAIT_SECONDS} seconds"
        )
    return value


def normalize_output_page_bytes(value: object) -> int:
    """Validate the retained-output page size before local or remote I/O."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= MAX_OUTPUT_PAGE_BYTES):
        raise ValueError(
            f"output page bytes must be between 1 and {MAX_OUTPUT_PAGE_BYTES}"
        )
    return value


class Lifecycle(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class Health(str, Enum):
    UNKNOWN = "unknown"
    ACTIVE = "active"
    QUIET = "quiet"
    SUSPECTED_STALLED = "suspected_stalled"
    STUCK = "stuck"
    SUPERVISOR_UNRESPONSIVE = "supervisor_unresponsive"
    ORPHANED = "orphaned"
    PROCESS_MISSING = "process_missing"
    UNREACHABLE = "unreachable"
    TERMINAL = "terminal"


TERMINAL_LIFECYCLES = frozenset({
    Lifecycle.SUCCEEDED, Lifecycle.FAILED, Lifecycle.TIMED_OUT,
    Lifecycle.CANCELLED, Lifecycle.INTERRUPTED,
})

_TRANSITIONS = {
    Lifecycle.ACCEPTED: frozenset({Lifecycle.QUEUED, Lifecycle.RUNNING, Lifecycle.CANCELLED,
                                   Lifecycle.FAILED, Lifecycle.INTERRUPTED}),
    Lifecycle.QUEUED: frozenset({Lifecycle.RUNNING, Lifecycle.SUCCEEDED, Lifecycle.CANCELLING,
                                 Lifecycle.CANCELLED, Lifecycle.TIMED_OUT,
                                 Lifecycle.FAILED, Lifecycle.INTERRUPTED}),
    Lifecycle.RUNNING: frozenset({Lifecycle.CANCELLING, Lifecycle.SUCCEEDED,
                                  Lifecycle.FAILED, Lifecycle.TIMED_OUT,
                                  Lifecycle.CANCELLED, Lifecycle.INTERRUPTED}),
    Lifecycle.CANCELLING: frozenset({Lifecycle.CANCELLED, Lifecycle.FAILED,
                                     Lifecycle.INTERRUPTED}),
}


def validate_transition(current: Lifecycle | str, target: Lifecycle | str) -> None:
    current, target = Lifecycle(current), Lifecycle(target)
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"invalid job lifecycle transition: {current.value} -> {target.value}")


def new_job_id() -> str:
    return secrets.token_hex(16)


def validate_job_id(value: object) -> str:
    if not isinstance(value, str) or not _JOB_ID.fullmatch(value):
        raise ValueError("job id is invalid")
    return value


def validate_ack_job_id(value: object, *, label: str = "job id") -> str:
    """Validate an acknowledgement identity without accepting a blank placeholder.

    Remote controllers may use a legacy short identifier in test doubles or while
    replaying an old record, so this boundary intentionally requires a bounded
    non-empty string rather than imposing the local repository's 16/32-hex shape.
    Local repository rows continue to use :func:`validate_job_id`.
    """
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{label} is missing or invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} is missing or invalid")
    return value


def validate_argv(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ValueError("command must be a non-empty argv list")
    result = tuple(value)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in result):
        raise ValueError("command must be a non-empty argv list without NUL bytes")
    return result


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    timeout_seconds: int
    stall_seconds: int = 300
    cancel_grace_seconds: int = 20
    cancel_on_stall: bool = False
    cleanup: str = "retain"

    def __post_init__(self) -> None:
        _safe_name(self.name, "execution profile name")
        object.__setattr__(self, "timeout_seconds", _positive_seconds(
            self.timeout_seconds, "execution timeout"))
        object.__setattr__(self, "stall_seconds", _positive_seconds(
            self.stall_seconds, "stall timeout"))
        object.__setattr__(self, "cancel_grace_seconds", _positive_seconds(
            self.cancel_grace_seconds, "cancellation grace", maximum=600))
        if not isinstance(self.cancel_on_stall, bool):
            raise ValueError("cancel_on_stall must be boolean")
        if self.cleanup not in {"retain", "always", "on-success", "ephemeral"}:
            raise ValueError("cleanup policy is invalid")


@dataclass(frozen=True)
class DeadlineResolution:
    seconds: int
    source: str
    reminder: str | None = None


def resolve_deadline(*, explicit_seconds: object = None,
                     profile: ExecutionProfile | None = None,
                     workflow_seconds: object = None,
                     fallback: ExecutionProfile | None = None) -> DeadlineResolution:
    if explicit_seconds is not None:
        return DeadlineResolution(_positive_seconds(explicit_seconds, "execution timeout"), "explicit")
    if workflow_seconds is not None:
        return DeadlineResolution(_positive_seconds(workflow_seconds, "workflow timeout"), "workflow")
    selected = profile or fallback
    if selected is None:
        raise ValueError("every execution requires a finite deadline")
    source = f"profile:{selected.name}"
    return DeadlineResolution(
        selected.timeout_seconds, source,
        f"deadline supplied by {source}; pass an explicit timeout to override it",
    )


@dataclass(frozen=True)
class OutputProfile:
    name: str
    mode: str = "smart"
    every_lines: int | None = None
    every_events: int | None = None
    every_seconds: float | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    before: int = 0
    after: int = 0
    deduplicate: bool = False
    timestamps: bool = False
    stream_prefixes: bool = False
    heartbeat_seconds: int | None = None
    max_bytes: int = 65_536
    max_events: int = 500

    def __post_init__(self) -> None:
        _safe_name(self.name, "output profile name")
        if self.mode not in {"full", "smart", "errors", "sampled", "quiet"}:
            raise ValueError("output mode is invalid")
        for value, label, maximum in (
            (self.every_lines, "line sample", 100_000),
            (self.every_events, "event sample", 100_000),
            (self.heartbeat_seconds, "heartbeat", 3_600),
        ):
            if value is not None:
                _positive_seconds(value, label, maximum=maximum)
        if self.every_seconds is not None:
            _positive_seconds(self.every_seconds, "time sample", maximum=3_600)
        for pattern in (*self.include, *self.exclude):
            _safe_text(pattern, "output match pattern")
            if len(pattern) > 256:
                raise ValueError("output match pattern is too long")
        if not 0 <= self.before <= 100 or not 0 <= self.after <= 100:
            raise ValueError("output context is invalid")
        _positive_seconds(self.max_bytes, "output profile byte budget", maximum=10_485_760)
        _positive_seconds(self.max_events, "output profile event budget", maximum=100_000)


def output_profile_from_definition(name: str, definition: Mapping[str, Any] | None = None) -> OutputProfile:
    """Build a validated profile from the persisted config-schema spelling."""
    definition = definition or {}
    if not isinstance(definition, Mapping):
        raise ValueError("output profile definition is invalid")
    aliases = {
        "everyLines": "every_lines", "everyEvents": "every_events",
        "everySeconds": "every_seconds", "streamPrefixes": "stream_prefixes",
        "heartbeatSeconds": "heartbeat_seconds", "maxBytes": "max_bytes",
        "maxEvents": "max_events",
    }
    allowed = {"mode", "everyLines", "everyEvents", "everySeconds", "include", "exclude",
               "before", "after", "deduplicate", "timestamps", "streamPrefixes",
               "heartbeatSeconds", "maxBytes", "maxEvents"}
    unknown = set(definition) - allowed
    if unknown:
        raise ValueError("output profile definition has unknown keys")
    kwargs = {aliases.get(key, key): tuple(value) if key in {"include", "exclude"}
              and isinstance(value, (list, tuple)) else value for key, value in definition.items()}
    return OutputProfile(name, **kwargs)


@dataclass(frozen=True)
class SourceIdentity:
    identity: str
    commit: str | None = None
    dirty_digest: str | None = None

    def __post_init__(self) -> None:
        _safe_text(self.identity, "source identity")
        if self.commit is not None:
            _safe_text(self.commit, "source commit")
        if self.dirty_digest is not None:
            _safe_text(self.dirty_digest, "source dirty digest")


@dataclass(frozen=True)
class TargetRequest:
    project_dir: str = "."
    local: bool = False
    remote: str | None = None
    workspace: str | None = None
    required_capability: str | None = None
    project_identity: str | None = None
    workspace_id: str | None = None
    migration_plan_id: str | None = None
    confirm: bool = False
    expected_legacy_namespace: str | None = None
    checkout_locator: str | None = None
    deployment_receipt: str | None = None
    inventory_digest: str | None = None
    index_generation: int | None = None
    limit: int = 50
    active_only: bool = False
    measure_sizes: bool = False
    mode: str = "persistent"
    # Whether this operation may infer the single configured remote when no
    # target is selected (spec 014: "operations that permit target inference").
    # Instance lifecycle opts out: booting a dev instance is a local action
    # unless the project or the caller asks for a remote, and inferring one
    # moved every project's `sb ensure` onto the VPS.
    allow_inferred_remote: bool = True

    def __post_init__(self) -> None:
        _safe_text(self.project_dir, "project directory")
        if not isinstance(self.local, bool):
            raise ValueError("local selector must be boolean")
        if not isinstance(self.allow_inferred_remote, bool):
            raise ValueError("inferred remote selector must be boolean")
        if self.remote is not None:
            _safe_name(self.remote, "remote name")
        if self.workspace is not None:
            _safe_name(self.workspace, "workspace label")
        if self.required_capability is not None:
            _safe_text(self.required_capability, "required capability")
        for value, label in (
            (self.project_identity, "project identity"),
            (self.workspace_id, "workspace id"),
            (self.migration_plan_id, "migration plan id"),
            (self.expected_legacy_namespace, "legacy workspace namespace"),
            (self.checkout_locator, "workspace checkout locator"),
            (self.deployment_receipt, "workspace deployment receipt"),
            (self.inventory_digest, "workspace inventory digest"),
        ):
            if value is not None:
                _safe_text(value, label)
        if not isinstance(self.confirm, bool):
            raise ValueError("workspace confirmation must be boolean")
        if (isinstance(self.index_generation, bool) or
                self.index_generation is not None and
                (not isinstance(self.index_generation, int) or self.index_generation < 0)):
            raise ValueError("workspace index generation must be non-negative")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 5000:
            raise ValueError("workspace list limit must be between 1 and 5000")
        if not isinstance(self.active_only, bool):
            raise ValueError("workspace active-only selector must be boolean")
        if not isinstance(self.measure_sizes, bool):
            raise ValueError("workspace size-measurement selector must be boolean")
        _safe_name(self.mode, "workspace mode")


@dataclass(frozen=True)
class ResolvedTarget:
    project_root: str
    kind: str
    remote_name: str | None
    workspace_label: str
    namespace: str
    sources: Mapping[str, str]
    remote: Mapping[str, Any] | None = None
    runtime_policy: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _safe_text(self.project_root, "project root")
        if self.kind not in {"local", "remote"}:
            raise ValueError("target kind is invalid")
        _safe_name(self.workspace_label, "workspace label")

    def context(self) -> dict[str, Any]:
        """Return the canonical project/workspace context for detached callers."""
        return {
            "project_dir": self.project_root,
            "workspace": self.workspace_label,
            "target": {"kind": self.kind, "remote": self.remote_name},
            "namespace": self.namespace,
        }

    @property
    def project_identity(self) -> str:
        value = self.sources.get("identity")
        if not isinstance(value, str) or not value:
            raise ValueError("resolved target has no canonical project identity")
        return value


@dataclass(frozen=True)
class OutputQuery:
    stream: str = "combined"
    cursor: str | None = None
    offset: int | None = None
    tail_bytes: int | None = None
    lines: int | None = None
    since: str | None = None
    max_bytes: int = 65_536
    max_events: int = 500
    encoding: str = "utf8"
    profile: str = "full"
    wait_seconds: int = 0

    def __post_init__(self) -> None:
        if self.stream not in {"combined", "stdout", "stderr"}:
            raise ValueError("output stream is invalid")
        object.__setattr__(self, "max_bytes", normalize_output_page_bytes(self.max_bytes))
        _positive_seconds(self.max_events, "output page events", maximum=500)
        if self.encoding not in {"utf8", "base64"}:
            raise ValueError("output encoding is invalid")
        object.__setattr__(self, "wait_seconds", normalize_output_wait_seconds(self.wait_seconds))
        positions = [self.cursor, self.offset, self.tail_bytes, self.lines, self.since]
        if sum(value is not None for value in positions) > 1:
            raise ValueError("output query accepts only one position selector")
        for value, label, allow_zero in (
            (self.offset, "output offset", True),
            (self.tail_bytes, "output tail bytes", False),
            (self.lines, "output lines", False),
        ):
            if value is None:
                continue
            if (isinstance(value, bool) or not isinstance(value, int) or
                    value < 0 or (not allow_zero and value == 0)):
                raise ValueError(f"{label} is invalid")
        if self.since is not None:
            if not isinstance(self.since, str) or not self.since.strip():
                raise ValueError("output since value is invalid")


@dataclass(frozen=True)
class ArtifactQuery:
    artifact_id: str
    offset: int = 0
    max_bytes: int = MAX_ARTIFACT_PAGE_BYTES
    encoding: str = "base64"

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, str) or not _ARTIFACT_ID.fullmatch(self.artifact_id):
            raise ValueError("artifact id is invalid")
        if isinstance(self.offset, bool) or not isinstance(self.offset, int) or self.offset < 0:
            raise ValueError("artifact offset is invalid")
        _positive_seconds(self.max_bytes, "artifact page bytes", maximum=MAX_ARTIFACT_PAGE_BYTES)
        if self.encoding not in {"base64", "bytes"}:
            raise ValueError("artifact encoding is invalid")


@dataclass(frozen=True)
class JobSubmission:
    kind: str
    project_root: str
    project_identity: str
    target_kind: str
    workspace_label: str
    argv: tuple[str, ...]
    deadline_seconds: int
    source: SourceIdentity
    remote_name: str | None = None
    request_id: str | None = None
    parent_job_id: str | None = None
    retry_of_job_id: str | None = None
    attempt: int = 1
    workspace_mode: str = "persistent"
    cwd_relative: str = "."
    execution_profile: str = "exec"
    output_profile: str = "smart"
    output_profile_definition: Mapping[str, Any] | None = None
    deadline_source: str = "explicit"
    deadline_reminder: str | None = None
    stall_seconds: int = 300
    cancel_grace_seconds: int = 20
    cancel_on_stall: bool = False
    cleanup_policy: str = "retain"
    execution_policy_provenance: Mapping[str, str] | None = None
    environment_keys: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    failure_policy: str = "fail-fast"
    compatibility_differences: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _safe_name(self.kind, "job kind")
        _safe_text(self.project_root, "project root")
        _safe_text(self.project_identity, "project identity")
        if self.target_kind not in {"local", "remote"}:
            raise ValueError("target kind is invalid")
        if self.target_kind == "remote" and self.remote_name is None:
            raise ValueError("remote target requires a remote name")
        if self.remote_name is not None:
            _safe_name(self.remote_name, "remote name")
        _safe_name(self.workspace_label, "workspace label")
        object.__setattr__(self, "argv", validate_argv(self.argv))
        object.__setattr__(self, "deadline_seconds", _positive_seconds(
            self.deadline_seconds, "execution timeout"))
        if self.request_id is not None:
            _safe_name(self.request_id, "request id")
        for job_id in (self.parent_job_id, self.retry_of_job_id):
            if job_id is not None:
                validate_job_id(job_id)
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("job attempt is invalid")
        if self.workspace_mode not in {"persistent", "isolated", "ephemeral"}:
            raise ValueError("workspace mode is invalid")
        path = Path(self.cwd_relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("job working directory must stay within the project")
        _safe_name(self.execution_profile, "execution profile")
        _safe_name(self.output_profile, "output profile")
        if self.output_profile_definition is not None:
            output_profile_from_definition(self.output_profile, self.output_profile_definition)
            object.__setattr__(self, "output_profile_definition", dict(self.output_profile_definition))
        _positive_seconds(self.stall_seconds, "stall timeout")
        _positive_seconds(self.cancel_grace_seconds, "cancellation grace", maximum=600)
        if not isinstance(self.cancel_on_stall, bool):
            raise ValueError("cancel_on_stall must be boolean")
        if self.cleanup_policy not in {"retain", "always", "on-success", "ephemeral"}:
            raise ValueError("cleanup policy is invalid")
        if self.deadline_reminder is not None:
            _safe_text(self.deadline_reminder, "deadline reminder")
        provenance = self.execution_policy_provenance or {}
        if not isinstance(provenance, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str) or not key or not value
                for key, value in provenance.items()):
            raise ValueError("execution policy provenance is invalid")
        object.__setattr__(self, "execution_policy_provenance", dict(provenance))
        for key in self.environment_keys:
            _safe_name(key, "environment key")
        for value in self.artifact_paths:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("artifact path must stay within the project")
        if not isinstance(self.depends_on, (tuple, list)):
            raise ValueError("job dependencies must be a sequence")
        labels = tuple(self.depends_on)
        if len(set(labels)) != len(labels):
            raise ValueError("job dependencies must be unique")
        for label in labels:
            _safe_name(label, "dependency workspace label")
        object.__setattr__(self, "depends_on", labels)
        if self.failure_policy not in {"fail-fast", "continue"}:
            raise ValueError("job failure policy is invalid")
        if not isinstance(self.compatibility_differences, (tuple, list)):
            raise ValueError("compatibility differences must be a sequence")
        object.__setattr__(self, "compatibility_differences", tuple(dict(item) for item in self.compatibility_differences))

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "project_root": self.project_root,
            "project_identity": self.project_identity,
            "target_kind": self.target_kind,
            "remote_name": self.remote_name,
            "workspace_label": self.workspace_label,
            "workspace_mode": self.workspace_mode,
            "argv": list(self.argv),
            "cwd_relative": self.cwd_relative,
            "execution_profile": self.execution_profile,
            "output_profile": self.output_profile,
            "output_profile_definition": dict(self.output_profile_definition or {}),
            "deadline_seconds": self.deadline_seconds,
            "deadline_source": self.deadline_source,
            "deadline_reminder": self.deadline_reminder,
            "stall_seconds": self.stall_seconds,
            "cancel_grace_seconds": self.cancel_grace_seconds,
            "cancel_on_stall": self.cancel_on_stall,
            "cleanup_policy": self.cleanup_policy,
            "execution_policy_provenance": dict(self.execution_policy_provenance or {}),
            "request_id": self.request_id,
            "parent_job_id": self.parent_job_id,
            "retry_of_job_id": self.retry_of_job_id,
            "attempt": self.attempt,
            "environment_keys": list(self.environment_keys),
            "artifact_paths": list(self.artifact_paths),
            "depends_on": list(self.depends_on),
            "failure_policy": self.failure_policy,
            "compatibility_differences": list(self.compatibility_differences),
            "source": asdict(self.source),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))

    def canonical_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()
