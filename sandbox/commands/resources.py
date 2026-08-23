"""Host-wide resource monitoring and confirmation-gated cleanup."""

from __future__ import annotations

import argparse
import json
import math
import time

from sandbox.config.storage_monitor import StorageMonitorConfigError
from sandbox.registry import CommandSpec, register_specs
from sandbox.resources.context import reclaim_service, resource_service
from sandbox.resources.models import redact
from sandbox.resources.monitor import record_path, resolve_policy


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


def _monitor_target(remote: str | None) -> dict[str, str]:
    """Return the public target descriptor used for CLI-side refusals."""
    if remote:
        return {"kind": "remote", "name": str(remote)}
    return {"kind": "local", "name": "local"}


def _monitor_error(exc: Exception, fallback: str) -> dict[str, object]:
    """Bound and redact policy/service errors before they reach the renderer."""
    code = getattr(exc, "code", None)
    if (
        not isinstance(code, str)
        or not code
        or len(code) > 64
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in code)
    ):
        code = fallback
    message = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if not message:
        message = fallback.replace("_", " ")
    return {
        "code": code,
        "message": message[:240],
        "retryable": bool(getattr(exc, "retryable", False)),
    }


def _monitor_refusal(remote: str | None, exc: Exception, fallback: str) -> dict:
    """Build the standard monitor envelope without constructing a service."""
    return {
        "schema_version": 1,
        "ok": False,
        "action": "monitor",
        "status": "refused",
        "target": _monitor_target(remote),
        "data": {},
        "error": _monitor_error(exc, fallback),
    }


def _monitor_invalid_mode(args) -> dict | None:
    """Reject options that belong to status/plan/cleanup before policy lookup."""
    invalid = (
        ("--scope", getattr(args, "scope", None) is not None),
        ("--tier", getattr(args, "tier", None) is not None),
        ("--plan-id", getattr(args, "plan_id", None) is not None),
        ("--confirm", bool(getattr(args, "confirm", False))),
        ("--thorough", bool(getattr(args, "thorough", False))),
        ("--deep", bool(getattr(args, "deep", False))),
        ("--fast", bool(getattr(args, "fast", False))),
        ("--refresh", bool(getattr(args, "refresh", False))),
        ("--cancelled", bool(getattr(args, "cancelled", False))),
    )
    for flag, present in invalid:
        if present:
            from sandbox.resources.service import ResourceError
            return _monitor_refusal(
                getattr(args, "remote", None),
                ResourceError(
                    f"{flag} is valid only for resources status, plan, or cleanup",
                    "invalid_mode",
                ),
                "invalid_mode",
            )
    return None


def _run_monitor(args) -> dict:
    """Resolve monitor policy, then invoke the host-facing monitor service."""
    invalid = _monitor_invalid_mode(args)
    if invalid is not None:
        return invalid

    remote = getattr(args, "remote", None)
    # This must remain before service construction: policy refusals (including
    # unknown targets and unsafe automatic tiers) are local and host-free.
    try:
        policy = resolve_policy(remote)
    except (StorageMonitorConfigError, ValueError, OSError) as exc:
        return _monitor_refusal(remote, exc, "policy_resolution_failed")
    except Exception as exc:
        return _monitor_refusal(remote, exc, "policy_resolution_failed")

    try:
        service = reclaim_service(remote)
        payload = service.monitor(
            policy,
            trigger="scheduled" if bool(getattr(args, "scheduled", False)) else "manual",
            dry_run=bool(getattr(args, "dry_run", False)),
            budget_seconds=(
                args.budget if getattr(args, "budget", None) is not None else 900
            ),
        )
    except Exception as exc:
        return _monitor_refusal(remote, exc, "monitor_service_failed")
    if not isinstance(payload, dict) or payload.get("action") != "monitor":
        from sandbox.resources.service import ResourceError
        return _monitor_refusal(
            remote,
            ResourceError("monitor returned an invalid result", "monitor_result_invalid"),
            "monitor_result_invalid",
        )
    return payload


