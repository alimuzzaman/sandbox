"""The timer's entry point: one bounded safe-tier reclamation pass.

Sequence: load the recorded routine, finalize any record the init-system
backstop left open, pre-check the host guard and apply locks, open a run
record, plan the safe tier with the workspace-reap selection, refuse on an
incomplete inventory, execute the reviewed plan with the
``scheduled_routine`` trigger, and close the record.

The run enforces its own bound: planning and removal budgets come from what
is left of the recorded timeout, and the probe stops removing at its deadline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import secrets
import time
from pathlib import Path
from typing import Any

from ..host_guard import apply_transaction_active, host_runtime, try_reclaim_guard
from ..models import utc_now
from .contract import span_seconds
from .store import RoutineStore, manifest_reference, utc_iso

TRIGGER = "scheduled_routine"
# Leave the probe room to report and the record room to be written before the
# init-system backstop (bound + 60s) fires.
_PLAN_BUDGET_CAP = 120.0
_MIN_REMOVAL_BUDGET = 5.0


def _host_revision() -> str:
    from sandbox.services.runtime_revision import runtime_revision

    return runtime_revision(Path(__file__).resolve().parents[3])


def _host_config() -> Mapping[str, Any]:
    from sandbox.core._config import load_config

    return load_config()


def _default_service():
    from ..context import reclaim_service

    return reclaim_service(None)


def effective_exclusions(routine: Mapping[str, Any], host_config: Mapping[str, Any] | None) -> list[str]:
    """Routine exclusions plus the host's own ``resources.reclaim_exclude``.

    The operator's local configuration never reaches the host, so it cannot
    affect a run (FR-012).
    """
    configured = ((host_config or {}).get("resources") or {}).get("reclaim_exclude") or ()
    if isinstance(configured, str):
        configured = (configured,)
    patterns = [*(routine.get("exclusions") or ()), *configured]
    return list(dict.fromkeys(item for item in patterns if isinstance(item, str) and item))


def _skipped_counts(items) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items or ():
        if isinstance(item, Mapping):
            reason = str(item.get("reason") or "skipped")[:64]
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _merge_counts(*maps: Mapping[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for counts in maps:
        for key, value in counts.items():
            merged[key] = merged.get(key, 0) + value
    return merged


def run_routine(
    *,
    runtime: Path | None = None,
    store: RoutineStore | None = None,
    service_factory: Callable[[], Any] | None = None,
    host_config: Mapping[str, Any] | None = None,
    revision: str | None = None,
    clock: Callable = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run once and return ``{"ok", "run"}`` (or a typed refusal)."""
    runtime = Path(runtime or host_runtime())
    store = store or RoutineStore(runtime / "resources" / "cleanup-routine")
    routine = store.read_config()
    if not routine or routine.get("enabled") is not True:
        return {"ok": False, "run": None, "error": {
            "code": "routine_disabled", "message": "the cleanup routine is not enabled"}}
    bound = float(span_seconds(str(routine.get("timeout") or "30min")))
    store.finalize_stale(now=clock(), default_timeout_seconds=bound)

    started = monotonic()
    record: dict[str, Any] = {
        "schema": 1,
        "run_id": secrets.token_hex(16),
        "started_at": utc_iso(clock()),
        "ended_at": None,
        "outcome": "running",
        "reason": None,
        "bytes_reclaimed": 0,
        "removed": 0,
        "skipped": 0,
        "skipped_reasons": {},
        "runtime_revision": revision or _host_revision(),
        "manifest": None,
        "timeout_seconds": bound,
    }

    def close(outcome: str, reason: str | None = None) -> dict[str, Any]:
        record.update({"outcome": outcome, "reason": reason,
                       "ended_at": utc_iso(clock())})
        store.write_run(record)
        return {"ok": outcome in {"reclaimed", "nothing_to_do"}, "run": record}

    # A busy host costs no inventory: check before planning.  The probe takes
    # the guard again for the removal itself, which closes the race.
    if apply_transaction_active():
        return close("skipped_busy", "apply_transaction_active")
    with try_reclaim_guard(runtime=runtime) as acquired:
        pass
    if not acquired:
        return close("skipped_busy", "host_reclaim_busy")

    store.write_run(record)
    exclusions = effective_exclusions(
        routine, _host_config() if host_config is None else host_config,
    )
    service = (service_factory or _default_service)()

    remaining = bound - (monotonic() - started)
    try:
        planned = service.plan(
            "safe", budget_seconds=max(min(remaining, _PLAN_BUDGET_CAP), 1.0),
            exclude_kinds=("runtime",), exclude_names=tuple(exclusions),
        )
    except Exception:
        return close("refused", "plan_failed")
    if not planned.get("ok"):
        code = str(((planned.get("error") or {}).get("code")) or "plan_failed")[:64]
        return close("refused", code)
    data = planned.get("data") or {}
    plan_skipped = _skipped_counts(data.get("skipped"))
    record["skipped_reasons"] = plan_skipped
    record["skipped"] = sum(plan_skipped.values())
    if data.get("inventory_status") != "complete" or data.get("truncated"):
        return close("refused", "inventory_incomplete")
    if not data.get("candidates"):
        return close("nothing_to_do")

    remaining = bound - (monotonic() - started)
    if remaining < _MIN_REMOVAL_BUDGET:
        return close("timed_out", "run_bound_exceeded")
    try:
        payload = service.cleanup(
            plan_id=data["plan_id"], confirm=True, trigger=TRIGGER,
            budget_seconds=remaining, run_id=record["run_id"],
        )
    except Exception:
        record["manifest"] = manifest_reference(record["run_id"])
        return close("refused", "cleanup_failed")
    error = payload.get("error") or {}
    if payload.get("status") == "skipped" and error.get("code") == "host_reclaim_busy":
        detail = (payload.get("data") or {}).get("detail")
        return close("skipped_busy", "apply_transaction_active"
                     if detail == "apply_transaction" else "host_reclaim_busy")

    record["manifest"] = manifest_reference(record["run_id"])
    result = payload.get("data") or {}
    outcomes = [item for item in result.get("outcomes") or () if isinstance(item, Mapping)]
    removed = [item for item in outcomes if item.get("status") == "removed"]
    record["removed"] = len(removed)
    record["bytes_reclaimed"] = int(result.get("observed_reclaimed_bytes") or 0)
    run_skipped = _skipped_counts(item for item in outcomes if item.get("status") == "skipped")
    record["skipped_reasons"] = _merge_counts(plan_skipped, run_skipped)
    record["skipped"] = sum(record["skipped_reasons"].values())
    if result.get("budget_exhausted"):
        return close("timed_out", "run_bound_exceeded")
    if payload.get("ok") is not True:
        return close("refused", str(error.get("code") or "cleanup_failed")[:64])
    return close("reclaimed", None if payload.get("status") == "completed" else "partial")


__all__ = ["TRIGGER", "effective_exclusions", "run_routine"]
