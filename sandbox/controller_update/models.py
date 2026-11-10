"""Types for controller auto-update (spec 072, data-model.md).

External data (the state file, probe output, git output) is parsed into these
types at the boundary. A value that does not parse is absent, never partly
valid.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

_REVISION_RE = re.compile(r"[0-9a-f]{40}")
_REMOTE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def parse_revision(value: object) -> str | None:
    """Return ``value`` when it is a full 40-hex lowercase commit id."""
    if isinstance(value, str) and _REVISION_RE.fullmatch(value):
        return value
    return None


def parse_remote_name(value: object) -> str | None:
    if isinstance(value, str) and _REMOTE_RE.fullmatch(value):
        return value
    return None


HintSource = Literal["install", "probe"]
HINT_SOURCES: tuple[str, ...] = ("install", "probe")


@dataclass(frozen=True)
class Hint:
    """An installed source revision a remote reported. Untrusted input."""

    remote: str
    revision: str
    source: HintSource
    recorded_at: float


class SkipReason(enum.Enum):
    """Why no update happened. ``notice`` says whether it prints a line."""

    DISABLED = ("disabled", False)
    MACHINE_OUTPUT = ("machine_output", False)
    JOB_CHILD = ("job_child", False)
    REEXEC_GUARD = ("reexec_guard", False)
    NOT_CLI = ("not_cli", False)
    NOT_REMOTE_COMMAND = ("not_remote_command", False)
    NO_HINT = ("no_hint", False)
    NOT_GIT = ("not_git", False)
    MEMOIZED = ("memoized", False)
    DETACHED = ("detached", True)
    NO_UPSTREAM = ("no_upstream", True)
    ORIGIN_NOT_GITHUB = ("origin_not_github", True)
    DIRTY = ("dirty", True)
    LOCKED = ("locked", True)
    FETCH_FAILED = ("fetch_failed", True)
    NOT_ON_UPSTREAM = ("not_on_upstream", True)
    NOT_DESCENDANT = ("not_descendant", True)
    FF_FAILED = ("ff_failed", True)

    def __init__(self, code: str, notice: bool) -> None:
        self.code = code
        self.notice = notice


# Refusals that are remembered, with how long (Policy supplies the values).
MEMOIZED_REASONS = (SkipReason.NOT_ON_UPSTREAM, SkipReason.NOT_DESCENDANT,
                    SkipReason.FETCH_FAILED)


@dataclass(frozen=True)
class Refusal:
    revision: str
    upstream: str
    reason: SkipReason
    until: float


@dataclass(frozen=True)
class CheckoutState:
    """The controller checkout as git reports it."""

    root: Path
    head: str
    branch: str | None          # None when detached
    upstream_remote: str | None
    upstream_branch: str | None  # short name, e.g. "latest"
    origin_url: str | None       # raw configured value, no insteadOf applied
    clean: bool                  # tracked files and index

    @property
    def upstream(self) -> str | None:
        if self.upstream_remote and self.upstream_branch:
            return f"{self.upstream_remote}/{self.upstream_branch}"
        return None


# ---- decisions -------------------------------------------------------------

@dataclass(frozen=True)
class NoAction:
    """Silent: the command runs normally."""

    reason: SkipReason


@dataclass(frozen=True)
class Skip:
    """One notice line, then the command runs on the current code."""

    reason: SkipReason
    notice: str


@dataclass(frozen=True)
class Updated:
    """Fast-forwarded and refreshed; the command is re-executed."""

    old: str
    new: str
    upstream: str
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlreadyMoved:
    """Another process moved HEAD to a verified revision; re-execute."""

    old: str
    new: str


@dataclass(frozen=True)
class RolledBack:
    """The refresh failed and HEAD is back at ``old``."""

    old: str
    attempted: str
    notice: str


@dataclass(frozen=True)
class Stranded:
    """The refresh failed and the rollback failed too; exit 1."""

    head: str | None
    notice: str


Decision = Union[NoAction, Skip, Updated, AlreadyMoved, RolledBack, Stranded]


@dataclass(frozen=True)
class Policy:
    fetch_timeout: float = 15.0
    git_timeout: float = 10.0
    refresh_timeout: float = 300.0
    refusal_ttl: float = 6 * 3600.0
    fetch_failure_ttl: float = 15 * 60.0
    lock_wait: float = 2.0

    def ttl_for(self, reason: SkipReason) -> float:
        if reason is SkipReason.FETCH_FAILED:
            return self.fetch_failure_ttl
        return self.refusal_ttl


@dataclass(frozen=True)
class State:
    """Parsed contents of the machine-local state file."""

    hints: tuple[Hint, ...] = ()
    refusals: tuple[Refusal, ...] = field(default=())
