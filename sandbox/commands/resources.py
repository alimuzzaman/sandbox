"""Host-wide resource monitoring and confirmation-gated cleanup."""

from __future__ import annotations

import json
import math
import time

from sandbox.registry import CommandSpec, register_specs
from sandbox.resources.context import resource_service
from sandbox.resources.models import redact


def _remaining_status_budget(args, requested_budget: float) -> float:
    """Return provider time left in the CLI's end-to-end status budget."""
    deadline = getattr(args, "_invocation_deadline_monotonic", None)
    requested = float(requested_budget)
    if (
        deadline is None
        or not math.isfinite(requested)
        or requested <= 0
        or requested > 3600
    ):
        # Preserve ResourceService's validation and direct-call compatibility.
        return requested_budget
    remaining = float(deadline) - time.monotonic()
    return min(requested, max(remaining, 0.0))


def _restore_requested_budget(payload: dict, requested_budget: float) -> None:
    """Keep the public scan contract about the user's requested budget."""
    data = payload.get("data")
    if isinstance(data, dict) and "budget_seconds" in data:
        data["budget_seconds"] = float(requested_budget)


def _budget_exhausted_payload(requested_budget: float) -> dict:
    from sandbox.resources.service import ResourceError, result

    return result(
        False,
        "status",
        status="timed_out",
        data={"budget_seconds": float(requested_budget)},
        error=ResourceError(
            "resource measurement budget was exhausted during CLI initialization",
            "overall_budget_exhausted",
            retryable=True,
        ),
    )


def configure_parser(parser) -> None:
    parser.description = "Monitor host storage and safely clean managed resources"
    parser.add_argument("action", choices=("status", "plan", "cleanup"))
    parser.add_argument("--remote", default=None, help="configured remote name")
    parser.add_argument("--scope", choices=("cache", "stale"), default=None)
    parser.add_argument("--thorough", action="store_true")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="run bounded filesystem, deleted-open, and engine attribution",
    )
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument(
        "--cancelled",
        action="store_true",
        help="express a pre-cancelled status request (for non-interactive callers)",
    )
    parser.add_argument("--plan-id", default=None)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")


def _human_bytes(value) -> str:
    if not isinstance(value, int):
        return "unknown"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _emit(payload: dict, as_json: bool) -> None:
    payload = redact(payload)
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    target = payload.get("target") or {}
    print(
        f"resources {payload.get('action')}: {payload.get('status')} "
        f"({target.get('name', 'unresolved')})"
    )
    if target:
        print(
            f"  target kind={target.get('kind', 'unknown')}; "
            f"name={target.get('name', 'unresolved')}"
        )
    if payload.get("error"):
        error = payload["error"]
        print(f"  {error.get('code')}: {error.get('message')}")
        return
    data = payload.get("data") or {}
    if payload.get("action") == "status":
        capacity = data.get("capacity") or {}
        summary = data.get("summary") or {}
        print(
            f"  used {_human_bytes(capacity.get('used_bytes'))} / "
            f"{_human_bytes(capacity.get('total_bytes'))}; "
            f"available {_human_bytes(capacity.get('available_bytes'))}"
        )
        print(
            f"  reclaimable {_human_bytes(summary.get('reclaimable_bytes'))}; "
            f"unknown {_human_bytes(summary.get('unknown_bytes'))}"
        )
        pressure = data.get("capacity_pressure") or {}
        if pressure:
            recovery = pressure.get("recovery") or {}
            print(
                "  network capacity pressure: "
                f"{pressure.get('level', 'unknown')} "
                f"({pressure.get('managed_user_defined_network_count', 'unknown')} "
                "managed user-defined networks; "
                f"threshold {pressure.get('threshold', 'unknown')}; "
                f"confidence {pressure.get('confidence', 'unknown')})"
            )
            if recovery.get("code") or recovery.get("guidance"):
                print(
                    f"  network recovery ({recovery.get('code') or 'monitoring'}): "
                    f"{recovery.get('guidance', 'none')}"
                )
        for item in (summary.get("owners") or ())[:5]:
            print(
                f"  owner {_human_bytes(item.get('measured_bytes')):>12} "
                f"{item.get('id')}"
            )
        for item in (summary.get("categories") or ())[:5]:
            print(
                f"  class {_human_bytes(item.get('measured_bytes')):>12} "
                f"{item.get('id')}"
            )
        for item in (data.get("resources") or ())[:20]:
            print(
                f"  {_human_bytes(item.get('size_bytes')):>12} "
                f"{item.get('classification', 'unknown'):>16} "
                f"{item.get('display_name', item.get('resource_id'))}"
            )
        for category in data.get("category_outcomes") or ():
            if category.get("status") not in {"complete", "observed"}:
                print(
                    f"  partial: {category.get('category')} "
                    f"({category.get('status')})"
                )
        _emit_deep(data.get("deep_attribution") or {})
    elif payload.get("action") == "plan":
        print(f"  plan: {data.get('plan_id')}")
        print(f"  expires: {data.get('expires_at')}")
        print(
            f"  candidates: {len(data.get('candidates') or ())}; "
            f"estimated {_human_bytes(data.get('estimated_reclaimable_bytes'))}"
        )
    else:
        print(
            f"  outcomes: {len(data.get('outcomes') or ())}; "
            f"observed reclaimed "
            f"{_human_bytes(data.get('observed_reclaimed_bytes'))}"
        )


