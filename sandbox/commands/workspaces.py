"""Feature-owned CLI adapters for explicit workspace lifecycle."""

from __future__ import annotations

import json

from sandbox.application.context import durable_job_dependencies
from sandbox.jobs.models import TargetRequest
from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.add_argument("action", choices=(
        "create", "list", "status", "migrate", "reset", "destroy",
        "release", "ttl", "reap",
    ))
    parser.add_argument("name", nargs="?", default=None,
                        help="workspace name for release and ttl")
    parser.add_argument("--project-dir", default=".")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true")
    target.add_argument("--remote")
    parser.add_argument("--workspace", "--workspace-label", dest="workspace", default="default")
    parser.add_argument("--project-identity")
    parser.add_argument("--workspace-id")
    parser.add_argument("--plan-id")
    parser.add_argument("--expected-legacy-namespace", help="scope a migration plan to one exact legacy namespace")
    parser.add_argument("--deployment-receipt", help=__import__("argparse").SUPPRESS)
    parser.add_argument("--inventory-digest")
    parser.add_argument("--index-generation", type=int)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--measure-sizes", action="store_true",
                        help="measure on-disk workspace sizes under a bounded walk "
                             "(default reports null sizes with a reason)")
    parser.add_argument("--mode", default="persistent")
    parser.add_argument("--confirm", "--yes", dest="confirm", action="store_true",
                        help="required before reset or destroy changes a workspace")
    # Creation is already idempotent; keep the documented spelling as an
    # explicit acknowledgement without creating a second behavior branch.
    parser.add_argument("--ensure", action="store_true",
                        help="accept idempotent workspace creation")
    parser.add_argument("--ttl", default=None,
                        help="retention duration such as 2h or 14d "
                             "(default 7d for workspaces and one-shot bases)")
    parser.add_argument("--dry-run", action="store_true",
                        help="reap: report what would be reclaimed, change nothing")
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument("--json", action="store_true")


_RETENTION_ACTIONS = frozenset({"release", "ttl", "reap"})
_REMOTE_WORKSPACE_RECOVERY = "./sb remote service migrate <name> --confirm --json"
_REMOTE_REVISION_STATES = frozenset({"match", "mismatch", "unavailable", "unknown"})
_REMOTE_OWNERSHIP_STATES = frozenset({"proven", "missing", "ambiguous", "unknown"})


def cmd_retention(args) -> None:
    """Agent-facing retention: declare done, extend, or reap what expired."""
    from sandbox.resources.context import reclaim_service
    from sandbox.resources.models import redact

    service = reclaim_service(getattr(args, "remote", None))
    if args.action == "reap":
        payload = service.reap(
            dry_run=bool(args.dry_run) or not args.confirm,
            ttl=args.ttl, confirm=bool(args.confirm),
            budget_seconds=args.budget if args.budget is not None else 900,
        )
    elif not args.name:
        from sandbox.core import die
        die(f"workspace {args.action} requires a workspace name")
        return
    elif args.action == "release":
        payload = service.release(args.name)
    else:
        if not args.ttl:
            from sandbox.core import die
            die("workspace ttl requires --ttl (for example --ttl 14d)")
            return
        payload = service.set_ttl(args.name, args.ttl)
    payload = redact(payload)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        error = payload.get("error") or {}
        print(f"workspace {args.action}: {payload.get('status')}"
              + (f" — {error.get('code')}: {error.get('message')}"
                 if error else ""))
        data = payload.get("data") or {}
        if args.action == "ttl" and data.get("expires_at"):
            print(f"  expires: {data['expires_at']}")
        if args.action == "reap":
            print(f"  dry-run: {bool(data.get('dry_run'))}")
            for item in (data.get("candidates") or ())[:200]:
                print(f"    {item.get('kind')} {item.get('display_name')} "
                      f"[{item.get('reason')}]")
            for item in (data.get("outcomes") or ())[:200]:
                print(f"    {item.get('status')} {item.get('resource_id')} "
                      f"[{item.get('reason')}]")
            if data.get("manifest_path"):
                print(f"  manifest: {data['manifest_path']}")
    if not payload.get("ok"):
        raise SystemExit(1)


