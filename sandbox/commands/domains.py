"""Project-scoped domain operations with staged legacy compatibility."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The default clean-URL provider is owned by the composed application seam
# (specs 037 FR-007/FR-031, 038 FR-029/FR-030); imported so the CLI and the core
# facade cannot drift apart on what "default" means.
from sandbox.application.clean_url_provider import DEFAULT_PROVIDER
from sandbox.network.manifest import BUILTIN_RESOLVER_ADAPTERS
from sandbox.registry import CommandSpec, register_specs


LEGACY_ACTIONS = frozenset({"setup", "up", "down", "teardown", "repair-ca", "list"})
DOMAIN_ACTIONS = (
    "setup", "up", "down", "teardown", "repair-ca", "list",
    "detect", "support", "plan", "apply", "status", "cleanup", "reconsider", "ingress",
    "use",
)
INGRESS_ACTIONS = frozenset({
    "detect", "support", "status", "plan", "apply", "cleanup", "reconcile", "reconsider",
})


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


def _annotate_default_ingress_failure(payload: dict) -> dict:
    """Add bounded published-listener evidence to a non-ready status.

    Resolver status and proxy listener status are separate observations. A
    running container or a present loopback alias is not proof that the
    published endpoint accepts connections, so expose the existing read-only
    listener check when a project status is already non-ready. Keep ready
    resolver results untouched and fail closed if the diagnostic is unavailable.
    """
    if not isinstance(payload, dict) or payload.get("state") == "ready":
        return payload
    try:
        from sandbox.core import _domains
        listener = _domains._published_listener_check()
    except Exception:
        return payload
    if not isinstance(listener, dict) or listener.get("ok"):
        return payload
    label = str(listener.get("label") or "published ingress listener is unreachable")
    hint = str(listener.get("hint") or "run `./sb domains up` to restore the proxy")
    payload = dict(payload)
    payload["ingress"] = {"state": "unreachable"}
    payload["application"] = {"state": "not_attempted"}
    payload["health"] = "degraded"
    payload["reason"] = {
        "code": "ingress_listener_unreachable",
        "message": f"{label}; {hint}",
    }
    return payload


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


def _ingress_plan(service, args) -> dict:
    selection = service.select(required_protocols=("http", "https"))
    return {
        "ok": selection.adapter_id is not None,
        "operation": "ingress_plan",
        "state": "ready" if selection.adapter_id else "fallback",
        "ingress": selection.adapter_id,
        "pin": selection.pin,
        "pin_source": selection.pin_source,
        "accepted_addresses": list(selection.accepted_addresses),
        "reason": {"code": selection.reason_code,
                   "message": selection.reason_code.replace("_", " ")},
        "mutated": False,
    }


def _ingress_owner(args) -> str:
    return f"{Path(args.project_dir or '.').expanduser().resolve()}::{args.label}"


def _use_provider(args) -> dict:
    """Switch the clean-URL provider on demand (037 FR-032, 038 FR-031).

    Writes the machine-local `domains.ingress` selection; no reprovisioning, no
    hostname change. `./sb domains use` with no value reports the current one.
    """
    from sandbox.core import _domains as core_domains
    from sandbox.core._config import _local_yaml, _write_local_yaml

    def effective() -> str:
        """The provider this project would use, project layer included.

        The project pin is read through the composed domain service, which owns
        project-config loading; the CLI never reaches for the compatibility
        facade itself (module boundaries).
        """
        project = {}
        try:
            from sandbox.application.context import domain_service

            policy = domain_service(None).ingress_policy(
                args.project_dir or ".", label=args.label)
            if policy.get("pin"):
                project = {"domains": {"ingress": policy["pin"]}}
        except Exception:
            project = {}
        return core_domains.clean_url_mode(project)

    requested = (args.tld or args.resolver or "").strip().lower()
    if not requested:
        return {"ok": True, "operation": "domains_use", "state": "ready",
                "mutated": False, "provider": effective(),
                "default": DEFAULT_PROVIDER,
                "reason": {"code": "provider_reported",
                           "message": "pass a provider id to switch, e.g. "
                                      f"`./sb domains use {DEFAULT_PROVIDER}`"}}
    known = {DEFAULT_PROVIDER, "default", "disabled"} | {
        item.adapter_id for item in BUILTIN_RESOLVER_ADAPTERS}
    try:
        from sandbox.ingress.manifest import BUILTIN_INGRESS
        known |= {item.adapter_id for item in BUILTIN_INGRESS}
    except ImportError:
        pass
    if requested not in known:
        return {"ok": False, "operation": "domains_use", "state": "invalid",
                "mutated": False, "provider": effective(),
                "reason": {"code": "unknown_provider",
                           "message": f"unknown provider {requested!r}; known: "
                                      + ", ".join(sorted(known))}}
    local = _local_yaml()
    block = local.setdefault("domains", {})
    if requested in (DEFAULT_PROVIDER, "default"):
        block.pop("ingress", None)
        if not block:
            local.pop("domains", None)
    else:
        block["ingress"] = requested
    _write_local_yaml(local)
    return {"ok": True, "operation": "domains_use", "state": "ready",
            "mutated": True, "provider": effective(),
            "default": DEFAULT_PROVIDER,
            "reason": {"code": "provider_selected",
                       "message": "run `./sb domains setup` (or `./sb ensure`) "
                                  "to apply the selection"}}


def cmd_domains(cfg, args) -> None:
    if args.action in LEGACY_ACTIONS:
        from sandbox.commands.net import cmd_domains as legacy_domains
        legacy_domains(cfg, args)
        return
    if args.action == "support":
        _emit(_support(), bool(args.json))
        return

    if args.action == "use":
        _emit(_use_provider(args), bool(args.json))
        return

    if args.action == "ingress":
        from sandbox.application.context import domain_service, ingress_service
        subaction = args.tld or "status"
        if subaction not in INGRESS_ACTIONS:
            _emit({"ok": False, "operation": f"ingress_{subaction}",
                   "state": "unsupported", "mutated": False,
                   "reason": {"code": "ingress_action_not_implemented",
                              "message": "This ingress mutation action is not implemented yet."}},
                  bool(args.json))
            return
        if subaction == "status":
            route_context = domain_service(cfg).route_context(
                args.project_dir or ".", label=args.label,
            )
            service = ingress_service(cfg, caddy_health_context=route_context)
        else:
            service = ingress_service(cfg)
        if subaction == "support":
            payload = service.support()
        elif subaction in {"detect", "status"}:
            payload = service.detect()
            payload["operation"] = f"ingress_{subaction}"
        elif subaction == "plan":
            payload = _ingress_plan(service, args)
        elif subaction == "cleanup":
            payload = service.cleanup_owner(_ingress_owner(args))
            payload["operation"] = "ingress_cleanup"
        elif subaction == "reconcile":
            payload = service.reconcile_owner(_ingress_owner(args))
        elif subaction == "reconsider":
            if not args.resolver:
                payload = {"ok": False, "operation": "ingress_reconsider",
                           "state": "invalid", "mutated": False,
                           "reason": {"code": "consent_identity_required"}}
            else:
                payload = service.reconsider(args.resolver)
                payload["operation"] = "ingress_reconsider"
        else:
            # Route activation is deliberately composed through the A→B→A
            # compatibility handoff, not this low-level transport.  Returning a
            # typed no-op keeps noninteractive CLI automation safe until it can
            # supply B's verified naming result.
            payload = {"ok": False, "operation": "ingress_apply",
                       "state": "requires_domain_handoff", "mutated": False,
                       "reason": {"code": "verified_naming_required"}}
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
    if args.action == "status":
        payload = _annotate_default_ingress_failure(payload)
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
