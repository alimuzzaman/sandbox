"""Host-side handler for the ``cleanup_routine_*`` control actions.

Called from the control service's ``/resources`` route ahead of the probe
allowlist.  Every outcome, including a refusal, is returned as a contract
envelope; nothing here raises to the transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..host_guard import host_runtime
from ..models import utc_now
from . import units
from .contract import (
    RoutineError,
    envelope,
    error_result,
    routine_result,
    validate_request,
)
from .run import _host_config, _host_revision, effective_exclusions
from .store import RoutineStore, utc_iso


def _sb_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "sb")


def _status_result(store: RoutineStore, config: Mapping[str, Any] | None,
                   host_config: Mapping[str, Any], *, history: int,
                   next_run: str | None) -> dict[str, Any]:
    return routine_result(
        config,
        effective_exclusions=effective_exclusions(config or {}, host_config),
        runs=store.runs(limit=history),
        next_run=next_run,
    )


def _enable(request: Mapping[str, Any], *, revision: str, store: RoutineStore,
            sb_path: str, sandbox_home: str, host_config: Mapping[str, Any]) -> dict:
    if request["expected_runtime_revision"] != revision:
        raise RoutineError(
            "the remote runtime differs from the operator's; run "
            "`sb remote service migrate <remote> --confirm` first",
            "runtime_revision_mismatch",
        )
    prior = store.read_config()
    config = {
        "schema": 1,
        "enabled": True,
        "cadence": request["cadence"],
        "timeout": request["timeout"],
        "randomized_delay": request["randomized_delay"],
        "exclusions": list(request["exclusions"]),
        "enabled_revision": revision,
        "enabled_at": utc_iso(utc_now()),
        "disabled_at": None,
        "unit": units.UNIT,
    }
    # Host checks and the cadence run before any write.  The config is written
    # before the units so a timer never fires without it, and the prior config
    # comes back if the install fails.
    units.check_host()
    next_run = units.validate_cadence(config["cadence"])
    try:
        store.write_config(config)
    except OSError as exc:
        raise RoutineError(f"routine config could not be written: {exc}",
                           "routine_install_failed") from None
    try:
        units.install(config, sb_path=sb_path, sandbox_home=sandbox_home,
                      preflight=False)
    except RoutineError:
        try:
            if prior is None:
                store.config_path.unlink()
            else:
                store.write_config(prior)
        except OSError:
            pass
        raise
    return _status_result(store, config, host_config, history=30,
                          next_run=next_run)


def handle(
    payload: Any,
    *,
    live_revision: str | None = None,
    store: RoutineStore | None = None,
    sb_path: str | None = None,
    sandbox_home: str | None = None,
    host_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and execute one routine action; return the contract envelope."""
    revision = live_revision or _host_revision()
    store = store or RoutineStore()
    try:
        request = validate_request(payload)
        config_source = _host_config() if host_config is None else host_config
        if request["action"] == "cleanup_routine_enable":
            result = _enable(
                request, revision=revision, store=store,
                sb_path=sb_path or _sb_path(),
                sandbox_home=sandbox_home or str(host_runtime().parent),
                host_config=config_source,
            )
        else:
            raise RoutineError("cleanup routine action is not available yet")
    except RoutineError as exc:
        result = error_result(exc.code, str(exc))
    return envelope(result, runtime_revision=revision)


__all__ = ["handle"]
