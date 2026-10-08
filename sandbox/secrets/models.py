"""Transport-neutral request, policy, and result models for secrets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


MAX_SOURCE_BYTES = 1_048_576
MAX_ENTRIES = 4_096
MAX_VALUE_BYTES = 65_536
MAX_SELECTED_KEYS = 100
MAX_OUTPUT_BYTES = 1_048_576
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 1_800
# Operator-only session mode (spec 059): one foreground child, bounded by a
# lifetime the broker enforces itself. Ordinary run limits above are unchanged.
DEFAULT_SESSION_SECONDS = 28_800
MAX_SESSION_SECONDS = 43_200
END_REASONS = ("lifetime_expired", "interrupted", "hangup", "child_exited")


class SecretBrokerError(RuntimeError):
    """Bounded public failure with no secret-bearing detail."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class SourcePolicy:
    alias: str
    scope: str
    path: Path
    format: str = "dotenv"
    mcp_modes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SafeSource:
    policy: SourcePolicy
    content: bytes
    signature: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class UseProfile:
    name: str
    source: str
    key: str
    argv: tuple[str, ...]
    destination: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = MAX_OUTPUT_BYTES
    mcp: bool = False


@dataclass(frozen=True)
class BrokerConfig:
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    use_profiles: dict[str, UseProfile] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    termination: str
    output: str
    truncated: bool
    elapsed_seconds: float

    def as_dict(self) -> dict[str, Any]:
        if self.elapsed_seconds < 1:
            elapsed_class = "under_1s"
        elif self.elapsed_seconds < 10:
            elapsed_class = "1_to_10s"
        elif self.elapsed_seconds < 60:
            elapsed_class = "10_to_60s"
        else:
            elapsed_class = "60s_plus"
        return {
            "exit_code": self.exit_code,
            "termination": self.termination,
            "output": self.output,
            "truncated": self.truncated,
            "elapsed_class": elapsed_class,
        }


_SESSION_ELAPSED_CLASSES = (
    (1, "under_1s"),
    (10, "1_to_10s"),
    (60, "10_to_60s"),
    (600, "1_to_10m"),
    (3_600, "10_to_60m"),
    (14_400, "1_to_4h"),
    (28_800, "4_to_8h"),
    (43_200, "8_to_12h"),
)


@dataclass(frozen=True)
class SessionResult:
    """Metadata-only outcome of one supervised session; never holds output."""

    end_reason: str
    exit_code: int | None
    elapsed_seconds: float
    dropped_chunks: int
    lifetime_seconds: int

    def elapsed_class(self) -> str:
        for bound, name in _SESSION_ELAPSED_CLASSES:
            if self.elapsed_seconds < bound:
                return name
        return "12h_plus"

    def as_dict(self) -> dict[str, Any]:
        return {
            "end_reason": self.end_reason,
            "exit_code": self.exit_code,
            "elapsed_class": self.elapsed_class(),
            "dropped_chunks": self.dropped_chunks,
            "lifetime_seconds": self.lifetime_seconds,
        }


def success(operation: str, **fields: Any) -> dict[str, Any]:
    return {"ok": True, "operation": operation, **fields}


def failure(operation: str, error: SecretBrokerError, **fields: Any) -> dict[str, Any]:
    return {"ok": False, "operation": operation, **fields, "error": error.as_dict()}


def require_one_key(keys: Iterable[str] | None) -> str:
    selected = tuple(keys or ())
    if len(selected) != 1:
        raise SecretBrokerError(
            "mode_requires_one_key", "this operation requires exactly one key"
        )
    return selected[0]
