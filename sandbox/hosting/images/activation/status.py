"""Bounded public activation diagnostics; no runtime or authority access."""

from .repository import decode_activation_state


def activation_status(value: object, request_id: str | None = None) -> dict:
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
    if request_id is not None:
        from .models import _text
        _text(request_id, identity=True)
        retained = state["results"].get(request_id)
        if retained is not None:
            result["request"] = {"request_id": request_id, "state": "terminal", "result": retained["result"]}
        elif active is not None and active["request_id"] == request_id:
            result["request"] = {"request_id": request_id, "state": "active", "result": None}
        else:
            result["request"] = {"request_id": request_id,
                "state": "retained_without_result" if request_id in state["tombstones"] else "unknown", "result": None}
    return result
