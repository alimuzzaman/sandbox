"""Project-scoped domain operations with staged legacy compatibility."""

from __future__ import annotations

import json
import sys

from sandbox.network.manifest import BUILTIN_RESOLVER_ADAPTERS
from sandbox.registry import CommandSpec, register_specs


LEGACY_ACTIONS = frozenset({"setup", "up", "down", "teardown", "repair-ca", "list"})
DOMAIN_ACTIONS = (
    "setup", "up", "down", "teardown", "repair-ca", "list",
    "detect", "support", "plan", "apply", "status", "cleanup", "reconsider", "ingress",
)


def configure_parser(parser) -> None:
    parser.description = "Inspect and manage scoped local domain resolution"
    parser.add_argument("action", nargs="?", choices=DOMAIN_ACTIONS, default="list")
    parser.add_argument("tld", nargs="?", help="legacy setup suffix")
    parser.add_argument("--project-dir", default=None)
    parser.add_argument("--label", default="default")
    parser.add_argument("--resolver", default=None)
    parser.add_argument("--json", action="store_true")


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, sort_keys=True))
        return
    print(f"domains {payload.get('operation', 'status')}: {payload.get('state', 'unknown')}")
    if payload.get("hostname"):
        print(f"  hostname: {payload['hostname']}")
    resolver = payload.get("resolver") or {}
    if resolver:
        print(f"  resolver: {resolver.get('owner', resolver.get('adapter_id', 'unknown'))} "
              f"({resolver.get('tier', resolver.get('support_tier', 'unknown'))})")
    reason = payload.get("reason") or {}
    if reason:
        print(f"  {reason.get('code', 'notice')}: {reason.get('message', '')}")
    if payload.get("fallback_url"):
        print(f"  fallback: {payload['fallback_url']}")


def _support() -> dict:
    return {
        "ok": True,
        "operation": "support",
        "state": "ready",
        "adapters": [{
            "adapter_id": item.adapter_id,
            "managers": list(item.managers),
            "platforms": list(item.platforms),
            "support_tier": item.support_tier,
            "capabilities": sorted(item.capabilities),
            "evidence_id": item.evidence_id,
            "adoptable": item.adoptable,
        } for item in BUILTIN_RESOLVER_ADAPTERS],
        "mutated": False,
    }


def cmd_domains(cfg, args) -> None:
    if args.action in LEGACY_ACTIONS:
        from sandbox.commands.net import cmd_domains as legacy_domains
        legacy_domains(cfg, args)
        return
    if args.action == "support":
        _emit(_support(), bool(args.json))
        return

    if args.action == "ingress":
        from sandbox.application.context import ingress_service
        subaction = args.tld or "status"
        if subaction not in {"detect", "support", "status", "plan"}:
            _emit({"ok": False, "operation": f"ingress_{subaction}",
                   "state": "unsupported", "mutated": False,
                   "reason": {"code": "ingress_action_not_implemented",
                              "message": "This ingress mutation action is not implemented yet."}},
                  bool(args.json))
            return
        service = ingress_service(cfg)
        if subaction == "support":
            payload = service.support()
        elif subaction in {"detect", "status"}:
            payload = service.detect()
            payload["operation"] = f"ingress_{subaction}"
        else:
            selection = service.select(required_protocols=("http", "https"))
            payload = {
                "ok": selection.adapter_id is not None,
                "operation": "ingress_plan",
                "state": "ready" if selection.adapter_id else "fallback",
                "ingress": selection.adapter_id,
                "accepted_addresses": list(selection.accepted_addresses),
                "reason": {"code": selection.reason_code,
                           "message": selection.reason_code.replace("_", " ")},
                "mutated": False,
            }
        _emit(payload, bool(args.json))
        return

    from sandbox.application.context import domain_service

    service = domain_service(cfg)
    project_dir = args.project_dir or "."
    if args.action in {"detect", "status"}:
        payload = service.status(project_dir, label=args.label)
    elif args.action == "plan":
        payload = service.plan(project_dir, label=args.label)
    elif args.action == "apply":
        payload = service.apply(
            project_dir, label=args.label, interactive=sys.stdin.isatty(),
        )
    elif args.action == "cleanup":
        payload = service.cleanup(
            project_dir, label=args.label, interactive=sys.stdin.isatty(),
        )
    else:
        payload = service.reconsider(args.resolver)
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    payload.setdefault("operation", args.action)
    _emit(payload, bool(args.json))
    if not payload.get("ok") and payload.get("state") not in {
        "fallback", "pending_consent", "pending_privilege", "unsupported",
        "incompatible_identity",
    }:
        raise SystemExit(1)


register_specs((CommandSpec(
    name="domains", handler=cmd_domains, configure=configure_parser,
    owner=__name__, order=55, scope="project", destructive=True,
),))
