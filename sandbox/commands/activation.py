"""Host-local request activation authority."""

from __future__ import annotations

from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.add_argument("action", choices=("serve", "scan", "status", "install", "enable", "disable"))
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable status/scan output")


def cmd_activation(cfg, args) -> None:
    if args.action in {"status", "install", "enable", "disable"}:
        import json
        from sandbox.activation import supervision
        result = getattr(supervision, args.action)()
        print(json.dumps(result, sort_keys=True))
        return
    # Keep the activation command on the compatibility facade owner.  Direct
    # imports of the legacy module make the command an untracked consumer and
    # let architecture-boundary drift accumulate; `_core()` returns the same
    # owner used by the other command modules without changing behavior.
    from sandbox.core import _core
    sc = _core()
    from sandbox.activation.catalog import ActivationCatalogError, build_catalog
    from sandbox.activation.http import ActivationHTTPApplication
    from sandbox.activation.server import serve
    from sandbox.activation.scheduler import ActivationScheduler, TcpActivityObserver
    from sandbox.activation.service import ActivationService
    from sandbox.application.context import runtime_service, wordpress_runtime_service
    from sandbox.core import resolve_instances
    from sandbox.core._config import load_config
    from sandbox.runtimes.base import OperationRequest, OperationResult

    try:
        catalog = build_catalog(sc.registry_all(), resolve_instances(cfg))
    except ActivationCatalogError as exc:
        # Catalog construction is deliberately fail-closed: an invalid opted-in
        # route must never be silently skipped or reach the scheduler.  Keep
        # diagnostics machine-readable so a malformed legacy row is actionable
        # without exposing a traceback or starting the authority.
        import json
        payload = {
            "ok": False,
            "action": args.action,
            "error": {
                "code": "activation_catalog_invalid",
                "message": str(exc)[:240] or "activation catalog is invalid",
            },
        }
        if args.action == "scan":
            payload["results"] = []
        print(json.dumps(payload, sort_keys=True))
        raise SystemExit(2)
    def current_catalog():
        """Read the registry/config on demand for the long-lived authority."""
        return build_catalog(sc.registry_all(), resolve_instances(load_config()))

    generic = runtime_service(cfg)
    wordpress = wordpress_runtime_service(cfg)

    def invoke(route, operation: str, timeout: int) -> bool:
        selected = wordpress if route.kind == "wordpress" else generic
        result = selected.invoke(OperationRequest(
            route.project_root, operation, label=route.label,
            arguments={"timeout": timeout},
        ))
        return isinstance(result, OperationResult) and result.ok

    def resume(route, timeout: int) -> bool:
        return invoke(route, "resume", timeout)

    service = ActivationService()
    application = ActivationHTTPApplication(catalog, service, resume,
                                             catalog_provider=current_catalog)
    tcp = TcpActivityObserver()
    try:
        from sandbox.application.context import durable_job_dependencies
        job_repository = durable_job_dependencies()["job_service"].repository
    except Exception:
        job_repository = None

    def observe(route) -> str:
        if route.kind == "wordpress":
            if not route.instance:
                return "unknown"
            try:
                return "ready" if sc._instance_running(route.instance) else "asleep"
            except Exception:
                return "unknown"
        try:
            result = generic.invoke(OperationRequest(route.project_root, "status", label=route.label))
            if not isinstance(result, OperationResult) or not result.ok:
                return "unknown"
            return "ready" if result.data.get("status") == "ready" else "asleep" if result.data.get("status") == "stopped" else "unknown"
        except Exception:
            return "unknown"

    def activity_safe(route) -> tuple[bool, str]:
        from sandbox.activation.leases import has_active_instance_lease
        if route.instance and has_active_instance_lease(route.instance):
            return False, "instance_activity_lease"
        connected = tcp(route.backend_port)
        if connected is None:
            return False, "tcp_evidence_unavailable"
        if connected:
            return False, "active_http_or_websocket"
        if job_repository is None:
            return False, "job_evidence_unavailable"
        try:
            jobs = job_repository.list(limit=200, active_only=True)
        except Exception:
            return False, "job_evidence_unavailable"
        if any(str(job.get("project_root")) == route.project_root for job in jobs):
            return False, "active_job"
        return True, "idle"

    scheduler = ActivationScheduler(
        catalog, service, observe_state=observe, activity_safe=activity_safe,
        suspend=lambda route, timeout: invoke(route, "suspend", timeout),
    )
    scheduler.reconcile()
    if args.action == "scan":
        import json
        payload = {"ok": True, "results": [result.__dict__ for result in
                         scheduler.scan(dry_run=bool(args.dry_run))]}
        if catalog.issues():
            payload["warnings"] = list(catalog.issues())
        print(json.dumps(payload, sort_keys=True))
        return
    import threading
    threading.Thread(target=scheduler.run, kwargs={"interval_seconds": args.interval,
                                                    "catalog_provider": current_catalog},
                     daemon=True, name="sandbox-activation-scheduler").start()
    serve(application, port=args.port)


register_specs((CommandSpec(
    "activation", cmd_activation, configure=configure_parser, owner=__name__,
    scope="global", help="Control the loopback request-wake and idle-stop authority",
    predispatch_policy=lambda _args: True,
),))
