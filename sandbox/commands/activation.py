"""Host-local request activation authority."""

from __future__ import annotations

from sandbox.registry import CommandSpec, register_specs


def configure_parser(parser) -> None:
    parser.add_argument("action", choices=("serve",))
    parser.add_argument("--port", type=int, default=8766)


def cmd_activation(cfg, args) -> None:
    import sandbox_core as sc
    from sandbox.activation.catalog import build_catalog
    from sandbox.activation.http import ActivationHTTPApplication
    from sandbox.activation.server import serve
    from sandbox.activation.service import ActivationService
    from sandbox.application.context import runtime_service, wordpress_runtime_service
    from sandbox.core import resolve_instances
    from sandbox.runtimes.base import OperationRequest, OperationResult

    catalog = build_catalog(sc.registry_all(), resolve_instances(cfg))
    generic = runtime_service(cfg)
    wordpress = wordpress_runtime_service(cfg)

    def resume(route, _timeout: int) -> bool:
        selected = wordpress if route.kind == "wordpress" else generic
        result = selected.invoke(OperationRequest(
            route.project_root, "resume", label=route.label,
        ))
        return isinstance(result, OperationResult) and result.ok

    application = ActivationHTTPApplication(catalog, ActivationService(), resume)
    serve(application, port=args.port)


register_specs((CommandSpec(
    "activation", cmd_activation, configure=configure_parser, owner=__name__,
    scope="global", help="Serve the loopback request-wake authority",
    predispatch_policy=lambda _args: True,
),))
