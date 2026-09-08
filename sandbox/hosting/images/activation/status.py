"""Bounded public activation diagnostics; no runtime or authority access."""

from .repository import decode_activation_state


def activation_status(value: object) -> dict:
    state = decode_activation_state(value)
    active = state["active"]
    from .settlement_forward import required_predecessor
    predecessor = required_predecessor(state)
    result = {
        "schema_version": 1, "ok": True, "code": "observed",
        "state_schema_version": state["schema_version"],
        "generation": state["generation"],
        "active": None if active is None else {key: active[key] for key in (
            "schema_version", "request_id", "request_digest", "transaction_digest",
            "operation", "phase", "effect_entered")},
        "current_generation_digest": (state["current"] or {}).get("generation_digest"),
        "previous_generation_digest": (state["previous"] or {}).get("generation_digest"),
        "retained_result_count": len(state["results"]),
        "retained_recovery_count": len(state["recovery_results"]),
    }
    if "settlements" in state:
        result.update(retained_settlement_count=len(state["settlements"]),
            required_settlement_predecessor=None if predecessor is None else
                predecessor["terminal_receipt"]["terminal_digest"])
    return result