def cmd_workspace(_cfg, args) -> None:
    if args.action in _RETENTION_ACTIONS:
        cmd_retention(args)
        return
    if (args.action in {"reset", "destroy"} or
            args.action == "migrate" and args.plan_id) and not args.confirm:
        from sandbox.core import die
        die(f"workspace {args.action} requires --confirm")
    service = durable_job_dependencies()["workspace_service"]
    request = TargetRequest(
        args.project_dir, local=args.local, remote=args.remote,
        workspace=args.workspace,
        project_identity=getattr(args, "project_identity", None),
        workspace_id=getattr(args, "workspace_id", None),
        migration_plan_id=getattr(args, "plan_id", None),
        confirm=args.confirm,
        expected_legacy_namespace=getattr(args, "expected_legacy_namespace", None),
        checkout_locator=getattr(args, "checkout_locator", None),
        deployment_receipt=getattr(args, "deployment_receipt", None),
        inventory_digest=getattr(args, "inventory_digest", None),
        index_generation=getattr(args, "index_generation", None),
        limit=getattr(args, "limit", 50), active_only=getattr(args, "active_only", False),
        measure_sizes=getattr(args, "measure_sizes", False),
        mode=getattr(args, "mode", "persistent"),
    )
    try:
        method = ("migration_apply" if args.action == "migrate" and args.plan_id
                  else "migration_plan" if args.action == "migrate"
                  else args.action)
        result = getattr(service, method)(request)
    except Exception as exc:
        if args.json:
            code = getattr(exc, "code", "workspace_operation_failed")
            error = {"code": code, "message": str(exc)}
            details = getattr(exc, "details", None)
            # Workspace preflight details are finite, adapter-owned values.
            # Preserve only those fields so JSON callers receive the observed
            # state and supported recovery command without leaking arbitrary
            # exception detail from a remote boundary.
            if isinstance(details, dict):
                observed = details.get("observed")
                if (isinstance(observed, dict) and
                        set(observed) == {"ownership", "runtime_revision_state"} and
                        isinstance(observed.get("ownership"), str) and
                        isinstance(observed.get("runtime_revision_state"), str) and
                        observed.get("ownership") in _REMOTE_OWNERSHIP_STATES and
                        observed.get("runtime_revision_state") in _REMOTE_REVISION_STATES):
                    error["observed"] = {
                        "ownership": observed["ownership"],
                        "runtime_revision_state": observed["runtime_revision_state"],
                    }
                if details.get("recovery_command") == _REMOTE_WORKSPACE_RECOVERY:
                    error["recovery_command"] = _REMOTE_WORKSPACE_RECOVERY
            print(json.dumps({
                "ok": False,
                "error": error,
            }, sort_keys=True))
            raise SystemExit(1)
        from sandbox.core import die
        message = str(exc)
        details = getattr(exc, "details", None)
        if (isinstance(details, dict) and
                details.get("recovery_command") == _REMOTE_WORKSPACE_RECOVERY and
                _REMOTE_WORKSPACE_RECOVERY not in message):
            message += f"; recovery: {_REMOTE_WORKSPACE_RECOVERY}"
        die(f"{getattr(exc, 'code', 'workspace_operation_failed')}: {message}")
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif args.action == "list":
        index = result.get("index") or {}
        if index.get("complete") is False:
            print("WARNING: " + (result.get("warning") or "workspace index is incomplete") +
                  f" [{index.get('code', 'workspace_index_incomplete')}]")
        for item in result.get("workspaces", []): print(item["label"])
        on_disk = result.get("on_disk") or {}
        if on_disk.get("available") is False:
            print(f"on-disk: unavailable ({on_disk.get('reason')})")
        for entry in on_disk.get("entries", []):
            size = (entry.get("size_bytes") if entry.get("size_bytes") is not None
                    else entry.get("size_reason"))
            print(f"on-disk {'indexed' if entry.get('indexed') else 'UNINDEXED'} "
                  f"{entry.get('path')} size={size} age={entry.get('age_seconds')}s")
        if on_disk.get("truncated"):
            print(f"on-disk: {len(on_disk.get('entries', []))} of "
                  f"{on_disk.get('total')} entries shown")
    elif args.action == "migrate":
        print(result.get("plan_id", "migration") + ": " +
              ("ok" if result.get("ok") else result.get("code", "failed")))
    else:
        print(f"{args.workspace}: {'ok' if result.get('ok') else result.get('code', 'failed')}")
    if result.get("ok") is False:
        raise SystemExit(1)


register_specs((CommandSpec("workspace", cmd_workspace, configure=configure_parser, owner=__name__, scope="global"),))
