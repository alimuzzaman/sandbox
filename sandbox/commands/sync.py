"""CLI adapter for opt-in agent-aware remote source synchronization."""

from __future__ import annotations

import json

from sandbox.application.sync_service import build_sync_service
from sandbox.registry import CommandSpec, register_specs
from sandbox.sync.service import SyncServiceError


def _common(parser, *, request: bool = False) -> None:
    parser.add_argument("--project-dir", required=True,
                        help="registered project checkout to synchronize")
    parser.add_argument("--remote", required=True,
                        help="explicit provisioned remote name")
    parser.add_argument("--workspace-id", required=True,
                        help="durable remote workspace identifier")
    if request:
        parser.add_argument("--request-id", required=True,
                            help="replay-safe request identity")
    parser.add_argument("--json", action="store_true", help="emit the bounded JSON envelope")


def configure_parser(parser) -> None:
    parser.description = "Opt-in staged source synchronization for disposable remote workspaces"
    actions = parser.add_subparsers(dest="sync_action", required=True)

    once = actions.add_parser("once", help="capture and transfer one screened generation")
    _common(once, request=True)
    once.add_argument("--include", action="append", default=[],
                      help="explicit relative path to include in the screened generation")

    status = actions.add_parser("status", help="read local synchronization state")
    _common(status)

    start = actions.add_parser("start", help="start live or checkpoint mode")
    _common(start)
    start.add_argument("--mode", choices=("live", "checkpoint"), required=True)

    stop = actions.add_parser("stop", help="stop future automatic transfers")
    _common(stop)


def _bounded_error(code: str, *, message: str = "synchronization operation failed") -> dict:
    return {"ok": False, "status": "failed", "code": code, "message": message}


def _emit(result: dict, args) -> None:
    if result.get("ok"):
        result = {key: result[key] for key in
                  ("ok", "status", "relationship", "generation", "job", "error")
                  if key in result}
    else:
        result = {key: result[key] for key in
                  ("ok", "status", "code", "message", "relationship", "request_id",
                   "accepted_generation", "pending_generation", "retryable")
                  if key in result}
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return
    if result.get("ok"):
        status = result.get("status", "complete")
        generation = result.get("generation") or {}
        generation_id = generation.get("id") if isinstance(generation, dict) else None
        suffix = f" generation={generation_id}" if generation_id else ""
        print(f"sync {status}{suffix}")
    else:
        print(f"error: {result.get('message', 'synchronization operation failed')} "
              f"({result.get('code', 'sync_failed')})")


def cmd_sync(_cfg, args) -> None:
    service = build_sync_service()
    try:
        common = {
            "project_dir": args.project_dir,
            "remote": args.remote,
            "workspace_id": args.workspace_id,
        }
        if args.sync_action == "once":
            result = service.once(
                **common, request_id=args.request_id,
                explicit_includes=tuple(args.include or ()),
            )
        elif args.sync_action == "status":
            result = service.status(**common)
        elif args.sync_action == "start":
            result = service.start(**common, mode=args.mode)
        elif args.sync_action == "stop":
            result = service.stop(**common)
        else:
            result = _bounded_error("invalid_sync_action")
    except SyncServiceError as exc:
        result = _bounded_error(exc.code)
    except Exception:
        # Do not forward path/config/transport exceptions through the public
        # command envelope. Detailed diagnostics remain in bounded logs.
        result = _bounded_error("sync_failed")
    _emit(result, args)
    if not result.get("ok"):
        raise SystemExit(1)


register_specs((CommandSpec(
    "sync", cmd_sync, configure=configure_parser, owner=__name__, scope="project",
    predispatch_policy=lambda _args: True,
    help="opt-in staged source synchronization for a disposable remote workspace",
),))


__all__ = ["cmd_sync", "configure_parser"]
