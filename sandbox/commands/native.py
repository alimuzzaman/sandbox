"""Managed/incumbent native runtime inspection and explicit install entrypoint."""

from __future__ import annotations

import json
import sys

from sandbox.registry import CommandSpec, register_specs


ACTIONS = ("support", "preflight", "baseline", "install-plan", "install", "status", "cleanup",
           "credential-status", "credential-bind", "credential-request",
           "credential-revoke")

# The public acceptance seam (spec 045, T037).  These actions accept only an
# opaque source reference and exact non-secret binding/request/revoke metadata.
# There is deliberately no option, prompt, file, or environment path that can
# carry a plaintext credential, and every action is refused while the live
# proof gate is open.
CREDENTIAL_ACCEPTANCE_ACTIONS = ("credential-bind", "credential-request",
                                 "credential-revoke")


def configure_parser(parser):
    parser.description = "Inspect and manage explicitly selected native runtimes"
    parser.add_argument("action", nargs="?", choices=ACTIONS, default="support")
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--label", default="default")
    parser.add_argument("--web-server", choices=("nginx", "apache"), default="nginx")
    parser.add_argument("--json", action="store_true")
    # Credential acceptance metadata.  Every value below is non-secret: an
    # opaque approved source reference, identities, and the exact request
    # scope.  No credential value is ever accepted here.
    parser.add_argument("--source-ref")
    parser.add_argument("--binding-id")
    parser.add_argument("--binding-version", type=int)
    parser.add_argument("--instance-id", dest="instance_id")
    parser.add_argument("--host")
    parser.add_argument("--path")
    parser.add_argument("--method")
    parser.add_argument("--auth-form")
    parser.add_argument("--expires-at")


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
    from sandbox.application.runtime_service import project_php_extension_status
    from sandbox.runtimes.base import OperationError, OperationRequest

    result = runtime_service(cfg).invoke(OperationRequest(
        args.project_dir, operation, label=args.label,
    ))
    if isinstance(result, OperationError):
        return {"ok": False, "operation": f"native_{operation}", "state": "blocked",
                "mutated": False, "reason": {"code": result.code,
                                                "message": result.message}}
    data = dict(result.data)
    if operation == "status" and isinstance(data.get("php_extensions"), dict):
        report = project_php_extension_status(data["php_extensions"])
        if isinstance(report, dict):
            data["php_extensions"] = report
            if not report.get("ok", False):
                data.update({"ok": False, "state": "blocked", "exit_code": 1,
                             "mutated": False})
            else:
                data.setdefault("exit_code", 0)
    return {"ok": result.ok, "operation": f"native_{operation}", **data}


def support():
    from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS
    return {"ok": True, "operation": "native_support", "state": "ready",
            "runtimes": [dict(value) for value in RUNTIME_DECLARATIONS], "mutated": False}


def _capability_declaration():
    from sandbox.isolation.manifest import MANAGED_ISOLATION_CAPABILITIES

    return next(item for item in MANAGED_ISOLATION_CAPABILITIES
                if item["capability_id"] == "outbound_credential_mediation")


