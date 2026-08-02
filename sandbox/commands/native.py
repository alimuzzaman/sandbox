"""Managed/incumbent native runtime inspection and explicit install entrypoint."""

from __future__ import annotations

import json
import sys

from sandbox.registry import CommandSpec, register_specs


ACTIONS = ("support", "preflight", "baseline", "install-plan", "install", "status", "cleanup")


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


def _runtime_result(cfg, args, operation):
    """Keep status and conservative cleanup on the adapter contract.

    This command never calls package management: installed host packages are
    shared prerequisites and have a separate reviewed lifecycle.
    """
    from sandbox.application.context import runtime_service
    from sandbox.runtimes.base import OperationError, OperationRequest

    result = runtime_service(cfg).invoke(OperationRequest(
        args.project_dir, operation, label=args.label,
    ))
    if isinstance(result, OperationError):
        return {"ok": False, "operation": f"native_{operation}", "state": "blocked",
                "mutated": False, "reason": {"code": result.code,
                                                "message": result.message}}
    return {"ok": result.ok, "operation": f"native_{operation}", **dict(result.data)}


def support():
    from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS
    return {"ok": True, "operation": "native_support", "state": "ready",
            "runtimes": [dict(value) for value in RUNTIME_DECLARATIONS], "mutated": False}


def cmd_native(cfg, args):
    if args.action == "support": result = support()
    elif args.action == "preflight":
        from sandbox.application.context import native_isolation_preflight
        result = native_isolation_preflight(cfg).inspect()
    elif args.action == "baseline":
        from sandbox.application.context import managed_host_service_baseline
        try:
            observed = managed_host_service_baseline(cfg).observe()
            result = {"operation": "native_baseline", "state": "ready",
                      "mutated": False, **observed,
                      "reason": {"code": "ready"}}
        except (OSError, RuntimeError, ValueError) as exc:
            result = {"ok": False, "operation": "native_baseline",
                      "state": "blocked", "mutated": False,
                      "reason": {"code": "host_service_baseline_unavailable",
                                 "message": str(exc)}}
    elif args.action == "install-plan":
        from sandbox.application.context import managed_package_planner
        try:
            plan = managed_package_planner(cfg).plan(web_server=args.web_server)
            result = {"ok": True, "operation": "native_install_plan", "state": "ready",
                      "mutated": False, "matrix_id": plan.matrix_id,
                      "host_packages": [dict(item) for item in plan.host_packages],
                      "image_packages": [dict(item) for item in plan.image_packages],
                      "sources": [dict(item) for item in plan.sources],
                      "service_effects": [dict(item) for item in plan.service_effects],
                      "owned_roots": list(plan.owned_roots),
                      "privilege_actions": list(plan.privilege_actions),
                      "simulation_digest": plan.simulation_digest,
                      "reason": {"code": "ready"}}
        except (OSError, ValueError) as exc:
            result = {"ok": False, "operation": "native_install_plan", "state": "blocked",
                      "mutated": False,
                      "reason": {"code": "version_unavailable", "message": str(exc)}}
    elif args.action == "install":
        from sandbox.application.context import managed_package_planner, managed_package_service
        try:
            plan = managed_package_planner(cfg).plan(web_server=args.web_server)
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
            result = {"operation": "native_install",
                      **managed_package_service(cfg, web_server=args.web_server).apply(
                          plan, interactive=interactive)}
        except (OSError, ValueError) as exc:
            result = {"ok": False, "operation": "native_install", "state": "blocked",
                      "mutated": False,
                      "reason": {"code": "version_unavailable", "message": str(exc)}}
    elif args.action == "status":
        result = _runtime_result(cfg, args, "status")
    elif args.action == "cleanup":
        result = _runtime_result(cfg, args, "destroy")
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
