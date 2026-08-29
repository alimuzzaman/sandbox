"""Fail-closed pre-start and periodic health gates for credential bindings."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import Any

from .credential_binding import CredentialBinding


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_INTERVAL_SECONDS = 60.0


def _admissible(value: Any, *, key: str = "admissible") -> bool:
    try:
        if hasattr(value, key):
            return bool(getattr(value, key))
        if isinstance(value, dict):
            return value.get(key) is True
        return value is True
    except Exception:
        return False


class CredentialHealthMonitor:
    """Observe proof and digest state without repairing or widening egress."""

    def __init__(
        self,
        *,
        supervisor,
        binding_loader,
        proof,
        egress,
        report=None,
        interval_seconds: float = 10.0,
        clock=None,
        utc_clock=None,
    ) -> None:
        if supervisor is None or not callable(getattr(supervisor, "revoke_binding", None)):
            raise ValueError("credential health supervisor is required")
        if not callable(binding_loader) or not callable(proof) or not callable(egress):
            raise ValueError("credential health observers are required")
        if not isinstance(interval_seconds, (int, float)) or isinstance(interval_seconds, bool) \
                or not 0 < interval_seconds <= MAX_INTERVAL_SECONDS:
            raise ValueError("credential health interval is invalid")
        if report is not None and not callable(report):
            raise ValueError("credential health report observer is invalid")
        self.supervisor = supervisor
        self.binding_loader = binding_loader
        self.proof = proof
        self.egress = egress
        self.report = report
        self.interval_seconds = float(interval_seconds)
        self.clock = clock or time.monotonic
        self.utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))
        self._last_periodic: dict[str, float] = {}

    @staticmethod
    def _digest(value: Any, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            return None
        return value

    def _close(self, binding: CredentialBinding, code: str) -> dict[str, Any]:
        try:
            closed = self.supervisor.revoke_binding(
                binding.binding_id, binding_version=binding.version,
                timeout_seconds=min(self.interval_seconds, 5.0),
            )
        except Exception:
            closed = {"ok": False, "drained": False}
        return {
            "ok": False,
            "state": "blocked",
            "binding_id": binding.binding_id,
            "binding_version": binding.version,
            "reason": {"code": code},
            "admission_closed": True,
            "drain": {
                "ok": bool(closed.get("ok")) if isinstance(closed, dict) else False,
                "drained": bool(closed.get("drained")) if isinstance(closed, dict) else False,
            },
            "mutated": True,
        }

    def _binding(self, binding_id: str) -> CredentialBinding | None:
        if not isinstance(binding_id, str) or not _IDENTITY.fullmatch(binding_id):
            return None
        try:
            value = self.binding_loader(binding_id)
        except Exception:
            return None
        return value if isinstance(value, CredentialBinding) else None

    def pre_start(
        self,
        binding_id: str,
        *,
        policy_digest: str,
        egress_digest: str,
        broker_digest: str,
    ) -> dict[str, Any]:
        """Check all gates immediately before a binding can be used."""

        binding = self._binding(binding_id)
        if binding is None:
            # There is no binding object to close, but the caller still gets a
            # bounded refusal and no loader diagnostics.
            return {"ok": False, "state": "blocked", "binding_id": binding_id,
                    "reason": {"code": "binding_unknown"}, "admission_closed": True,
                    "mutated": False}
        expected = {
            "policy_digest": self._digest(policy_digest, "policy"),
            "egress_digest": self._digest(egress_digest, "egress"),
            "broker_digest": self._digest(broker_digest, "broker"),
        }
        for name, value in expected.items():
            if value is None:
                return self._close(binding, f"{name}_invalid")
            if getattr(binding, name) != value:
                return self._close(binding, f"{name}_mismatch")
        if binding.state != "ready":
            return self._close(binding, "binding_not_ready")
        try:
            if binding.is_expired(now=self.utc_clock()):
                return self._close(binding, "binding_expired")
        except Exception:
            return self._close(binding, "binding_expired")
        try:
            proof = self.proof(binding)
        except Exception:
            proof = False
        if not _admissible(proof):
            return self._close(binding, "proof_unavailable")
        try:
            egress = self.egress(binding)
        except Exception:
            egress = False
        if not _admissible(egress, key="allowed"):
            return self._close(binding, "egress_not_authorized")
        if self.report is not None:
            try:
                report = self.report(binding)
                if hasattr(report, "require_admission"):
                    report.require_admission()
                elif not _admissible(report):
                    return self._close(binding, "capability_unproven")
            except Exception:
                return self._close(binding, "capability_unproven")
        return {
            "ok": True,
            "state": "ready",
            "binding_id": binding.binding_id,
            "binding_version": binding.version,
            "admission_closed": False,
            "mutated": False,
        }

    def periodic(self, binding_id: str, **digests: str) -> dict[str, Any]:
        """Run at most once per configured interval; drift closes admission."""

        if not isinstance(binding_id, str) or not _IDENTITY.fullmatch(binding_id):
            return {"ok": False, "state": "blocked", "binding_id": binding_id,
                    "reason": {"code": "binding_invalid"},
                    "admission_closed": True, "mutated": False}
        now = self.clock()
        if not isinstance(now, (int, float)) or isinstance(now, bool):
            return {"ok": False, "state": "blocked", "binding_id": binding_id,
                    "reason": {"code": "health_clock_invalid"},
                    "admission_closed": True, "mutated": False}
        previous = self._last_periodic.get(binding_id)
        if previous is not None and now - previous < self.interval_seconds:
            return {"ok": True, "state": "skipped", "binding_id": binding_id,
                    "next_check_in": max(0.0, self.interval_seconds - (now - previous)),
                    "mutated": False}
        self._last_periodic[binding_id] = float(now)
        return self.pre_start(
            binding_id,
            policy_digest=digests.get("policy_digest"),
            egress_digest=digests.get("egress_digest"),
            broker_digest=digests.get("broker_digest"),
        )


__all__ = ["CredentialHealthMonitor", "MAX_INTERVAL_SECONDS"]