def _emit_deep(deep: dict) -> None:
    """Render the complete public deep-attribution contract without identifiers."""
    if not deep:
        return
    reconciliation = deep.get("reconciliation") or {}
    print(f"  deep status: {deep.get('status', 'unknown')}")
    for filesystem in deep.get("filesystems") or ():
        print(
            f"  filesystem {filesystem.get('display_name', 'unknown')} "
            f"({filesystem.get('filesystem_type', 'unknown')}): "
            f"used {_human_bytes(filesystem.get('used_bytes'))} / "
            f"{_human_bytes(filesystem.get('total_bytes'))}; "
            f"available {_human_bytes(filesystem.get('available_bytes'))}; "
            f"observed {_human_bytes(filesystem.get('observed_allocated_bytes'))}"
        )
        print(
            f"    writable={filesystem.get('writable')}; "
            f"selected={filesystem.get('selected')} "
            f"({filesystem.get('selection_reason', 'unknown')}); "
            f"status={filesystem.get('status', 'unknown')}; hardlinks="
            f"{filesystem.get('hardlink_deduplication', 'unknown')}"
        )
        print(
            f"    mount_id={filesystem.get('mount_id') or 'none'}; "
            f"parent_mount_id={filesystem.get('parent_mount_id') or 'none'}; "
            f"capacity_scope_id={filesystem.get('capacity_scope_id') or 'none'}; "
            f"mount_flags={','.join(map(str, filesystem.get('mount_flags') or ())) or 'none'}"
        )
        limitations = filesystem.get("limitations") or ()
        if limitations:
            print(f"    limitations: {', '.join(map(str, limitations))}")
    print(
        f"  deep used {_human_bytes(reconciliation.get('used_bytes'))}; "
        f"accounted {_human_bytes(reconciliation.get('accounted_bytes'))}; "
        f"residual {_human_bytes(reconciliation.get('residual_unexplained_bytes'))}; "
        f"deleted-open {_human_bytes(reconciliation.get('deleted_open_bytes'))}; "
        f"directory allocated {_human_bytes(reconciliation.get('directory_allocated_bytes'))}; "
        f"overhead {_human_bytes(reconciliation.get('observable_overhead_bytes'))}; "
        f"logical overlap {_human_bytes(reconciliation.get('overlapping_logical_bytes'))}; "
        f"overage {_human_bytes(reconciliation.get('overage_bytes'))}; "
        f"drift {_human_bytes(reconciliation.get('drift_bytes'))} "
        f"(material={reconciliation.get('drift_material')}); capacity drift "
        f"{_human_bytes(reconciliation.get('capacity_drift_bytes'))} "
        f"(material={reconciliation.get('capacity_drift_material')}); "
        f"attributed drift "
        f"{_human_bytes(reconciliation.get('attributed_drift_bytes'))} "
        f"(material={reconciliation.get('attributed_drift_material')})"
    )
    for capability in deep.get("capabilities") or ():
        print(
            f"  tool {capability.get('category')}: {capability.get('name')} "
            f"({capability.get('status')}); version="
            f"{capability.get('version') or 'unknown'}; fallback="
            f"{capability.get('fallback')}; privilege="
            f"{capability.get('privilege', 'unknown')}"
        )
        limitations = capability.get("limitations") or ()
        if limitations:
            print(f"    limitations: {', '.join(map(str, limitations))}")
    filesystem_labels = {
        item.get("filesystem_id"): item.get("display_name", "unknown filesystem")
        for item in deep.get("filesystems") or ()
    }
    for coverage in deep.get("coverage") or ():
        reason = coverage.get("reason") or "none"
        boundary = coverage.get("boundary_id")
        boundary_label = filesystem_labels.get(boundary, "host-wide")
        print(
            f"  coverage: {coverage.get('category')} "
            f"({coverage.get('status')}: {reason}); duration="
            f"{coverage.get('duration_ms', 'unknown')}ms; confidence="
            f"{coverage.get('confidence', 'unknown')}; privilege_sufficient="
            f"{coverage.get('privilege_sufficient')}; boundary={boundary_label}"
        )
        if coverage.get("status") not in {"complete", "not_selected"}:
            print(
                f"  deep partial: {coverage.get('category')} "
                f"({coverage.get('status')}: {reason})"
            )
    findings_by_filesystem: dict[str | None, list[dict]] = {}
    for finding in deep.get("findings") or ():
        findings_by_filesystem.setdefault(finding.get("filesystem_id"), []).append(finding)
    ranking_groups = [
        (filesystem_id, findings_by_filesystem.get(filesystem_id, ()))
        for filesystem_id in filesystem_labels
    ]
    ranking_groups.extend(
        (filesystem_id, findings)
        for filesystem_id, findings in findings_by_filesystem.items()
        if filesystem_id not in filesystem_labels
    )
    for filesystem_id, findings in ranking_groups:
        print(f"  rankings for {filesystem_labels.get(filesystem_id, 'unassigned diagnostics')}")
        for finding in sorted(
            findings,
            key=lambda item: (
                int(item.get("observed_bytes") or 0),
                str(item.get("finding_id") or ""),
            ),
            reverse=True,
        )[:100]:
            print(
                f"    {_human_bytes(finding.get('observed_bytes')):>12} "
                f"{finding.get('kind', 'unknown'):>16} "
                f"{finding.get('display_name', 'unknown')} "
                f"[{finding.get('guidance', 'monitoring_only')}] "
                f"accounted={finding.get('capacity_accounted')}; "
                f"overlap={finding.get('overlap', 'unknown')}; "
                f"activity={finding.get('activity', 'unknown')}; "
                f"owner={((finding.get('owner') or {}).get('kind') or 'none')}:"
                f"{((finding.get('owner') or {}).get('id') or 'none')}; "
                f"unique={_human_bytes(finding.get('unique_bytes'))}; "
                f"shared={_human_bytes(finding.get('shared_bytes'))}; "
                f"potentially reclaimable="
                f"{_human_bytes(finding.get('potentially_reclaimable_bytes'))}; "
                f"evidence={','.join(map(str, finding.get('evidence') or ())) or 'none'}; "
                f"limitations={','.join(map(str, finding.get('limitations') or ())) or 'none'}"
            )


