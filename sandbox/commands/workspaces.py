"""Feature-owned CLI adapters for explicit workspace lifecycle."""

from __future__ import annotations

import json

from sandbox.application.context import durable_job_dependencies
from sandbox.jobs.models import TargetRequest
from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.add_argument("action", choices=("create", "list", "status", "migrate", "reset", "destroy"))
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
    parser.add_argument("--json", action="store_true")


def cmd_workspace(_cfg, args) -> None:
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
            print(json.dumps({
                "ok": False,
                "error": {"code": code, "message": str(exc)},
            }, sort_keys=True))
            raise SystemExit(1)
        from sandbox.core import die
        die(f"{getattr(exc, 'code', 'workspace_operation_failed')}: {exc}")
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