def configure_parser(parser) -> None:
    parser.description = "Monitor host storage and safely clean managed resources"
    parser.add_argument("action", choices=("status", "plan", "cleanup", "monitor"))
    parser.add_argument("--remote", default=None, help="configured remote name")
    parser.add_argument("--scope", choices=("cache", "stale"), default=None)
    parser.add_argument(
        "--tier",
        choices=("safe", "tmp", "all"),
        default=None,
        help=(
            "tiered reclamation of managed deployment storage; strictly nested "
            "(safe subset of tmp subset of all)"
        ),
    )
    parser.add_argument("--thorough", action="store_true")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="run bounded filesystem, deleted-open, and engine attribution",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help=(
            "always-available attribution: capacity plus the cached host "
            "directory index, with no disk walk and no engine inventory"
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild the cached host directory index instead of reusing it",
    )
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument(
        "--cancelled",
        action="store_true",
        help="express a pre-cancelled status request (for non-interactive callers)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help=(
            "accept a durable host-level scan and return a job id for polling"
        ),
    )
    parser.add_argument(
        "--request-id",
        default=None,
        help="replay-safe idempotency key required with --detach",
    )
    # Internal worker entrypoint used by the durable detached resource scan.
    # It is intentionally hidden: users submit with --detach and observe with
    # job-status/job-output rather than invoking the worker directly.
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--plan-id", default=None)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="record this monitor invocation as a scheduled trigger",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="observe capacity and retention candidates without deleting",
    )
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


def _monitor_percent(value, *, fallback_bytes=None, total_bytes=None) -> str:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0 <= float(value) <= 1
    ):
        return f"{float(value) * 100:.1f}%"
    if (
        isinstance(fallback_bytes, int)
        and isinstance(total_bytes, int)
        and not isinstance(fallback_bytes, bool)
        and not isinstance(total_bytes, bool)
        and total_bytes > 0
    ):
        return f"{fallback_bytes * 100.0 / total_bytes:.1f}%"
    return "unknown"


def _monitor_next_command(target: dict, level: str, threshold: str | None) -> str:
    """Return the review command for warning/critical output."""
    remote = target.get("name") if target.get("kind") == "remote" else None
    suffix = f" --remote {remote}" if remote else ""
    if level == "critical" or threshold == "critical_ratio":
        return f"sb resources cleanup --tier safe --confirm{suffix}"
    return f"sb resources plan --tier safe{suffix}"


