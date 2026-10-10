"""The readiness refusal (spec 063 US2).

Raised on the controller before any transfer, so it lives outside the
transports whose payload keys form the controller-to-runtime protocol
(``sandbox/remote_runtime/shapes.py``): nothing here is sent to or read from
the remote runtime.
"""
from __future__ import annotations

from sandbox.transports.remote_jobs import RemoteJobAdmissionError, RemoteJobTransportError


class RemoteNotReadyError(RemoteJobAdmissionError):
    """A readiness row refused the submission before any byte was sent (spec 063).

    An admission refusal, so every caller that already returns
    ``RemoteJobAdmissionError.to_payload()`` (CLI edge, MCP job tools,
    ``run_tests``, ``e2e``) returns this envelope unchanged.
"""

    retryable = False
    _ASPECTS = frozenset({"registration", "reachability", "runtime_compatibility",
                          "capacity", "ownership_repair", "handoff"})

    def __init__(self, row: dict, *, remote: str | None = None) -> None:
        aspect = row.get("aspect") if isinstance(row, dict) else None
        aspect = aspect if aspect in self._ASPECTS else "registration"
        reason = row.get("reason") if isinstance(row.get("reason"), str) else "not_ready"
        remedy = row.get("remedy") if isinstance(row.get("remedy"), str) else None
        self.code = f"remote_not_ready_{aspect}"
        self.remote = remote
        detail = {"aspect": aspect, "reason": reason, "bytes_transferred": 0}
        if remedy is not None:
            detail["remedy"] = remedy
        self._decision = {}
        # Skip the admission constructor: there is no capacity decision here.
        RemoteJobTransportError.__init__(
            self, f"remote is not ready: {aspect} ({reason})",
            retryable=False, detail=detail)

    def to_payload(self, *, remote: str | None = None,
                   operation: str | None = None) -> dict:
        payload = RemoteJobTransportError.to_payload(
            self, remote=remote or self.remote, operation=operation)
        # A definite refusal before deployment: nothing was accepted or staged.
        payload["status"] = "blocked"
        payload["acceptance"] = None
        payload["side_effects"] = {"staging_started": False, "bytes_transferred": 0}
        if "remedy" in self.detail:
            payload["remedy"] = self.detail["remedy"]
        return payload


def human_refusal(payload: dict) -> str | None:
    """The one-line human message for a readiness refusal payload, or None.

    Kept here so runtime-side renderers need not read readiness keys (their
    payload keys form the control-protocol fingerprint)."""
    if not isinstance(payload, dict) or payload.get("status") != "blocked":
        return None
    remedy = payload.get("remedy")
    if not isinstance(remedy, str) or not remedy.startswith("./sb "):
        return None
    return (f"{payload.get('error')} ({payload.get('code')}). Nothing was transferred. "
            f"Remedy: {remedy}")


_REGISTRATION_CODES = frozenset({"unknown_remote", "remote_not_provisioned"})


def registration_refusal(exc) -> "RemoteNotReadyError | None":
    """The readiness refusal for a target resolution that failed on the
    remote's registration, or None for any other resolution failure."""
    code = getattr(exc, "code", None)
    if code not in _REGISTRATION_CODES:
        return None
    from sandbox.readiness.rows import registration
    name = getattr(exc, "remote_name", None)
    name = name if isinstance(name, str) else None
    return RemoteNotReadyError(registration(code, name), remote=name)


def raise_registration_refusal(exc) -> None:
    """Raise ``registration_refusal(exc)`` when there is one; the caller's
    admission handling then renders the blocked envelope."""
    refusal = registration_refusal(exc)
    if refusal is not None:
        raise refusal from exc
