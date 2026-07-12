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


def wordpress_runtime_service(cfg):
    """Compose current WordPress behavior behind the runtime contract."""
    import sandbox.core as core
    import sandbox_core as sc

    from sandbox.application.runtime_service import RuntimeService
    from sandbox.runtimes.registry import wordpress_registry

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

    adapters = wordpress_registry(
        {"ensure": ensure, "apply": apply, "status": status},
        capabilities={
            "wordpress.cli", "wordpress.exec", "wordpress.rest",
            "wordpress.snapshot", "wordpress.restore", "wordpress.reset",
            "wordpress.database", "wordpress.files", "wordpress.mail",
            "wordpress.remote-deploy", "wordpress.remote-preview",
        },
    )
    return RuntimeService(resolve_descriptor=resolve_descriptor, adapters=adapters)


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
    return wordpress_runtime_service(cfg).check(
        owner["root"], capability, label=owner.get("label", "default")
    )


def preflight_project_capability(cfg, project_root: str, capability: str, *, label: str = "default"):
    """Validate a project-scoped operation before any remote or local mutation."""
    return wordpress_runtime_service(cfg).check(project_root, capability, label=label)