def _credential_metadata(action, args):
    """Canonicalize only exact, non-secret acceptance metadata.

    A rejected value is never echoed back: the caller gets a stable reason
    code.  The opaque source reference is reduced to a digest so that even an
    approved reference identity does not have to appear in retained output.
    """
    from hashlib import sha256

    from sandbox.isolation.credential_binding import (
        ALLOWED_AUTH_FORMS, ALLOWED_METHODS, canonical_host, canonical_path,
        canonical_source_reference, canonical_timestamp,
    )

    def identity(value, label):
        import re as _re
        if not isinstance(value, str) or not _re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
            raise ValueError(label)
        return value

    def method(value):
        if not isinstance(value, str) or value.upper() not in ALLOWED_METHODS:
            raise ValueError("method")
        return value.upper()

    def version(value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("binding version")
        return value

    if action == "credential-bind":
        reference = canonical_source_reference(args.source_ref)
        if args.auth_form not in ALLOWED_AUTH_FORMS:
            raise ValueError("auth form")
        return {
            "binding_id": identity(args.binding_id, "binding id"),
            "instance_id": identity(args.instance_id, "instance id"),
            "credential_reference_digest": sha256(reference.encode()).hexdigest(),
            "scheme": "https", "host": canonical_host(args.host), "port": 443,
            "method": method(args.method), "path": canonical_path(args.path),
            "auth_form": args.auth_form,
            "expires_at": canonical_timestamp(args.expires_at),
        }
    if action == "credential-request":
        return {
            "binding_id": identity(args.binding_id, "binding id"),
            "binding_version": version(args.binding_version),
            "method": method(args.method), "path": canonical_path(args.path),
        }
    return {
        "binding_id": identity(args.binding_id, "binding id"),
        "binding_version": version(args.binding_version),
    }


def credential_acceptance(action, args):
    """Accept exact non-secret metadata, then refuse without live evidence.

    This is the public seam the authorized live matrix (T029) will drive.  It
    stays fail-closed here: the capability is `implemented_unproven`, not
    adoptable, and carries no evidence identity, so every acceptance action is
    a bounded refusal that mutates nothing.
    """
    from sandbox.isolation.capability_report import (
        CapabilityPrerequisite, CapabilityReport,
    )

    operation = f"native_{action.replace('-', '_')}"
    try:
        accepted = _credential_metadata(action, args)
    except (TypeError, ValueError):
        return {"ok": False, "operation": operation, "state": "blocked",
                "mutated": False, "accepted": {},
                "reason": {"code": "credential_metadata_invalid",
                           "message": "acceptance metadata is missing or not exact"}}
    declaration = _capability_declaration()
    report = CapabilityReport(
        support_tier=declaration["support_tier"],
        adoptable=bool(declaration["adoptable"]),
        evidence_id=declaration.get("evidence_id"),
        prerequisites=(CapabilityPrerequisite(
            "managed-native-live-proof", "unknown", "evidence_missing",
        ),),
    )
    if report.admissible:
        # Unreachable while the gate is open, and deliberately still a refusal:
        # promoting the tier is T031's decision, not this command's.
        return {"ok": False, "operation": operation, "state": "blocked",
                "mutated": False, "accepted": accepted,
                "support_tier": report.support_tier, "adoptable": report.adoptable,
                "evidence_id": report.evidence_id,
                "refusal_reasons": ["acceptance_review_required"],
                "reason": {"code": "credential_acceptance_unreviewed"}}
    return {"ok": False, "operation": operation, "state": "blocked", "mutated": False,
            "accepted": accepted, "support_tier": report.support_tier,
            "adoptable": report.adoptable, "evidence_id": report.evidence_id,
            "refusal_reasons": list(report.derived_refusals),
            "reason": {"code": "credential_acceptance_unproven",
                       "message": "live isolation and credential evidence is missing"}}


def credential_status(*, repository=None):
    """Return a secret-free, fail-closed Credential Vault capability report."""
    from sandbox.isolation.capability_report import (
        BindingState, CapabilityPrerequisite, CapabilityReport,
    )
    declaration = _capability_declaration()
    bindings = ()
    status_error = None
    if repository is not None:
        try:
            bindings = tuple(
                BindingState(
                    binding.binding_id, binding.version, binding.scope(),
                    binding.state, binding.expires_at,
                )
                for binding in repository.list()
            )
        except Exception:
            # A status surface must not turn corrupt/foreign state into a
            # success claim or echo lower-layer diagnostics.
            bindings = ()
            status_error = "binding_status_unavailable"
    report = CapabilityReport(
        support_tier=declaration["support_tier"],
        adoptable=bool(declaration["adoptable"]),
        evidence_id=declaration.get("evidence_id"),
        prerequisites=(CapabilityPrerequisite(
            "managed-native-live-proof", "unknown", "evidence_missing",
        ),),
        binding_states=bindings,
        refusal_reasons=((status_error,) if status_error else ()),
    )
    return {
        "ok": report.admissible,
        "operation": "native_credential_status",
        "state": "ready" if report.admissible else "blocked",
        "mutated": False,
        **report.to_dict(),
    }


def _read_credential_repository():
    """Open existing native state for status only; never create/migrate it."""
    try:
        from sandbox.application.context import managed_native_credential_repository
        return managed_native_credential_repository()
    except Exception:
        # Status must remain a bounded refusal when state-path composition or
        # a lower repository adapter is unavailable.
        return None


def _predispatch(args):
    return getattr(args, "action", None) in (
        "credential-status", *CREDENTIAL_ACCEPTANCE_ACTIONS)


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
    elif args.action == "credential-status":
        result = credential_status(repository=_read_credential_repository())
    elif args.action in CREDENTIAL_ACCEPTANCE_ACTIONS:
        result = credential_acceptance(args.action, args)
    elif args.action == "cleanup":
        result = _runtime_result(cfg, args, "destroy")
    else:
        result = {"ok": False, "operation": f"native_{args.action.replace('-', '_')}",
                  "state": "unsupported", "mutated": False,
                  "reason": {"code": "native_action_not_implemented",
                             "message": "This mutation remains disabled until its isolation proof is complete."}}
    _emit(result, bool(args.json))
    if not result.get("ok"):
        # A bounded status report can carry a concrete exit code.  Preserve it
        # for shell/CI callers even when the operation is deliberately
        # fail-closed (for example, an incumbent PHP-extension probe).
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            raise SystemExit(exit_code)
        if result.get("state") not in {"blocked", "unsupported", "pending_confirmation"}:
            raise SystemExit(1)


register_specs((CommandSpec(
    name="native", handler=cmd_native, configure=configure_parser,
    owner=__name__, order=56, scope="project", destructive=True,
    predispatch_policy=_predispatch,
),))
