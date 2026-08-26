"""Restart recovery for managed Credential Vault binding metadata."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from sandbox.isolation.credential_binding import CredentialBinding
from sandbox.isolation.credential_resolver import BrokerLease


_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class CredentialRecoveryService:
    """Move persisted bindings through pending before fresh proof and lease use."""

    def __init__(self, *, repository, resolver, proof, egress, report=None,
                 supervisor=None, owner=None, utc_clock=None):
        if repository is None or not callable(getattr(repository, "get", None)) \
                or not callable(getattr(repository, "transition", None)):
            raise ValueError("credential recovery repository is required")
        if resolver is None or not callable(getattr(resolver, "issue", None)):
            raise ValueError("credential recovery resolver is required")
        if not callable(proof) or not callable(egress):
            raise ValueError("credential recovery proof observers are required")
        if report is not None and not callable(report):
            raise ValueError("credential recovery report observer is invalid")
        if owner is not None and (not isinstance(owner, str) or not owner or len(owner) > 512
                                  or any(ord(char) < 32 or ord(char) == 127 for char in owner)):
            raise ValueError("credential recovery owner is invalid")
        self.repository = repository
        self.resolver = resolver
        self.proof = proof
        self.egress = egress
        self.report = report
        self.supervisor = supervisor
        self.owner = owner
        self.utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _digest(value: Any) -> bool:
        return isinstance(value, str) and bool(_DIGEST.fullmatch(value))

    @staticmethod
    def _allowed(value: Any, key: str) -> bool:
        if hasattr(value, key):
            return bool(getattr(value, key))
        if isinstance(value, dict):
            if key == "allowed":
                return value.get("allowed") is True
            return value.get(key) is True or value.get("admissible") is True
        return value is True

    @staticmethod
    def _result(binding, *, ok: bool, state: str, code: str, mutated: bool = False,
               fresh_lease: bool = False) -> dict[str, Any]:
        return {
            "ok": ok,
            "state": state,
            "binding_id": binding.binding_id,
            "binding_version": binding.version,
            "reason": {"code": code},
            "fresh_lease": fresh_lease,
            "mutated": mutated,
        }

    def _pending(self, binding: CredentialBinding) -> CredentialBinding | None:
        if self.owner is not None and binding.owner != self.owner:
            return None
        if binding.state == "credential_pending":
            return binding
        if binding.state in {"revoked", "expired", "revoking"}:
            return None
        try:
            return self.repository.transition(
                binding.binding_id, "credential_pending", expected_version=binding.version,
                owner=self.owner or binding.owner,
            )
        except Exception:
            return None

    def recover(
        self,
        binding_id: str,
        *,
        policy_digest: str,
        egress_digest: str,
        broker_digest: str,
    ) -> dict[str, Any]:
        """Reconcile one binding after broker/instance restart."""

        try:
            binding = (self.repository.get(binding_id, owner=self.owner)
                       if self.owner is not None else self.repository.get(binding_id))
        except Exception:
            binding = None
        if not isinstance(binding, CredentialBinding):
            return {"ok": False, "state": "blocked", "binding_id": binding_id,
                    "reason": {"code": "binding_unknown"}, "fresh_lease": False,
                    "mutated": False}
        if self.supervisor is not None:
            # A prior broker process must not keep serving while desired state is
            # being reconciled.  Ignore only bounded shutdown diagnostics.
            try:
                self.supervisor.shutdown()
            except Exception:
                pass
        if binding.state == "ready":
            pending = self._pending(binding)
            if pending is None:
                return self._result(binding, ok=False, state="blocked",
                                    code="recovery_transition_failed")
            try:
                self.resolver.invalidate(binding.binding_id, binding_version=binding.version)
            except Exception:
                pass
        else:
            pending = self._pending(binding)
        if pending is None:
            return self._result(binding, ok=False, state="blocked",
                                code="binding_recovery_denied")
        expected = {
            "policy_digest": policy_digest,
            "egress_digest": egress_digest,
            "broker_digest": broker_digest,
        }
        for name, value in expected.items():
            if not self._digest(value):
                return self._result(pending, ok=False, state="blocked", code=f"{name}_invalid", mutated=True)
            if getattr(pending, name) != value:
                return self._result(pending, ok=False, state="blocked", code=f"{name}_mismatch", mutated=True)
        try:
            if pending.is_expired(now=self.utc_clock()):
                expired = self.repository.transition(
                    pending.binding_id, "expired", expected_version=pending.version,
                    owner=self.owner or pending.owner,
                )
                return self._result(expired, ok=False, state="expired", code="binding_expired", mutated=True)
        except Exception:
            return self._result(pending, ok=False, state="blocked", code="binding_expired", mutated=True)
        try:
            if not self._allowed(self.proof(pending), "admissible"):
                return self._result(pending, ok=False, state="blocked", code="proof_unavailable", mutated=True)
        except Exception:
            return self._result(pending, ok=False, state="blocked", code="proof_unavailable", mutated=True)
        try:
            if not self._allowed(self.egress(pending), "allowed"):
                return self._result(pending, ok=False, state="blocked", code="egress_not_authorized", mutated=True)
        except Exception:
            return self._result(pending, ok=False, state="blocked", code="egress_not_authorized", mutated=True)
        if self.report is not None:
            try:
                observed = self.report(pending)
                if hasattr(observed, "require_admission"):
                    observed.require_admission()
                elif not self._allowed(observed, "admissible"):
                    return self._result(pending, ok=False, state="blocked", code="capability_unproven", mutated=True)
            except Exception:
                return self._result(pending, ok=False, state="blocked", code="capability_unproven", mutated=True)
        # The resolver requires a ready binding, so form an in-memory candidate
        # only after every gate has passed.  It is persisted with a CAS below.
        try:
            ready = pending.transition("ready", now=self.utc_clock())
            lease = self.resolver.issue(ready)
        except Exception:
            return self._result(pending, ok=False, state="blocked", code="lease_unavailable", mutated=True)
        try:
            persisted = self.repository.transition(
                pending.binding_id, "ready", expected_version=pending.version,
                owner=self.owner or pending.owner,
            )
        except Exception:
            try:
                lease.invalidate()
            except Exception:
                pass
            return self._result(pending, ok=False, state="blocked", code="recovery_conflict", mutated=True)
        return self._result(persisted, ok=True, state="ready", code="recovered",
                            mutated=True, fresh_lease=isinstance(lease, BrokerLease) or lease is not None)


__all__ = ["CredentialRecoveryService"]
