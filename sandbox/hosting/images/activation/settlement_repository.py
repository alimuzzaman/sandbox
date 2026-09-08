"""Pure settlement candidate and persistence validation.

The outer host repository owns locking and durable replacement.  This module
only validates a nested state value and proposes the one legal settlement
transition: retain the uncertain transaction and record an immutable terminal
settlement while leaving generation/current unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import (
    ActivationContractError,
    ActivationTransaction,
    _closed,
    _digest,
    _integer,
    _safe_mapping,
    _text,
    activation_digest,
    canonical_bytes,
)
from .settlement_models import SettlementApproval, SettlementPlan


MAX_SETTLEMENTS = 32
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_LEASE_ID = re.compile(r"activation-lease/[0-9a-f]{48}\Z")
_HOLDER_ID = re.compile(r"activation-owner/[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_ACCEPTANCE_ID = re.compile(r"host-acceptance/[0-9a-f]{64}\Z")
_SETTLEMENT_FIELDS = frozenset({
    "schema_version", "settlement_request_id", "plan_digest", "approval_digest",
    "active_request_id", "active_request_digest", "transaction_digest", "generation",
    "current_generation_digest", "plan", "original_transaction", "original_result",
    "proof_pin", "terminal_receipt",
    "approval", "authorized_at", "sequence", "previous_terminal_digest",
})
_TERMINAL_FIELDS = frozenset({
    "schema_version", "code", "result_class", "ok", "settlement_request_id",
    "plan_digest", "approval_digest", "request_id", "request_digest",
    "transaction_digest", "starting_generation", "resulting_generation",
    "generation_digest", "current_generation_digest", "terminal_digest",
    "authorized_at", "sequence", "previous_terminal_digest",
})


class SettlementRepositoryError(ValueError):
    """Stable refusal/persistence code for the pure settlement boundary."""

    def __init__(self, code: str = "settlement_conflict") -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str = "settlement_conflict") -> None:
    raise SettlementRepositoryError(code)


def _copy(value: object) -> Any:
    try:
        return json.loads(canonical_bytes(value))
    except (ActivationContractError, TypeError, ValueError):
        _fail("persistence_uncertain")


def _proof_pin(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
            "lease_id", "holder", "phase", "proof_digest", "host_acceptance_receipt"}:
        _fail("persistence_uncertain")
    if value["phase"] != "accepted" or type(value["lease_id"]) is not str \
            or _LEASE_ID.fullmatch(value["lease_id"]) is None \
            or type(value["holder"]) is not str or _HOLDER_ID.fullmatch(value["holder"]) is None \
            or type(value["proof_digest"]) is not str \
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["proof_digest"]) \
            or type(value["host_acceptance_receipt"]) is not str \
            or _ACCEPTANCE_ID.fullmatch(value["host_acceptance_receipt"]) is None:
        _fail("persistence_uncertain")
    return value


def _validate_original_transaction(value: object) -> dict[str, Any]:
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        _fail("persistence_uncertain")
    try:
        if value["schema_version"] == 1:
            raw = {
                **value,
                "init_receipts": tuple(value["init_receipts"]),
                "init_steps": tuple(value["init_steps"]),
            }
            ActivationTransaction(**raw)
        elif value["schema_version"] == 2:
            # Lazy import avoids the activation repository <-> settlement
            # repository cycle when the outer decoder installs this hook.
            from .v2_repository import validate_transaction_v2
            validate_transaction_v2(value)
        else:
            _fail("persistence_uncertain")
    except (ActivationContractError, KeyError, TypeError, ValueError):
        _fail("persistence_uncertain")
    return value


def _validate_original_result(value: object, *, request_id: str,
                              request_digest: str, transaction_digest: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {"result", "holder", "proof_digest", "proof_pin"}:
        _fail("persistence_uncertain")
    result = value["result"]
    if type(result) is not dict or type(result.get("schema_version")) is not int:
        _fail("persistence_uncertain")
    try:
        if result["schema_version"] == 1:
            from .models import ActivationResult
            parsed = ActivationResult.from_mapping(result)
            result_id = parsed.request_id
        elif result["schema_version"] == 2:
            from .v2_repository import validate_result_v2
            result_id = validate_result_v2(result)["request_id"]
        else:
            _fail("persistence_uncertain")
    except (ActivationContractError, KeyError, TypeError, ValueError):
        _fail("persistence_uncertain")
    if (result_id != request_id or result.get("request_digest") != request_digest
            or result.get("transaction_digest") != transaction_digest
            or value.get("holder") != value.get("proof_pin", {}).get("holder")
            or value.get("proof_digest") != value.get("proof_pin", {}).get("proof_digest")):
        _fail("persistence_uncertain")
    _text(value.get("holder"), identity=True)
    _digest(value.get("proof_digest"))
    _proof_pin(value["proof_pin"])
    return value


def _terminal_receipt(value: object) -> dict[str, Any]:
    raw = _closed(value, _TERMINAL_FIELDS)
    if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or raw["code"] != "abandoned_with_effects"
            or raw["result_class"] != "uncertain" or raw["ok"] is not False
            or raw["generation_digest"] is not None):
        _fail("persistence_uncertain")
    _text(raw["settlement_request_id"], identity=True)
    _digest(raw["plan_digest"]); _digest(raw["approval_digest"])
    _text(raw["request_id"], identity=True)
    _digest(raw["request_digest"]); _digest(raw["transaction_digest"])
    _integer(raw["starting_generation"]); _integer(raw["resulting_generation"])
    if raw["starting_generation"] != raw["resulting_generation"]:
        _fail("persistence_uncertain")
    if raw["current_generation_digest"] is not None:
        _digest(raw["current_generation_digest"])
    _digest(raw["terminal_digest"])
    _integer(raw["sequence"], minimum=1)
    _integer(raw["authorized_at"])
    if raw["previous_terminal_digest"] is not None:
        _digest(raw["previous_terminal_digest"])
    body = {key: item for key, item in raw.items() if key != "terminal_digest"}
    if raw["terminal_digest"] != activation_digest(
            "sandbox.hosting.images.settlement-terminal.v1", body):
        _fail("persistence_uncertain")
    return raw


def _validate_record(value: object, *, key: str, state: dict[str, Any]) -> dict[str, Any]:
    raw = _closed(value, _SETTLEMENT_FIELDS)
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 \
            or raw["settlement_request_id"] != key:
        _fail("persistence_uncertain")
    _text(key, identity=True); _digest(raw["plan_digest"]); _digest(raw["approval_digest"])
    _text(raw["active_request_id"], identity=True)
    _digest(raw["active_request_digest"]); _digest(raw["transaction_digest"])
    _integer(raw["generation"])
    _integer(raw["sequence"], minimum=1)
    _integer(raw["authorized_at"])
    if raw["previous_terminal_digest"] is not None:
        _digest(raw["previous_terminal_digest"])
    current = state.get("current")
    current_digest = None if current is None else current.get("generation_digest")
    if type(state.get("generation")) is not int or state["generation"] < raw["generation"]:
        _fail("persistence_uncertain")
    if state["generation"] == raw["generation"] and raw["current_generation_digest"] != current_digest:
        _fail("persistence_uncertain")
    if current_digest is not None:
        _digest(current_digest)
    plan = SettlementPlan.from_mapping(raw["plan"])
    approval = SettlementApproval.from_mapping(raw["approval"])
    if (approval.approval_digest != raw["approval_digest"]
            or approval.plan_digest != plan.plan_digest
            or not approval.issued_at <= raw["authorized_at"] < approval.expires_at):
        _fail("persistence_uncertain")
    if (plan.plan_digest != raw["plan_digest"]
            or plan.request_id != key
            or plan.active_request_id != raw["active_request_id"]
            or plan.active_request_digest != raw["active_request_digest"]
            or plan.transaction_digest != raw["transaction_digest"]
            or plan.target != plan.observation.target
            or plan.generation != raw["generation"]):
        _fail("persistence_uncertain")
    if plan.current_generation_digest != raw["current_generation_digest"]:
        _fail("persistence_uncertain")
    original = _validate_original_transaction(raw["original_transaction"])
    original_result = _validate_original_result(
        raw["original_result"], request_id=raw["active_request_id"],
        request_digest=raw["active_request_digest"], transaction_digest=raw["transaction_digest"])
    pin = _proof_pin(raw["proof_pin"])
    results = state.get("results")
    if type(results) is not dict or results.get(raw["active_request_id"]) != original_result:
        _fail("persistence_uncertain")
    if ((original.get("recovery_context") or {}).get("target") != plan.target.as_mapping()
            or original.get("request_id") != raw["active_request_id"]
            or original.get("request_digest") != raw["active_request_digest"]
            or original.get("transaction_digest") != raw["transaction_digest"]
            or original.get("starting_generation") != raw["generation"]
            or original.get("phase") != "uncertain"
            or original.get("effect_entered") is not True
            or original.get("proof_pin") != pin
            or original_result.get("result") != original.get("result")
            or original_result.get("proof_pin") != pin):
        _fail("persistence_uncertain")
    receipt = _terminal_receipt(raw["terminal_receipt"])
    if (receipt["settlement_request_id"] != key
            or receipt["plan_digest"] != plan.plan_digest
            or receipt["approval_digest"] != raw["approval_digest"]
            or receipt["request_id"] != raw["active_request_id"]
            or receipt["request_digest"] != raw["active_request_digest"]
            or receipt["transaction_digest"] != raw["transaction_digest"]
            or receipt["starting_generation"] != raw["generation"]
            or receipt["sequence"] != raw["sequence"]
            or receipt["previous_terminal_digest"] != raw["previous_terminal_digest"]
            or receipt["authorized_at"] != raw["authorized_at"]
            or receipt["current_generation_digest"] != raw["current_generation_digest"]):
        _fail("persistence_uncertain")
    return raw


def validate_settlements(state: object, *, validate_base: bool = True) -> dict[str, Any]:
    """Validate the optional settlement map without owning outer state."""
    if type(state) is not dict:
        _fail("persistence_uncertain")
    safe = _copy(state)
    try:
        # The outer decoder calls this hook with validate_base=False. Pure
        # candidates validate the ordinary envelope first without recursion.
        if validate_base:
            from .repository import decode_activation_state
            return decode_activation_state(safe)
    except SettlementRepositoryError:
        raise
    except Exception:
        _fail("persistence_uncertain")
    if "settlements" not in safe:
        return safe
    settlements = safe["settlements"]
    if settlements is None:
        _fail("persistence_uncertain")
    if type(settlements) is not dict or len(settlements) > MAX_SETTLEMENTS:
        _fail("persistence_uncertain")
    for key, value in settlements.items():
        if type(key) is not str or _ID.fullmatch(key) is None:
            _fail("persistence_uncertain")
        if any(key in safe[name] for name in ("results", "tombstones", "recovery_results")):
            _fail("persistence_uncertain")
        _validate_record(value, key=key, state=safe)
    subjects = [row["transaction_digest"] for row in settlements.values()]
    if len(set(subjects)) != len(subjects):
        _fail("persistence_uncertain")
    previous = None
    prior_generation = 0
    target = None
    ordered = sorted(settlements.values(), key=lambda r: r["sequence"])
    for sequence, row in enumerate(ordered, 1):
        row_target = row["plan"]["target"]
        target = row_target if target is None else target
        if (row["sequence"] != sequence or row["previous_terminal_digest"] != previous
                or row["generation"] < prior_generation
                or row_target != target
                or (safe.get("active") or {}).get("transaction_digest") == row["transaction_digest"]):
            _fail("persistence_uncertain")
        previous = row["terminal_receipt"]["terminal_digest"]
        prior_generation = row["generation"]
        predecessors = [item for item in ordered if item["sequence"] < sequence
                        and item["generation"] == row["generation"]]
        _validate_successor_link(row["original_transaction"]["recovery_context"].get("settlement_forward"),
            predecessors[-1] if predecessors else None, starting_generation=row["generation"])
    active = safe.get("active")
    if target is not None:
        for generation in (safe.get("current"), safe.get("previous")):
            if generation is not None and generation.get("target") != target:
                _fail("persistence_uncertain")
        if active is not None and active["recovery_context"]["target"] != target:
            _fail("persistence_uncertain")
    if active is not None:
        predecessors = [row for row in ordered if row["generation"] == active["starting_generation"]]
        _validate_successor_link(active["recovery_context"].get("settlement_forward"),
            predecessors[-1] if predecessors else None, starting_generation=active["starting_generation"])
    for generation in (safe.get("current"), safe.get("previous")):
        if generation is None:
            continue
        starting = generation["generation"] - 1
        predecessors = [row for row in ordered if row["generation"] == starting]
        value = (generation.get("execution_evidence") or {}).get("settlement_forward")
        _validate_successor_link(value, predecessors[-1] if predecessors else None,
                                 starting_generation=starting)
        if value is not None:
            result = safe["results"].get(value["request_id"])
            terminal = result["result"] if result else safe["tombstones"].get(value["request_id"])
            if terminal is None or terminal["request_digest"] != generation["request_digest"]:
                _fail("persistence_uncertain")
    return safe


def _validate_successor_link(value, predecessor, *, starting_generation):
    if predecessor is None:
        if value is not None:
            _fail("persistence_uncertain")
        return
    from .settlement_forward import ForwardSettlementApproval
    try:
        approval = ForwardSettlementApproval.from_mapping(value)
        if (approval.predecessor_digest != predecessor["terminal_receipt"]["terminal_digest"]
                or approval.transaction_digest != predecessor["transaction_digest"]
                or approval.target.as_mapping() != predecessor["plan"]["target"]
                or approval.expected_generation != starting_generation):
            _fail("persistence_uncertain")
    except (TypeError, ValueError):
        _fail("persistence_uncertain")


def latest_settlement(state: object) -> dict[str, Any] | None:
    safe = validate_settlements(state)
    records = safe.get("settlements", {})
    return max(records.values(), key=lambda row: row["sequence"]) if records else None


def _validate_active_for_plan(state: dict[str, Any], plan: SettlementPlan) -> tuple[dict, dict, dict]:
    active = state.get("active")
    if type(active) is not dict:
        _fail("settlement_conflict")
    generation = state.get("generation")
    if type(generation) is not int or generation < 0:
        _fail("persistence_uncertain")
    if state.get("recovery_provisional") is not None:
        _fail("settlement_conflict")
    if (active.get("request_id") != plan.active_request_id
            or active.get("request_digest") != plan.active_request_digest
            or active.get("transaction_digest") != plan.transaction_digest
            or active.get("starting_generation") != generation
            or active.get("phase") != "uncertain"
            or active.get("effect_entered") is not True):
        _fail("settlement_conflict")
    current = state.get("current")
    current_digest = None if current is None else current.get("generation_digest")
    if plan.generation != generation or plan.current_generation_digest != current_digest:
        _fail("settlement_conflict")
    target = (active.get("recovery_context") or {}).get("target")
    if target != plan.target.as_mapping():
        _fail("settlement_conflict")
    original_result = (state.get("results") or {}).get(plan.active_request_id)
    if original_result is None:
        _fail("settlement_conflict")
    _validate_original_transaction(active)
    _validate_original_result(original_result, request_id=plan.active_request_id,
                              request_digest=plan.active_request_digest,
                              transaction_digest=plan.transaction_digest)
    pin = _proof_pin(active.get("proof_pin"))
    if original_result.get("proof_pin") != pin or active.get("result") != original_result.get("result"):
        _fail("settlement_conflict")
    return active, original_result, pin


def _make_terminal(plan: SettlementPlan, approval_digest: str, active: dict,
                   generation: int, current_generation_digest: str | None, *,
                   authorized_at: int, sequence: int,
                   previous_terminal_digest: str | None) -> dict[str, Any]:
    body = {
        "schema_version": 1, "code": "abandoned_with_effects",
        "result_class": "uncertain", "ok": False,
        "settlement_request_id": plan.request_id, "plan_digest": plan.plan_digest,
        "approval_digest": approval_digest, "request_id": active["request_id"],
        "request_digest": active["request_digest"],
        "transaction_digest": active["transaction_digest"],
        "starting_generation": generation, "resulting_generation": generation,
        "generation_digest": None, "current_generation_digest": current_generation_digest,
        "authorized_at": authorized_at, "sequence": sequence,
        "previous_terminal_digest": previous_terminal_digest,
    }
    return {**body, "terminal_digest": activation_digest(
        "sandbox.hosting.images.settlement-terminal.v1", body)}


def settle_candidate(state: object, plan: object, approval: object, *, authorized_at: int):
    """Return ``(status, candidate, settlement_record)`` for one settlement.

    The service verifies the installed approval before calling this pure
    transition. Its complete signed value is retained for later audit.
    """
    if type(plan) is not SettlementPlan or type(approval) is not SettlementApproval:
        _fail("artifact_invalid")
    _integer(authorized_at)
    approval_digest = approval.approval_digest
    if approval.plan_digest != plan.plan_digest:
        _fail("authority_mismatch")
    safe = validate_settlements(state)
    settlements = safe.get("settlements")
    if settlements is None:
        settlements = {}
    existing = settlements.get(plan.request_id)
    if existing is not None:
        _validate_record(existing, key=plan.request_id, state=safe)
        if (existing["plan_digest"] != plan.plan_digest
                or existing["approval_digest"] != approval_digest):
            return "conflict", safe, None
        return "replay", safe, _copy(existing)
    if not approval.issued_at <= authorized_at < approval.expires_at:
        _fail("approval_expired")
    if any(plan.request_id in safe[name] for name in ("results", "tombstones", "recovery_results")):
        return "conflict", safe, None
    if len(settlements) >= MAX_SETTLEMENTS:
        _fail("retention_full")
    try:
        active, original_result, pin = _validate_active_for_plan(safe, plan)
    except SettlementRepositoryError as exc:
        if exc.code == "settlement_conflict":
            return "conflict", safe, None
        raise
    generation = safe["generation"]
    current = safe.get("current")
    current_digest = None if current is None else current.get("generation_digest")
    head = max(settlements.values(), key=lambda row: row["sequence"]) if settlements else None
    sequence = len(settlements) + 1
    previous = head["terminal_receipt"]["terminal_digest"] if head else None
    terminal = _make_terminal(plan, approval_digest, active, generation, current_digest,
        authorized_at=authorized_at, sequence=sequence, previous_terminal_digest=previous)
    record = {
        "schema_version": 1, "settlement_request_id": plan.request_id,
        "plan_digest": plan.plan_digest, "approval_digest": approval_digest,
        "active_request_id": active["request_id"],
        "active_request_digest": active["request_digest"],
        "transaction_digest": active["transaction_digest"],
        "generation": generation, "current_generation_digest": current_digest,
        "plan": _copy(plan.as_mapping()),
        "original_transaction": _copy(active),
        "original_result": _copy(original_result),
        "proof_pin": _copy(pin), "terminal_receipt": terminal,
        "approval": approval.as_mapping(), "authorized_at": authorized_at,
        "sequence": sequence, "previous_terminal_digest": previous,
    }
    candidate = _copy(safe)
    candidate["settlements"] = dict(settlements)
    candidate["settlements"][plan.request_id] = record
    candidate["active"] = None
    candidate["reserved_terminal_bytes"] = 0
    validate_settlements(candidate)
    return "settled", candidate, _copy(record)


__all__ = (
    "MAX_SETTLEMENTS", "SettlementRepositoryError", "settle_candidate",
    "validate_settlements",
)
