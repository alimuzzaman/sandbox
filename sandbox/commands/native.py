"""Managed/incumbent native runtime inspection and explicit install entrypoint."""

from __future__ import annotations

import json

from sandbox.registry import CommandSpec, register_specs


ACTIONS = ("support", "preflight", "install-plan", "install", "status", "cleanup")


def configure_parser(parser):
    parser.description = "Inspect and manage explicitly selected native runtimes"
    parser.add_argument("action", nargs="?", choices=ACTIONS, default="support")
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--label", default="default")
    parser.add_argument("--web-server", choices=("nginx", "apache"), default="nginx")
    parser.add_argument("--json", action="store_true")


def _emit(value, as_json):
    if as_json: print(json.dumps(value, sort_keys=True)); return
    print(f"native {value.get('operation', 'status')}: {value.get('state', 'unknown')}")
    reason = value.get("reason") or {}
    if reason.get("code") and reason.get("code") != "ready":
        print(f"  {reason['code']}: {', '.join(reason.get('missing', ())) or reason.get('message', '')}")


def support():
    from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS
    return {"ok": True, "operation": "native_support", "state": "ready",
            "runtimes": [dict(value) for value in RUNTIME_DECLARATIONS], "mutated": False}


def cmd_native(cfg, args):
    if args.action == "support": result = support()
    elif args.action == "preflight":
        from sandbox.application.context import native_isolation_preflight
        result = native_isolation_preflight(cfg).inspect()
    else:
        result = {"ok": False, "operation": f"native_{args.action.replace('-', '_')}",
                  "state": "unsupported", "mutated": False,
                  "reason": {"code": "native_action_not_implemented",
                             "message": "This mutation remains disabled until its isolation proof is complete."}}
    _emit(result, bool(args.json))
    if not result.get("ok") and result.get("state") not in {"blocked", "unsupported",
                                                            "pending_confirmation"}:
        raise SystemExit(1)


register_specs((CommandSpec(
    name="native", handler=cmd_native, configure=configure_parser,
    owner=__name__, order=56, scope="project", destructive=True,
),))
