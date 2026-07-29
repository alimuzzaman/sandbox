"""Host-wide resource monitoring and confirmation-gated cleanup."""

from __future__ import annotations

import json

from sandbox.registry import CommandSpec, register_specs
from sandbox.resources.context import resource_service


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
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    target = payload.get("target") or {}
    print(
        f"resources {payload.get('action')}: {payload.get('status')} "
        f"({target.get('name', 'unresolved')})"
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
        deep = data.get("deep_attribution") or {}
        reconciliation = deep.get("reconciliation") or {}
        if deep:
            print(
                f"  deep accounted "
                f"{_human_bytes(reconciliation.get('accounted_bytes'))}; "
                f"residual "
                f"{_human_bytes(reconciliation.get('residual_unexplained_bytes'))}; "
                f"deleted-open "
                f"{_human_bytes(reconciliation.get('deleted_open_bytes'))}"
            )
            for capability in deep.get("capabilities") or ():
                print(
                    f"  tool {capability.get('category')}: "
                    f"{capability.get('name')} ({capability.get('status')})"
                )
            for coverage in deep.get("coverage") or ():
                if coverage.get("status") not in {"complete", "not_selected"}:
                    print(
                        f"  deep partial: {coverage.get('category')} "
                        f"({coverage.get('status')}: "
                        f"{coverage.get('reason') or 'unspecified'})"
                    )
            for finding in (deep.get("findings") or ())[:20]:
                print(
                    f"  deep {_human_bytes(finding.get('observed_bytes')):>12} "
                    f"{finding.get('kind', 'unknown'):>16} "
                    f"{finding.get('display_name', finding.get('finding_id'))} "
                    f"[{finding.get('guidance', 'monitoring_only')}]"
                )
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


def cmd_resources(_cfg, args) -> None:
    service = resource_service(getattr(args, "remote", None))
    action = args.action
    progress = (
        None if args.json
        else lambda category: print(f"  measuring: {category}")
    )
    if action == "status":
        payload = service.status(
            thorough=bool(args.thorough or args.deep),
            budget_seconds=args.budget if args.budget is not None else 15,
            progress=progress,
            deep=bool(args.deep),
        )
    elif action == "plan":
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
