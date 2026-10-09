"""The single compatibility rule (spec 061 FR-002, FR-003, FR-008)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from sandbox.remote_runtime.protocol import ControlProtocol, local_protocol

COMPATIBLE = "compatible"
PROTOCOL_NEWER = "protocol_newer"
PROTOCOL_TOO_OLD = "protocol_too_old"
EXACT_ONLY = "exact_only"
UNKNOWN = "unknown"
STATES = (COMPATIBLE, PROTOCOL_NEWER, PROTOCOL_TOO_OLD, EXACT_ONLY, UNKNOWN)

STRICT_ENVIRONMENT = "SANDBOX_STRICT_RUNTIME"


def strict_requested(flag: bool | None = None, environ=None) -> bool:
    """Strict mode from the ``--strict-runtime`` flag or ``SANDBOX_STRICT_RUNTIME=1``."""
    if flag:
        return True
    environ = os.environ if environ is None else environ
    return environ.get(STRICT_ENVIRONMENT, "").strip() == "1"


@dataclass(frozen=True)
class Verdict:
    state: str
    ok: bool
    reason: str
    local_revision: str | None
    installed_revision: str | None
    local_protocol: ControlProtocol | None
    installed_protocol: ControlProtocol | None
    strict: bool = False
    extra: dict = field(default_factory=dict)

    def as_mapping(self) -> dict:
        return {
            "state": self.state, "ok": self.ok, "reason": self.reason,
            "strict": self.strict,
            "local": {"revision": self.local_revision,
                      "protocol": self.local_protocol.as_mapping()
                      if self.local_protocol else None},
            "installed": {"revision": self.installed_revision,
                          "protocol": self.installed_protocol.as_mapping()
                          if self.installed_protocol else None},
        }


def compatibility(*, local_revision: str | None, installed_revision: str | None,
                  installed_protocol: ControlProtocol | None,
                  determinate: bool = True, strict: bool = False,
                  local: ControlProtocol | None = None) -> Verdict:
    """Decide whether this controller may operate the installed runtime.

    ``determinate`` is False when the installed state cannot be established
    (probe failure, indeterminate rollback); the verdict is then ``unknown``.
    Strict mode additionally requires the exact installed revision.
    """
    local = local or local_protocol()

    def make(state, ok, reason):
        return Verdict(state, ok, reason, local_revision, installed_revision,
                       local, installed_protocol, strict)

    if not determinate or not installed_revision or not local_revision:
        return make(UNKNOWN, False, "installed_runtime_state_unknown")
    same_revision = installed_revision == local_revision
    if installed_protocol is None:
        if same_revision:
            return make(EXACT_ONLY, True, "undeclared_runtime_same_revision")
        return make(EXACT_ONLY, False, "undeclared_runtime_requires_exact_revision")
    if local.spoken > installed_protocol.spoken:
        state, ok, reason = PROTOCOL_NEWER, False, "controller_protocol_newer_than_runtime"
    elif local.spoken < installed_protocol.oldest_served:
        state, ok, reason = PROTOCOL_TOO_OLD, False, "controller_protocol_older_than_runtime_serves"
    else:
        state, ok, reason = COMPATIBLE, True, "protocol_served"
    if strict and ok and not same_revision:
        return make(state, False, "strict_requires_exact_revision")
    return make(state, ok, reason)


def admitted(status) -> tuple[bool, str]:
    """Read the verdict a ``remote service status`` envelope already carries.

    Returns ``(ok, state)`` with ``state`` always one of :data:`STATES`. The
    envelope is remote-derived input, so only the finite ``state`` and the
    boolean ``ok`` are trusted. An envelope without a verdict (an older
    controller module or a test double) falls back to the exact rule.
    """
    if not isinstance(status, dict):
        return False, UNKNOWN
    verdict = status.get("compatibility")
    if isinstance(verdict, dict):
        state = verdict.get("state")
        if state in STATES and isinstance(verdict.get("ok"), bool):
            return verdict["ok"] and state != UNKNOWN, state
        return False, UNKNOWN
    revision_state = status.get("runtime_revision_state")
    if revision_state == "match":
        return True, EXACT_ONLY
    if revision_state == "mismatch":
        return False, EXACT_ONLY
    return False, UNKNOWN


def explicitly_refused(status) -> bool:
    """True when the envelope carries a verdict that refuses the effect.

    Callers that also accept ``runtime_revision_state == "match"`` must check
    this first: a strict refusal (``strict_pin_unverifiable``, a broken pin)
    leaves the revisions matching, and only the verdict says no. An
    indeterminate (``unknown``) non-strict verdict is not an explicit refusal;
    those callers keep their own handling of unknown state.
    """
    if not isinstance(status, dict):
        return False
    verdict = status.get("compatibility")
    if not isinstance(verdict, dict) or verdict.get("ok") is not False:
        return False
    return verdict.get("strict") is True or verdict.get("state") != UNKNOWN
