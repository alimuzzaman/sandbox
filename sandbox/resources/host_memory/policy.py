"""Pure fail-closed host-memory policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

from .models import HEX24, SwapPolicy, canonical_digest, parse_utc, utc_text

GIB = 1024 ** 3
ACTIVE_ARTIFACTS = ("swap_file", "swap_unit", "swappiness_policy", "monitor_helper",
                    "monitor_service", "monitor_timer", "rotation_policy", "receipt")
DISABLE_ARTIFACTS = ("monitor_timer", "monitor_service", "monitor_helper",
                      "swappiness_policy", "swap_unit", "swap_file")


class PolicyRefusal(ValueError):
    def __init__(self, code, message):
        super().__init__(message); self.code = code


def _integer(value, code="invalid_size"):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyRefusal(code, "value must be a non-negative integer")
    return value


def enable_calculations(state: Mapping, size_gib: int):
    size_gib = _integer(size_gib)
    if not 1 <= size_gib <= 8:
        raise PolicyRefusal("invalid_size", "size must be from 1 through 8 GiB")
    try:
        ram = _integer(state["memory"]["total_bytes"], "insufficient_capacity")
        total = _integer(state["filesystem"]["total_bytes"], "insufficient_capacity")
        free = _integer(state["filesystem"]["free_bytes"], "insufficient_capacity")
    except (KeyError, TypeError):
        raise PolicyRefusal("insufficient_capacity", "capacity evidence is incomplete") from None
    requested = size_gib * GIB
    reserve = max(10 * GIB, (total * 15 + 99) // 100)
    rows = (
        ("size_range", requested, 8 * GIB, "<=", 1 <= size_gib <= 8),
        ("ram_half", requested, ram // 2, "<=", requested <= ram // 2),
        ("filesystem_tenth", requested, total // 10, "<=", requested <= total // 10),
        ("free_reserve", free - requested, reserve, ">=", free - requested >= reserve),
    )
    calculations = [{"name": n, "observed_bytes": o, "threshold_bytes": t,
                     "comparator": c, "passed": p} for n, o, t, c, p in rows]
    if not all(row[4] for row in rows):
        raise PolicyRefusal("insufficient_capacity", "requested swap exceeds a capacity bound")
    return calculations


def validate_common(state: Mapping):
    evidence = state.get("evidence_state")
    if evidence == "unsupported": raise PolicyRefusal("unsupported_platform", "host is unsupported")
    areas = state.get("swap_areas") or []
    if len(areas) > 1: raise PolicyRefusal("unmanaged_swap", "multiple swap areas are unsupported")
    if any(area.get("ownership") != "owned" for area in areas):
        raise PolicyRefusal("unmanaged_swap", "unmanaged swap blocks lifecycle mutation")
    if state.get("ownership") not in {"absent", "owned"}:
        raise PolicyRefusal("ownership_unknown", "host ownership is ambiguous or unproven")
    if state.get("operation_block"):
        code = "rollback_incomplete" if state["operation_block"].get("reason") == "rollback_incomplete" else "operation_in_progress"
        raise PolicyRefusal(code, "another lifecycle operation blocks mutation")
    if evidence != "known": raise PolicyRefusal("ownership_unknown", "required evidence is not complete")


def validate_target(target: Mapping):
    if not isinstance(target, Mapping): raise PolicyRefusal("unregistered_target", "planning target is not registered")
    name, identity = target.get("remote_name"), target.get("target_identity")
    if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
        raise PolicyRefusal("unregistered_target", "planning target is not registered")
    if "/" in identity or ".." in identity or "/" in name or ".." in name:
        raise PolicyRefusal("unregistered_target", "planning target carries an unsafe locator")
    marker = target.get("service_ownership_marker", target.get("ownership_marker"))
    if not HEX24.fullmatch(str(marker or "")):
        raise PolicyRefusal("unregistered_target", "planning target lacks service evidence")
    if not HEX24.fullmatch(str(target.get("runtime_revision", ""))):
        raise PolicyRefusal("unregistered_target", "planning target lacks service evidence")


def validate_freshness(state: Mapping, now):
    try:
        fresh = freshness(state["observed_at"], now)
    except (KeyError, TypeError, ValueError):
        raise PolicyRefusal("evidence_stale", "planning requires a timestamped observation") from None
    if fresh != "fresh":
        raise PolicyRefusal("evidence_stale", "planning requires a fresh observation")


def disable_calculations(state: Mapping):
    validate_common(state)
    if state.get("ownership") != "owned": raise PolicyRefusal("ownership_unknown", "owned state is not proven")
    memory = state.get("memory") or {}; areas = state.get("swap_areas") or []
    available = _integer(memory.get("available_bytes"), "insufficient_disable_headroom")
    total = _integer(memory.get("total_bytes"), "insufficient_disable_headroom")
    used = sum(_integer(a.get("used_bytes", 0), "insufficient_disable_headroom") for a in areas)
    required = used + max(GIB, total // 10)
    if available <= required:
        raise PolicyRefusal("insufficient_disable_headroom", "available RAM must strictly exceed disable reserve")
    return [{"name": "disable_headroom", "observed_bytes": available,
             "threshold_bytes": required, "comparator": ">", "passed": True}]


def build_plan(operation, target, state, *, size_gib=4, now=None):
    if operation not in {"enable", "disable"}: raise PolicyRefusal("invalid_mode", "operation must be enable or disable")
    if operation == "enable":
        size_gib = _integer(size_gib)
        if not 1 <= size_gib <= 8:
            raise PolicyRefusal("invalid_size", "size must be from 1 through 8 GiB")
    validate_target(target)
    validate_common(state)
    now = now or datetime.now(timezone.utc)
    validate_freshness(state, now)
    policy = SwapPolicy(size_gib=size_gib) if operation == "enable" else None
    calculations = enable_calculations(state, size_gib) if policy else disable_calculations(state)
    converged = operation == "enable" and _already_enabled(state, size_gib)
    payload = {
        "schema_version": 1, "operation": operation, "target": dict(target),
        "created_at": utc_text(now), "expires_at": utc_text(now + timedelta(minutes=15)),
        "observation": dict(state), "observation_digest": canonical_digest(state),
        "requested_policy": {"size_gib": size_gib} if policy else None,
        "effective_policy": policy.to_dict() if policy else None,
        "calculations": calculations, "intended_changes": list(ACTIVE_ARTIFACTS) if operation == "enable" else list(DISABLE_ARTIFACTS),
        "rollback_scope": list(ACTIVE_ARTIFACTS), "requires_confirmation": True,
        "state": "already_current" if converged else "planned",
    }
    payload["plan_id"] = canonical_digest(payload)
    return payload


def _already_enabled(state: Mapping, size_gib: int):
    areas = state.get("swap_areas") or []
    return (len(areas) == 1 and areas[0].get("ownership") == "owned"
            and areas[0].get("total_bytes") == size_gib * GIB)


def plan_current(plan, state, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now > parse_utc(plan["expires_at"]): raise PolicyRefusal("plan_expired", "plan has expired")
    if canonical_digest(state) != plan["observation_digest"]: raise PolicyRefusal("plan_drifted", "host state changed after planning")


def freshness(sampled_at, now, seconds=660):
    age = (now - parse_utc(sampled_at)).total_seconds()
    if age < 0: return "unknown"
    return "fresh" if age <= seconds else "stale"


def sustained_swap_use(samples, threshold=512 * 1024 * 1024):
    recent = list(samples)[-3:]
    return len(recent) == 3 and all(
        sample.get("status") == "valid"
        and (sample.get("swap") or {}).get("used_bytes", -1) >= threshold
        for sample in recent
    )
