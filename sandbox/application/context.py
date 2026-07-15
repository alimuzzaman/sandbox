from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sandbox.runtimes.base import OperationRequest


@dataclass(frozen=True)
class ApplicationDependencies:
    """Concrete dependencies are assembled at transport composition roots."""

    registry: Any
    process: Any
    http: Any
    ports: Any
    paths: Any
    proxy: Any
    clock: Any | None = None


def runtime_neutral_dependencies(
    *, registry: Any, allowed_roots, proxy: Any, process: Any | None = None,
    http: Any | None = None, ports: Any | None = None, paths: Any | None = None,
    clock: Any | None = None,
) -> ApplicationDependencies:
    """Build generic mechanisms; callers retain runtime-specific proxy policy."""
    from sandbox.services import (
        AllowedRootPathPolicy, BoundedProcessRunner, SocketPortAllocator,
        UrlHttpProbe,
    )

    return ApplicationDependencies(
        registry=registry,
        process=process or BoundedProcessRunner(),
        http=http or UrlHttpProbe(),
        ports=ports or SocketPortAllocator(),
        paths=paths or AllowedRootPathPolicy(allowed_roots),
        proxy=proxy,
        clock=clock,
    )


def wordpress_proxy_facade(cfg, *, core=None):
    """Adapt declared WordPress routes to the existing aggregate Caddy owner.

    Route policy and host mutations remain in ``sandbox.core``. This facade
    validates exact route identity and delegates apply/remove operations.
    Removal is allowed only after the owning config entry has been removed,
    matching the existing instance-delete ordering.
    """
    if core is None:
        import sandbox.core as core

    from sandbox.services import CallbackProxyManager

    def declared(config, hostname, port=None):
        for instance in core.resolve_instances(config).values():
            if instance.get("domain") != hostname:
                continue
            if port is None or int(instance.get("wordpress_port", 0)) == int(port):
                return True
        return False

    def validate(plan):
        if not declared(cfg, str(plan["hostname"]), int(plan["port"])):
            raise ValueError("proxy plan does not match a declared WordPress route")

    def apply_route(_hostname, _port):
        core._ensure_proxy_up(cfg)

    def remove_route(hostname):
        current = core.load_config()
        if declared(current, hostname):
            raise ValueError(f"proxy route {hostname!r} is still declared")
        core.regen_caddyfile(current)
        if not core.reload_proxy():
            raise RuntimeError(f"failed to reload proxy after removing {hostname!r}")

    return CallbackProxyManager(
        apply_route=apply_route,
        remove_route=remove_route,
        validate_plan=validate,
    )


def wordpress_runtime_dependencies(cfg, *, core=None, registry=None, **overrides):
    """Compose bounded mechanisms with the existing WordPress proxy facade."""
    if core is None:
        import sandbox.core as core
    if registry is None:
        import sandbox_core as sc
        registry = sc
    allowed_roots = overrides.pop("allowed_roots", (core.ROOT, core.BASE))
    return runtime_neutral_dependencies(
        registry=registry,
        allowed_roots=allowed_roots,
        proxy=overrides.pop("proxy", wordpress_proxy_facade(cfg, core=core)),
        **overrides,
    )


def runtime_service(cfg):
    """Compose WordPress compatibility and the framework-neutral Compose adapter."""
    import sandbox.core as core
    import sandbox_core as sc

    from sandbox.application.runtime_service import RuntimeService
    from sandbox.runtimes.compose import ComposeAdapter
    from sandbox.runtimes import builtin_adapter_registry

    def resolve_descriptor(root, label=None):
        return sc.load_project_config(root, label=label)

    def ensure(request: OperationRequest):
        return core.ensure_instance(
            cfg,
            request.project_root,
            label=request.label,
            create=bool(request.arguments.get("create", False)),
        )

    def apply(request: OperationRequest):
        return core.apply_config(cfg, request.project_root, label=request.label)

    def status(request: OperationRequest):
        entry = sc.registry_get(request.project_root, label=request.label)
        return {"ok": entry is not None, **(entry or {})}

    dependencies = runtime_neutral_dependencies(
        registry=sc, allowed_roots=(core.ROOT, core.BASE),
        proxy=wordpress_proxy_facade(cfg, core=core),
    )
    compose = ComposeAdapter(dependencies, sc)
    adapters = builtin_adapter_registry(
        {"ensure": ensure, "apply": apply, "status": status}, compose=compose,
    )
    adapters.for_kind("wordpress").adapter.capabilities = frozenset({
            *adapters.for_kind("wordpress").adapter.capabilities,
            "wordpress.cli", "wordpress.exec", "wordpress.rest",
            "wordpress.snapshot", "wordpress.restore", "wordpress.reset",
            "wordpress.database", "wordpress.files", "wordpress.mail",
            "wordpress.abilities",
            "wordpress.remote-deploy", "wordpress.remote-preview",
        })
    return RuntimeService(resolve_descriptor=resolve_descriptor, adapters=adapters)


def wordpress_runtime_service(cfg):
    """Backward-compatible name for the shared runtime composition root."""
    return runtime_service(cfg)


def preflight_instance_capability(cfg, instance: str, capability: str):
    import sandbox_core as sc

    from sandbox.runtimes.base import OperationError

    owner = sc.registry_find_instance(instance)
    if not owner or not owner.get("root"):
        return OperationError(
            code="unknown_instance_owner",
            message=f"instance {instance!r} has no registered project owner",
            requested_capability=capability,
        )
    return runtime_service(cfg).check(
        owner["root"], capability, label=owner.get("label", "default")
    )


def preflight_project_capability(cfg, project_root: str, capability: str, *, label: str = "default"):
    """Validate a project-scoped operation before any remote or local mutation."""
    return runtime_service(cfg).check(project_root, capability, label=label)
