"""Explicit observation-only incident settlement through the shared owner."""

from __future__ import annotations

import time

from .models import _digest, _integer, _text
from .settlement_models import SettlementDataAssessment, SettlementObservation, SettlementPlan


SETTLEMENT_CODES = frozenset({
    "abandoned_with_effects", "artifact_invalid", "authority_missing", "authority_mismatch",
    "approval_expired", "request_conflict", "generation_conflict", "settlement_conflict",
    "observation_unavailable", "not_quiescent", "evidence_changed", "retention_full",
    "persistence_uncertain", "custody_pending", "target_busy", "path_unsafe", "remote_runtime_revision_mismatch",
    "process_owner_unavailable", "container_paused", "container_restarting", "container_state_invalid",
})


class SettlementError(ValueError):
    def __init__(self, code: str):
        self.code = code if code in SETTLEMENT_CODES else "artifact_invalid"
        super().__init__(self.code)


def _code(exc: Exception, fallback: str) -> str:
    code = getattr(exc, "code", None)
    if code is None and len(exc.args) == 1 and type(exc.args[0]) is str:
        code = exc.args[0]
    if code == "operation_busy":
        return "target_busy"
    return code if type(code) is str and code in SETTLEMENT_CODES else fallback


class SettlementService:
    def __init__(self, *, repository, observer, approval_store, clock=None):
        self.repository = repository
        self.observer = observer
        self.approval_store = approval_store
        self.clock = clock or time.time

    @staticmethod
    def _active(state, *, target, transaction_digest, expected_generation):
        if state["generation"] != expected_generation:
            raise SettlementError("generation_conflict")
        active = state.get("active")
        if (type(active) is not dict or active.get("phase") != "uncertain"
                or active.get("effect_entered") is not True
                or active.get("transaction_digest") != transaction_digest
                or active.get("recovery_context", {}).get("target", {}).get("target_identity") != target
                or state.get("recovery_provisional") is not None):
            raise SettlementError("settlement_conflict")
        return active

    def _observe(self, active, generation):
        try:
            value = self.observer.observe(transaction=active, generation=generation)
            return value if type(value) is SettlementObservation else SettlementObservation.from_mapping(value)
        except Exception as exc:
            raise SettlementError(_code(exc, "observation_unavailable")) from None

    def plan(self, *, target: str, request_id: str, transaction_digest: str,
             expected_generation: int, data_assessment) -> SettlementPlan:
        _text(target, identity=True); _text(request_id, identity=True)
        _digest(transaction_digest); _integer(expected_generation)
        assessment = data_assessment if type(data_assessment) is SettlementDataAssessment \
            else SettlementDataAssessment.from_mapping(data_assessment)
        with self.repository.operation_transaction(target):
            state = self.repository.snapshot(target)
            if any(request_id in state.get(name, {}) for name in (
                    "results", "tombstones", "recovery_results", "settlements")):
                raise SettlementError("request_conflict")
            active = self._active(state, target=target, transaction_digest=transaction_digest,
                                  expected_generation=expected_generation)
            observation = self._observe(active, expected_generation)
            return SettlementPlan.create(request_id=request_id,
                active_request_id=active["request_id"], active_request_digest=active["request_digest"],
                transaction_digest=transaction_digest, target=active["recovery_context"]["target"],
                generation=expected_generation,
                current_generation_digest=(state.get("current") or {}).get("generation_digest"),
                observation=observation, data_assessment=assessment)

    @staticmethod
    def _result(plan, *, code: str, record=None):
        result = {"schema_version": 1, "operation": "settle", "ok": code == "abandoned_with_effects",
            "code": code, "request_id": plan.request_id, "plan_digest": plan.plan_digest,
            "transaction_digest": plan.transaction_digest,
            "starting_generation": plan.generation, "resulting_generation": plan.generation,
            "current_generation_digest": plan.current_generation_digest,
            "terminal_digest": None}
        if record is not None:
            result["terminal_digest"] = record["terminal_receipt"]["terminal_digest"]
            result["original_result_class"] = record["original_result"]["result"]["result_class"]
        return result

    def _release(self, target, plan, record):
        try:
            self.repository.release_settlement_pin(target, record)
        except Exception:
            return self._result(plan, code="custody_pending", record=record)
        return self._result(plan, code="abandoned_with_effects", record=record)

    def apply(self, plan: SettlementPlan, *, approval_digest: str) -> dict:
        if type(plan) is not SettlementPlan:
            raise SettlementError("artifact_invalid")
        _digest(approval_digest)
        target = plan.target.target_identity
        try:
            with self.repository.operation_transaction(target):
                # Durable replay precedes authority/observation access. Expired
                # admission cannot invalidate an already committed ownership result.
                record = self.repository.lookup_settlement(target, request_id=plan.request_id,
                    plan_digest=plan.plan_digest, approval_digest=approval_digest)
                if record is not None:
                    return self._release(target, plan, record)
                state = self.repository.snapshot(target)
                active = self._active(state, target=target, transaction_digest=plan.transaction_digest,
                                      expected_generation=plan.generation)
                approval = self.approval_store.read_settlement(plan, approval_digest, now=int(self.clock()))
                first = self._observe(active, plan.generation)
                if first != plan.observation:
                    raise SettlementError("evidence_changed")
                second = self._observe(active, plan.generation)
                if second != first:
                    raise SettlementError("evidence_changed")
                authorized_at = int(self.clock())
                checked = self.approval_store.read_settlement(plan, approval_digest, now=authorized_at)
                if checked != approval:
                    raise SettlementError("authority_mismatch")
                try:
                    record = self.repository.commit_settlement(target, plan, approval,
                                                               authorized_at=authorized_at)
                except Exception as exc:
                    # Even a lost post-replace acknowledgement must not release
                    # custody. An exact later call consults the durable record.
                    return self._result(plan, code=_code(exc, "persistence_uncertain"))
                return self._release(target, plan, record)
        except Exception as exc:
            return self._result(plan, code=_code(exc, "settlement_conflict"))