def cmd_resources(_cfg, args) -> None:
    action = args.action
    progress = (
        None if args.json
        else lambda category: print(f"  measuring: {category}")
    )
    if action == "status":
        requested_budget = args.budget if args.budget is not None else 15
        remaining_budget = _remaining_status_budget(args, requested_budget)
        if remaining_budget == 0:
            _emit(_budget_exhausted_payload(requested_budget), bool(args.json))
            raise SystemExit(1)
        service = resource_service(getattr(args, "remote", None))
        # Service construction may resolve a remote and is part of startup too.
        remaining_budget = _remaining_status_budget(args, requested_budget)
        if remaining_budget == 0:
            _emit(_budget_exhausted_payload(requested_budget), bool(args.json))
            raise SystemExit(1)
        status_kwargs = {
            "thorough": bool(args.thorough or args.deep),
            "budget_seconds": remaining_budget,
            "progress": progress,
            "deep": bool(args.deep),
        }
        if args.cancelled:
            status_kwargs["cancelled"] = True
        payload = service.status(
            **status_kwargs,
        )
        _restore_requested_budget(payload, requested_budget)
    elif action == "plan":
        service = resource_service(getattr(args, "remote", None))
        if args.cancelled:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "plan", status="failed",
                error=ResourceError(
                    "--cancelled is valid only for resources status",
                    "invalid_mode",
                ),
            )
            _emit(payload, bool(args.json))
            raise SystemExit(1)
        if args.deep:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "plan", status="failed",
                error=ResourceError(
                    "--deep is valid only for resources status",
                    "invalid_mode",
                ),
            )
            _emit(payload, bool(args.json))
            raise SystemExit(1)
        if not args.scope:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "plan", status="failed",
                error=ResourceError("--scope is required", "invalid_scope"),
            )
        else:
            payload = service.plan(
                args.scope,
                thorough=bool(args.thorough),
                budget_seconds=args.budget if args.budget is not None else 60,
                progress=progress,
            )
    else:
        service = resource_service(getattr(args, "remote", None))
        if args.cancelled:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "cleanup", status="refused",
                error=ResourceError(
                    "--cancelled is valid only for resources status",
                    "invalid_mode",
                ),
            )
            _emit(payload, bool(args.json))
            raise SystemExit(1)
        if args.deep:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "cleanup", status="refused",
                error=ResourceError(
                    "--deep is valid only for resources status",
                    "invalid_mode",
                ),
            )
            _emit(payload, bool(args.json))
            raise SystemExit(1)
        if not args.plan_id:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "cleanup", status="refused",
                error=ResourceError("--plan-id is required", "plan_not_found"),
            )
        else:
            payload = service.cleanup(args.plan_id, confirm=bool(args.confirm))
    _emit(payload, bool(args.json))
    if not payload.get("ok"):
        raise SystemExit(1)


register_specs((CommandSpec(
    name="resources",
    handler=cmd_resources,
    owner=__name__,
    order=205,
    configure=configure_parser,
    scope="global",
    destructive=True,
),))
