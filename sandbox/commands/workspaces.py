"""Feature-owned CLI adapters for explicit workspace lifecycle."""

from __future__ import annotations

import json

from sandbox.application.context import durable_job_dependencies
from sandbox.jobs.models import TargetRequest
from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.add_argument("action", choices=("create", "list", "status", "reset", "destroy"))
    parser.add_argument("--project-dir", default=".")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true")
    target.add_argument("--remote")
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--json", action="store_true")


def cmd_workspace(_cfg, args) -> None:
    service = durable_job_dependencies()["workspace_service"]
    request = TargetRequest(args.project_dir, local=args.local, remote=args.remote, workspace=args.workspace)
    try:
        result = getattr(service, args.action)(request)
    except Exception as exc:
        from sandbox.core import die
        die(str(exc))
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif args.action == "list":
        for item in result.get("workspaces", []): print(item["label"])
    else:
        print(f"{args.workspace}: {'ok' if result.get('ok') else result.get('code', 'failed')}")


register_specs((CommandSpec("workspace", cmd_workspace, configure=configure_parser, owner=__name__, scope="global"),))
