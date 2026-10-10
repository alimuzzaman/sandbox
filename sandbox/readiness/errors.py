"""The readiness refusal (spec 063 US2).

Raised on the controller before any transfer, so it lives outside the
transports whose payload keys form the controller-to-runtime protocol
(``sandbox/remote_runtime/shapes.py``): nothing here is sent to or read from
the remote runtime.
"""
from __future__ import annotations

from sandbox.transports.remote_jobs import RemoteJobTransportError


class RemoteNotReadyError(RemoteJobTransportError):
    """A readiness row refused the submission before any byte was sent (spec 063)."""

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
        super().__init__(f"remote is not ready: {aspect} ({reason})",
                         retryable=False, detail=detail)

    def to_payload(self, *, remote: str | None = None,
                   operation: str | None = None) -> dict:
        payload = super().to_payload(remote=remote or self.remote, operation=operation)
        # A definite refusal before deployment: nothing was accepted or staged.
        payload["status"] = "blocked"
        payload["acceptance"] = None
        payload["side_effects"] = {"staging_started": False, "bytes_transferred": 0}
        return payload