def _emit_monitor(payload: dict, as_json: bool) -> None:
    """Render a monitor record without hiding capacity evidence behind errors."""
    payload = redact(payload)
    if as_json:
        # Keep the standard envelope and the complete MonitorRunRecord intact.
        print(json.dumps(payload, sort_keys=True))
        return

    target = payload.get("target") or {}
    target_name = target.get("name", "unresolved")
    print(f"resources monitor: {payload.get('status', 'unknown')} ({target_name})")
    if target:
        print(
            f"  target kind={target.get('kind', 'unknown')}; "
            f"name={target_name}"
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    level = str(data.get("level") or payload.get("status") or "unknown")
    free_bytes = data.get("free_bytes")
    total_bytes = data.get("total_bytes")
    free_ratio = data.get("free_ratio")
    free_percent = _monitor_percent(
        free_ratio, fallback_bytes=free_bytes, total_bytes=total_bytes,
    )
    warn_ratio = data.get("warn_ratio")
    critical_ratio = data.get("critical_ratio")
    threshold = data.get("threshold_crossed")

    if data:
        if level in {"warning", "critical"}:
            threshold_ratio = data.get(threshold) if isinstance(threshold, str) else None
            print(
                f"  CAPACITY {level.upper()}: {_human_bytes(free_bytes)} free of "
                f"{_human_bytes(total_bytes)} ({free_percent}); threshold "
                f"{threshold or 'unknown'} ({_monitor_percent(threshold_ratio)})"
            )
            guidance = data.get("guidance")
            if isinstance(guidance, str) and guidance:
                print(f"    {guidance}")
            print(
                f"    next: `{_monitor_next_command(target, level, threshold)}`"
            )
        elif level == "unknown":
            print(
                f"  CAPACITY UNKNOWN: {_human_bytes(free_bytes)} free of "
                f"{_human_bytes(total_bytes)} ({free_percent}); thresholds warn "
                f"{_monitor_percent(warn_ratio)} / critical "
                f"{_monitor_percent(critical_ratio)}"
            )
            guidance = data.get("guidance")
            if isinstance(guidance, str) and guidance:
                print(f"    {guidance}")
        else:
            # Normal output intentionally contains no warning line.
            print(
                f"  {_human_bytes(free_bytes)} free of {_human_bytes(total_bytes)} "
                f"({free_percent}); thresholds warn {_monitor_percent(warn_ratio)} "
                f"/ critical {_monitor_percent(critical_ratio)}"
            )

        auto = data.get("auto")
        if isinstance(auto, dict):
            state = "enabled" if auto.get("enabled") else "disabled"
            details = [
                f"eligible={auto.get('eligible', False)}",
                f"tier={auto.get('tier') or 'none'}",
                f"ran={auto.get('ran', False)}",
                f"reclaimed {_human_bytes(auto.get('reclaimed_bytes'))}",
            ]
            if auto.get("reason"):
                details.append(f"reason={auto['reason']}")
            print(f"  automatic reclamation: {state}; " + "; ".join(details))

        reap = data.get("reap")
        if isinstance(reap, dict):
            candidates = reap.get("candidates", 0)
            if reap.get("dry_run"):
                print(
                    f"  reap: dry run — {candidates} candidates, "
                    f"{_human_bytes(reap.get('reclaimed_bytes'))} would be reclaimed"
                )
            else:
                print(
                    f"  reap: enabled — {candidates} candidates, "
                    f"{_human_bytes(reap.get('reclaimed_bytes'))} reclaimed"
                )

        try:
            record_target = {
                "kind": target.get("kind"), "name": target.get("name"),
            }
            if record_target["kind"] and record_target["name"]:
                print(f"  record: {record_path(record_target)}")
        except (TypeError, ValueError, OSError):
            # A refusal or malformed test seam has no record path to report.
            pass

    errors = data.get("errors")
    if isinstance(errors, list) and errors:
        print("  errors:")
        for error in errors:
            if isinstance(error, dict):
                print(
                    f"    {error.get('code', 'monitor_error')}: "
                    f"{error.get('message', 'monitor failed')}"
                )
    top_error = payload.get("error")
    if isinstance(top_error, dict):
        # Run records can carry the same error in ``data.errors``.  Avoid
        # printing a duplicate while still preserving refusal evidence.
        top_pair = (top_error.get("code"), top_error.get("message"))
        data_pairs = {
            (item.get("code"), item.get("message"))
            for item in errors or () if isinstance(item, dict)
        }
        if top_pair not in data_pairs:
            print(
                f"  {top_error.get('code', 'monitor_error')}: "
                f"{top_error.get('message', 'monitor failed')}"
            )


def _emit(payload: dict, as_json: bool) -> None:
    payload = redact(payload)
    if payload.get("action") == "monitor":
        _emit_monitor(payload, as_json)
        return
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
    if payload.get("status") == "accepted" and payload.get("kind") == "resource-scan":
        probe = payload.get("probe") or {}
        poll = payload.get("poll") or {}
        print(
            f"  detached scan accepted: job {payload.get('job_id', 'unknown')} "
            f"(budget {probe.get('budget_seconds', 'unknown')}s; "
            f"worker {probe.get('worker_mode', 'unknown')})"
        )
        if poll.get("status"):
            print(f"  status: {poll['status']}")
        if poll.get("output"):
            print(f"  output: {poll['output']}")
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
        _emit_unattributed(capacity, summary)
        print(
            f"  reclaimable {_human_bytes(summary.get('reclaimable_bytes'))}; "
            f"unknown {_human_bytes(summary.get('unknown_bytes'))}"
        )
        _emit_directory_index(data.get("deep_attribution") or {})
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
                measured = category.get("measured_bytes")
                skipped = category.get("unmeasured_count")
                detail = (
                    ""
                    if measured is None and skipped is None
                    else (
                        f"; measured {_human_bytes(measured)}"
                        f"; unmeasured rows {skipped}"
                    )
                )
                print(
                    f"  partial: {category.get('category')} "
                    f"({category.get('status')}"
                    f"{': ' + str(category['reason']) if category.get('reason') else ''}"
                    f"){detail}"
                )
        _emit_reclaim(data)
        _emit_deep(data.get("deep_attribution") or {})
    elif payload.get("action") in {"plan", "reap"} and data.get("tier"):
        _emit_reclaim_plan(data)
    elif payload.get("action") in {"cleanup", "reap"} and data.get("tier"):
        _emit_reclaim_cleanup(data)
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


def _emit_unattributed(capacity: dict, summary: dict) -> None:
    """Lead with the gap: unattributed space is the finding, not a footnote."""
    used = capacity.get("used_bytes")
    unknown = summary.get("unknown_bytes")
    if not isinstance(unknown, int) or not isinstance(used, int) or used <= 0:
        return
    share = unknown * 100.0 / used
    marker = "UNATTRIBUTED" if share >= 10 else "unattributed"
    print(
        f"  {marker}: {_human_bytes(unknown)} of {_human_bytes(used)} used "
        f"({share:.1f}%) is not attributed to any measured resource"
    )
    if share >= 10:
        print(
            "  attribution is incomplete — rerun with --deep, or rebuild the "
            "host directory index with --refresh"
        )


def _emit_directory_index(deep: dict) -> None:
    index = deep.get("directory_index") if isinstance(deep, dict) else None
    if not isinstance(index, dict):
        return
    age = index.get("age_seconds")
    print(
        f"  directory index: {index.get('source', 'unknown')}; "
        f"complete={index.get('complete')}; stale={index.get('stale')}; "
        f"age={'unknown' if age is None else str(int(age)) + 's'}; "
        f"depth={index.get('depth', 'unknown')}; "
        f"floor {_human_bytes(index.get('minimum_row_bytes'))}"
    )
    if index.get("source") == "cache_missing":
        print(
            "  no cached host directory index — run "
            "`sb resources status --deep --refresh` to build one"
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


def _emit_reclaim(data: dict) -> None:
    """Lead with the classification: it is the answer, not an appendix."""
    block = data.get("reclaim")
    if not isinstance(block, dict):
        return
    print(
        f"  reclaim inventory: {block.get('status', 'unknown')}"
        f"{' (' + str(block['reason']) + ')' if block.get('reason') else ''}; "
        f"root {block.get('deployment_root', 'unknown')}"
    )
    for row in block.get("classes") or ():
        print(
            f"    {row.get('class', 'UNKNOWN'):>9} "
            f"{row.get('count', 0):>4} entries "
            f"{_human_bytes(row.get('bytes')):>12}"
            + (
                f"  ({row.get('unmeasured')} unmeasured)"
                if row.get("unmeasured") else ""
            )
        )
    volumes = block.get("volumes") or {}
    print(
        f"    volumes: {volumes.get('eligible', 0)} workspace-scoped eligible "
        f"({_human_bytes(volumes.get('eligible_bytes'))}); "
        f"{volumes.get('protected', 0)} protected"
    )
    tiers = block.get("tiers") or {}
    if tiers:
        print(
            "    tier totals: " + " | ".join(
                f"{name} {tiers[name]['candidates']} "
                f"({_human_bytes(tiers[name]['bytes'])})"
                for name in ("safe", "tmp", "all") if name in tiers
            )
        )
    drift = block.get("drift") or {}
    print(
        f"    index drift: {drift.get('indexed_absent', 0)} indexed but absent; "
        f"{drift.get('present_unindexed', 0)} present but unindexed"
    )
    if block.get("truncated") or block.get("unmeasured_count"):
        print(
            f"    PARTIAL: {block.get('unmeasured_count', 0)} entries unmeasured"
            f"{'; entry walk truncated' if block.get('truncated') else ''}"
        )
    pressure = block.get("capacity_pressure") or {}
    if pressure.get("level") in {"warning", "critical"}:
        print(
            f"    CAPACITY {str(pressure.get('level')).upper()}: "
            f"{_human_bytes(pressure.get('free_bytes'))} free "
            f"({(pressure.get('free_ratio') or 0) * 100:.1f}%); threshold "
            f"{pressure.get('threshold_crossed')} — {pressure.get('guidance')}"
        )


def _emit_reclaim_plan(data: dict) -> None:
    print(f"  plan: {data.get('plan_id')}  tier: {data.get('tier')}")
    print(f"  expires: {data.get('expires_at')}")
    print(
        f"  candidates: {len(data.get('candidates') or ())}; "
        f"estimated {_human_bytes(data.get('estimated_reclaimable_bytes'))}"
    )
    for item in (data.get("candidates") or ())[:200]:
        print(
            f"    {_human_bytes(item.get('bytes')):>12} "
            f"{item.get('kind', 'unknown'):>9} "
            f"{item.get('class', 'unknown'):>8} "
            f"{item.get('display_name', item.get('locator'))} "
            f"[{item.get('reason')}] mtime={item.get('modified_at') or 'unknown'}"
        )
    skipped = data.get("skipped") or ()
    print(f"  skipped: {len(skipped)}")
    for item in skipped[:200]:
        print(
            f"    {item.get('kind', 'unknown'):>9} "
            f"{item.get('display_name', item.get('locator'))} "
            f"[{item.get('reason')}]"
        )
    totals = data.get("tier_totals") or {}
    if totals:
        print(
            "  tier totals: " + " | ".join(
                f"{name} {_human_bytes(totals[name])}"
                for name in ("safe", "tmp", "all") if name in totals
            )
        )
    if data.get("truncated") or data.get("unmeasured_count"):
        print(
            f"  PARTIAL inventory: {data.get('unmeasured_count', 0)} entries "
            f"unmeasured{'; walk truncated' if data.get('truncated') else ''} — "
            "candidates below are what could be measured, not the whole host"
        )


def _emit_reclaim_cleanup(data: dict) -> None:
    print(f"  tier: {data.get('tier')}  run: {data.get('run_id')}")
    print(f"  manifest: {data.get('manifest_path')}")
    print(
        f"  processed {data.get('processed_candidates')} of "
        f"{data.get('planned_candidates')} candidates; reclaimed "
        f"{_human_bytes(data.get('observed_reclaimed_bytes'))}"
        + ("; RESUMED" if data.get("resumed") else "")
    )
    counts: dict[str, int] = {}
    for item in data.get("outcomes") or ():
        counts[item.get("status", "unknown")] = counts.get(
            item.get("status", "unknown"), 0,
        ) + 1
    print("  outcomes: " + ", ".join(
        f"{name}={value}" for name, value in sorted(counts.items())
    ) or "  outcomes: none")
    for item in data.get("outcomes") or ():
        if item.get("status") not in {"removed", "already_absent"}:
            print(
                f"    {item.get('status')}: {item.get('resource_id')} "
                f"[{item.get('reason')}]"
            )
    reconciled = data.get("reconciled") or {}
    if reconciled:
        print(
            f"  reconciled: registry {reconciled.get('registry_removed', 0)}; "
            f"index {reconciled.get('index_removed', 0)} "
            f"(pending {reconciled.get('index_pending', 0)}); "
            f"leases {reconciled.get('leases_removed', 0)}"
            + (
                f"; {reconciled.get('status')}"
                f" ({reconciled.get('reason')})"
                if reconciled.get("status") != "complete" else ""
            )
        )
    if data.get("budget_exhausted"):
        print(
            "  BUDGET EXHAUSTED: not every candidate was processed — re-run the "
            "same tier to continue"
        )


def _tier_action(args) -> bool:
    return bool(getattr(args, "tier", None)) and args.action in {"plan", "cleanup"}


def _run_tier(args) -> dict:
    from sandbox.resources.context import reclaim_service

    service = reclaim_service(getattr(args, "remote", None))
    if args.action == "plan":
        return service.plan(
            args.tier,
            budget_seconds=args.budget if args.budget is not None else 60,
        )
    return service.cleanup(
        tier=args.tier, plan_id=getattr(args, "plan_id", None),
        confirm=bool(args.confirm),
        budget_seconds=args.budget if args.budget is not None else 900,
    )


def cmd_resources(_cfg, args) -> None:
    action = args.action
    if action == "status" and bool(getattr(args, "worker", False)):
        from sandbox.resources.detached import run_worker
        worker_status = run_worker(args)
        if worker_status:
            raise SystemExit(worker_status)
        return
    if bool(getattr(args, "detach", False)) and action != "status":
        from sandbox.resources.service import ResourceError, result
        payload = result(
            False, action, status="failed",
            error=ResourceError(
                "--detach is valid only for resources status", "invalid_mode",
            ),
        )
        _emit(payload, bool(args.json))
        raise SystemExit(1)
    if getattr(args, "request_id", None) and not bool(getattr(args, "detach", False)):
        from sandbox.resources.service import ResourceError, result
        payload = result(
            False, action, status="failed",
            error=ResourceError(
                "--request-id requires --detach", "invalid_mode",
            ),
        )
        _emit(payload, bool(args.json))
        raise SystemExit(1)
    if action == "monitor":
        payload = _run_monitor(args)
        _emit(payload, bool(args.json))
        if not payload.get("ok"):
            raise SystemExit(1)
        return
    if getattr(args, "scheduled", False) or getattr(args, "dry_run", False):
        from sandbox.resources.service import ResourceError, result
        flag = "--scheduled" if getattr(args, "scheduled", False) else "--dry-run"
        payload = result(
            False, action, status="failed",
            error=ResourceError(
                f"{flag} is valid only for resources monitor", "invalid_mode",
            ),
        )
        _emit(payload, bool(args.json))
        raise SystemExit(1)
    if getattr(args, "tier", None) and getattr(args, "scope", None):
        from sandbox.resources.service import ResourceError, result
        _emit(result(
            False, action, status="failed",
            error=ResourceError("--tier and --scope are mutually exclusive",
                                "invalid_mode"),
        ), bool(args.json))
        raise SystemExit(1)
    if getattr(args, "tier", None) and action == "status":
        from sandbox.resources.service import ResourceError, result
        _emit(result(
            False, action, status="failed",
            error=ResourceError("--tier is valid only for plan and cleanup",
                                "invalid_mode"),
        ), bool(args.json))
        raise SystemExit(1)
    if _tier_action(args):
        payload = _run_tier(args)
        _emit(payload, bool(args.json))
        if not payload.get("ok"):
            raise SystemExit(1)
        return
    progress = (
        None if args.json
        else lambda category: print(f"  measuring: {category}")
    )
    if action == "status":
        fast = bool(getattr(args, "fast", False))
        refresh = bool(getattr(args, "refresh", False))
        if fast and refresh:
            from sandbox.resources.service import ResourceError, result
            payload = result(
                False, "status", status="failed",
                error=ResourceError(
                    "--fast and --refresh are mutually exclusive",
                    "invalid_mode",
                ),
            )
            _emit(payload, bool(args.json))
            raise SystemExit(1)
        if getattr(args, "detach", False):
            from sandbox.resources.detached import start
            payload = start(args)
            _emit(payload, bool(args.json))
            if not payload.get("ok"):
                raise SystemExit(1)
            return
        default_budget = 10 if fast else 900 if refresh else 15
        requested_budget = (
            args.budget if args.budget is not None else default_budget
        )
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
            "thorough": bool(args.thorough or args.deep) and not fast,
            "budget_seconds": remaining_budget,
            "progress": progress,
            "deep": bool(args.deep or fast or refresh),
        }
        if fast or refresh:
            # Keep the default call compatible with providers that predate
            # the cached host directory index.
            status_kwargs["directory_cache"] = (
                "cache_only" if fast else "refresh"
            )
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
