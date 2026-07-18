"""Runtime-neutral instance operations.

This group is deliberately separate from the WordPress tool group.  It uses
the same application runtime service as the CLI, so generic Compose projects
never need to pass through WP-CLI, WP REST, or the WordPress container model.
"""

from __future__ import annotations

from dependencies import ToolDependencies


_core = None
_project_instance = None
_runtime_service = None


def register(server, dependencies: ToolDependencies) -> None:
    global _core, _project_instance, _runtime_service
    _core = dependencies.require("core")
    _project_instance = dependencies.require("project_instance")
    _runtime_service = dependencies.require("runtime_service")
    for tool in (instance_status, instance_logs, instance_exec):
        server.tool()(tool)


def _typed_invoke(project_dir: str, label: str | None, operation: str, arguments=None) -> dict:
    from sandbox.runtimes.base import OperationRequest

    instance, error = _project_instance(project_dir, label)
    if error:
        return error
    owner = _core().registry_find_instance(instance)
    if not owner or not owner.get("root"):
        return {"ok": False, "error": f"instance '{instance}' has no project owner"}
    result = _runtime_service().invoke(OperationRequest(
        project_root=owner["root"], operation=operation,
        label=owner.get("label", "default"), arguments=arguments or {},
    ))
    if hasattr(result, "message") and hasattr(result, "code"):
        return {"ok": False, "code": result.code, "error": result.message,
                "project_kind": result.project_kind,
                "available_capabilities": list(result.available_capabilities)}
    return {"ok": bool(result.ok), "operation": result.operation,
            **dict(result.data)}


def instance_status(project_dir: str, label: str | None = None) -> dict:
    """Return runtime-neutral status for a project instance."""
    return _typed_invoke(project_dir, label, "status")


def instance_logs(project_dir: str, label: str | None = None) -> dict:
    """Return bounded logs for the declared public service."""
    return _typed_invoke(project_dir, label, "logs")


def instance_exec(command: list[str], project_dir: str,
                 label: str | None = None, local: bool = False,
                 remote: str | None = None, workspace: str | None = None,
                 timeout_seconds: int | None = None,
                 output_profile: str = "smart") -> dict:
    """Execute an argv list in the declared public service.

    Shell text is intentionally not accepted; callers that need a shell must
    explicitly pass ``["sh", "-lc", ...]`` and therefore make that boundary
    visible to policy and audit logs.
    """
    if not command or any(not isinstance(item, str) or not item for item in command):
        return {"ok": False, "code": "invalid_command",
                "error": "command must be a non-empty argv list"}
    durable = bool(local or remote or workspace or timeout_seconds is not None)
    if not durable:
        # A project-level remote-first policy must route instance execution to
        # the durable remote controller even when the MCP caller omits target
        # options. Local projects retain the historical direct invocation.
        try:
            from sandbox.application.context import durable_job_dependencies
            from sandbox.jobs.models import TargetRequest
            target = durable_job_dependencies()["target_service"].resolve(
                TargetRequest(project_dir=project_dir, required_capability="job.exec"))
            durable = target.kind == "remote"
        except Exception:
            durable = False
    if durable:
        from tools.jobs import _submit_explicit_job
        job = _submit_explicit_job(command, project_dir, local=local, remote=remote, workspace=workspace,
                                   timeout_seconds=timeout_seconds or 900,
                                   output_profile=output_profile, kind="runtime-exec")
        return {"ok": bool(job.get("ok")), "operation": "exec", **job}
    return _typed_invoke(project_dir, label, "exec", {"argv": command})
