"""Truthfully lower-isolation incumbent native runtime adapters."""

from __future__ import annotations

import re

from sandbox.isolation.models import canonical_digest
from sandbox.runtimes.base import OperationResult


LOWER_ISOLATION = "trusted_shared_host"
_VERSION = re.compile(r"\b(\d+(?:\.\d+){1,3})\b")


def version_from(result):
    if getattr(result, "returncode", 1) != 0:
        return None
    match = _VERSION.search((getattr(result, "stdout", "") or "") + " " +
                            (getattr(result, "stderr", "") or ""))
    return match.group(1) if match else None


def php_version_matches(observed, required):
    """Compare explicit PHP major/minor requirements without guessing patches."""
    if observed is None:
        return False
    observed_match = _VERSION.search(str(observed))
    if observed_match is None:
        return False
    if required is None:
        return True
    if not isinstance(required, str) or re.fullmatch(r"\d+\.\d+", required) is None:
        raise ValueError("invalid_php_requirement")
    return ".".join(observed_match.group(1).split(".")[:2]) == required


def safe_database(value):
    if value is None:
        return {"configured": False, "required": True}
    if not isinstance(value, dict) or set(value) - {"host", "port", "name", "user"}:
        raise ValueError("incumbent database must be a user-supplied reference")
    if not {"host", "name", "user"}.issubset(value):
        raise ValueError("incumbent database must name host, database, and user")
    if any(not isinstance(value[key], str) or not value[key] for key in ("host", "name", "user")):
        raise ValueError("incumbent database reference is invalid")
    if "port" in value and (isinstance(value["port"], bool) or not isinstance(value["port"], int)
                            or not 1 <= value["port"] <= 65535):
        raise ValueError("incumbent database port is invalid")
    return {"configured": True, **dict(value)}


def result(request, ok, state, **data):
    return OperationResult(ok, request.operation, request.project_root, "wordpress", {
        "runtime": {"mode": "incumbent_native", "isolation": LOWER_ISOLATION,
                    "route_mutations": False, "route_owner": "ingress"},
        "state": state, "mutated": False, **data,
    })


def cleanup_owned(request, cleanup):
    """Run an explicit incumbent cleanup callback only after state comparison.

    Incumbent adapters never discover or infer host state.  The composition root
    must supply an owned record with digestable expected/observed values and a
    dedicated remover; otherwise the adapter preserves state for manual retry.
    """
    if cleanup is None:
        return {"ok": True, "state": "ready", "mutated": False,
                "cleanup": {"complete": True, "removed": (), "residual": ()},
                "reason": {"code": "no_incumbent_state_owned"}}
    try:
        item = cleanup(request)
    except (OSError, RuntimeError, TypeError, ValueError):
        item = None
    if not isinstance(item, dict) or not isinstance(item.get("expected"), dict) \
            or not isinstance(item.get("observed"), dict) or not callable(item.get("remove")):
        return {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                "cleanup": {"complete": False, "removed": (), "residual": ("state",)},
                "recovery": {"object_type": "state", "reason_code": "runtime_unavailable",
                             "retry_state": "pending"},
                "reason": {"code": "cleanup_incomplete"}}
    expected = canonical_digest(item["expected"])
    observed = canonical_digest(item["observed"])
    identity = item.get("identity", "state")
    if expected != observed:
        return {"ok": False, "state": "cleanup_incomplete", "mutated": False,
                "cleanup": {"complete": False, "removed": (), "residual": (identity,)},
                "recovery": {"object_type": "state", "reason_code": "owned_state_drifted",
                             "retry_state": "pending"},
                "reason": {"code": "cleanup_incomplete"}}
    try:
        removed = item["remove"]()
    except (OSError, RuntimeError, TypeError, ValueError):
        removed = False
    ok = bool(removed.get("ok")) if isinstance(removed, dict) else bool(removed)
    mutated = bool(removed.get("mutated", ok)) if isinstance(removed, dict) else ok
    if not ok:
        return {"ok": False, "state": "cleanup_incomplete", "mutated": mutated,
                "cleanup": {"complete": False, "removed": (), "residual": (identity,)},
                "recovery": {"object_type": "state", "reason_code": "cleanup_failed",
                             "retry_state": "pending"},
                "reason": {"code": "cleanup_incomplete"}}
    return {"ok": True, "state": "ready", "mutated": mutated,
            "cleanup": {"complete": True, "removed": (identity,), "residual": ()},
            "reason": {"code": "cleanup_complete"}}
