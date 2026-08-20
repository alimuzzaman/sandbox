"""Runtime-neutral instance operations.

This group is deliberately separate from the WordPress tool group.  It uses
the same application runtime service as the CLI, so generic Compose projects
never need to pass through WP-CLI, WP REST, or the WordPress container model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from dependencies import ToolDependencies


_core = None
_project_instance = None
_runtime_service = None
_native_preflight = None
_managed_package_planner = None


_MCP_PHP_PLANES = ("web", "cli", "exec", "phpunit")
_MCP_PHP_STATES = frozenset({
    "ready", "blocked", "unavailable", "unknown", "drift", "error",
    "fresh", "stale", "missing", "discarded",
})
_MCP_PHP_ISSUE_CODES = frozenset({
    "missing", "version_mismatch", "version_unobservable",
    "unsupported_provisioning", "unsupported_disable", "plane_drift",
})
_MCP_PHP_ISSUE_MESSAGES = {
    "missing": "required PHP extension is missing",
    "version_mismatch": "PHP extension version does not match the requirement",
    "version_unobservable": "PHP extension version cannot be observed",
    "unsupported_provisioning": "PHP extension provisioning is unsupported",
    "unsupported_disable": "disabling this PHP extension is unsupported",
    "plane_drift": "PHP extension observations differ between execution planes",
}
_MCP_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@|*-]{0,127}$")
_MCP_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MCP_FORBIDDEN_TEXT = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:password|passphrase|secret|token|credential|"
    r"authorization|cookie|private(?:[_-]?key)?|bearer|basic|api[_-]?key)"
    r"(?![A-Za-z0-9])"
)


def register(server, dependencies: ToolDependencies) -> None:
    global _core, _project_instance, _runtime_service, _native_preflight, _managed_package_planner
    _core = dependencies.require("core")
    _project_instance = dependencies.require("project_instance")
    _runtime_service = dependencies.require("runtime_service")
    _native_preflight = dependencies.require("native_preflight")
    _managed_package_planner = dependencies.require("managed_package_planner")
    for tool in (instance_status, instance_logs, instance_exec,
                 native_support, native_preflight, native_install_plan):
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
                "available_capabilities": list(result.available_capabilities),
                **({"alternative": result.suggestion} if result.suggestion else {}),
                "mutated": False}
    payload = {"ok": bool(result.ok), "operation": result.operation,
               **dict(result.data)}
    # WordPress lifecycle status exposes the same PHP-extension cache receipt
    # as the CLI status path.  Keep this enrichment status-only and adapter-
    # specific: generic Compose remains runtime-neutral, while the canonical
    # core producer owns active-base resolution, probing, and redaction.
    if operation == "status":
        # Preserve an adapter-supplied canonical report (incumbent Herd/Valet)
        # and project it through the shared closed boundary. Only when no
        # adapter report exists do we use the legacy core producer below.
        adapter_report = payload.get("php_extensions")
        if isinstance(adapter_report, Mapping):
            projected = _public_php_extension_status(adapter_report)
            if isinstance(projected, dict):
                payload["php_extensions"] = projected
                payload["ok"] = bool(payload["ok"] and projected.get("ok", False))
                payload["state"] = "ready" if projected.get("ok", False) else "blocked"
                payload["mutated"] = False
                payload["exit_code"] = 0 if payload["ok"] else 1
        elif (getattr(result, "project_kind", None) == "wordpress"
              and not (isinstance(payload.get("runtime"), Mapping)
                       and payload["runtime"].get("mode") == "incumbent_native")):
            extension_status = _wordpress_extension_status(owner)
            if isinstance(extension_status, dict):
                payload["php_extensions"] = extension_status
                payload["ok"] = bool(payload["ok"] and extension_status.get("ok", True))
                if not payload["ok"]:
                    payload["state"] = "blocked"
                payload["exit_code"] = 0 if payload["ok"] else 1
    return payload


def _wordpress_extension_status(owner: dict) -> dict | None:
    """Return the canonical PHP-extension report for one WordPress owner.

    The MCP runtime group must not inspect registry/state files or duplicate
    cache/provenance parsing.  Resolve the instance through the existing core
    config facade (which uses the active ``SANDBOX_HOME``), then delegate to
    ``php_extension_status``—the same redacted producer used by CLI status.
    Missing/invalid legacy config is a compatibility absence, not permission
    to guess or expose raw state, so status simply retains its typed runtime
    result in that case.
    """
    if not isinstance(owner, dict) or owner.get("kind") == "compose":
        return None
    instance = owner.get("instance")
    if not isinstance(instance, str) or not instance:
        return None
    try:
        from sandbox.core import load_config, php_extension_status, resolve_instances

        instance_config = resolve_instances(load_config()).get(instance)
        if not isinstance(instance_config, dict):
            return None
        report = php_extension_status(instance_config, instance=instance)
    except Exception:
        # The runtime status contract is still useful when an older or partial
        # installation has no extension config.  Do not echo exception text:
        # it could contain a private path or an untrusted provider response.
        return None
    if not isinstance(report, dict):
        return None
    # The core producer already applies this boundary, but keep the MCP
    # transport fail-closed if a compatibility adapter returns an older or
    # untrusted shape.  Project the complete documented public report rather
    # than recursively redacting an open-ended receipt.  In particular,
    # unknown keys, paths, generated receipt content, and credentials are
    # discarded instead of crossing the MCP boundary.
    return _public_php_extension_status(report)


def _safe_php_value(value: object) -> str | None:
    if (not isinstance(value, str) or not _MCP_SAFE_VALUE.fullmatch(value)
            or _MCP_FORBIDDEN_TEXT.search(value)):
        return None
    return value


def _safe_php_digest(value: object) -> str | None:
    return value if isinstance(value, str) and _MCP_DIGEST.fullmatch(value) else None


def _safe_php_state(value: object) -> str | None:
    return value if isinstance(value, str) and value in _MCP_PHP_STATES else None


def _public_php_issue(value: object) -> dict | None:
    if not isinstance(value, Mapping):
        return None
    code = value.get("code")
    message = value.get("message")
    if (not isinstance(code, str) or code not in _MCP_PHP_ISSUE_CODES
            or message != _MCP_PHP_ISSUE_MESSAGES[code]):
        return None
    row = {"code": code, "message": message}
    for key in ("plane", "extension", "expected", "observed"):
        item = _safe_php_value(value.get(key))
        if item is not None:
            row[key] = item
    return row


def _public_php_issues(value: object) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    return [row for item in value if (row := _public_php_issue(item)) is not None]


def _public_php_requirements(value: object) -> list[dict]:
    if not isinstance(value, (list, tuple)):
        return []
    rows = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _safe_php_value(item.get("name"))
        state = item.get("state")
        version = item.get("version")
        if (name is None or not isinstance(state, str)
                or state not in {"enabled", "disabled"}):
            continue
        if version is not None and _safe_php_value(version) is None:
            continue
        rows.append({"name": name, "state": state, "version": version})
    return rows


def _public_php_extensions(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    rows = {}
    for name, item in value.items():
        safe_name = _safe_php_value(name)
        if safe_name is None or not isinstance(item, Mapping):
            continue
        enabled = item.get("enabled")
        version = item.get("version")
        if not isinstance(enabled, bool):
            continue
        if version is not None and _safe_php_value(version) is None:
            continue
        rows[safe_name] = {"enabled": enabled, "version": version}
    return rows


def _public_php_observed(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    observed = {}
    for plane in _MCP_PHP_PLANES:
        item = value.get(plane)
        if not isinstance(item, Mapping):
            continue
        row = {}
        state = _safe_php_state(item.get("state"))
        if state is not None:
            row["state"] = state
        for key in ("php_version", "sapi"):
            dimension = item.get(key)
            if dimension is None:
                row[key] = None
            else:
                safe_dimension = _safe_php_value(dimension)
                if safe_dimension is not None:
                    row[key] = safe_dimension
        row["extensions"] = _public_php_extensions(item.get("extensions"))
        row["issues"] = _public_php_issues(item.get("issues"))
        observed[plane] = row
    return observed


def _public_php_catalog(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    catalog = {}
    revision = value.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool):
        catalog["revision"] = revision
    digest = _safe_php_digest(value.get("digest"))
    if digest is not None:
        catalog["digest"] = digest
    return catalog


def _public_php_desired(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    desired = {}
    profile = value.get("profile")
    if profile is None:
        desired["profile"] = None
    else:
        safe_profile = _safe_php_value(profile)
        if safe_profile is not None:
            desired["profile"] = safe_profile
    desired["catalog"] = _public_php_catalog(value.get("catalog"))
    desired["requirements"] = _public_php_requirements(value.get("requirements"))
    for key in ("resolution_digest", "build_digest"):
        digest = _safe_php_digest(value.get(key))
        if digest is not None:
            desired[key] = digest
    return desired


def _public_php_provenance(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    provenance = {}
    state = _safe_php_state(value.get("state"))
    if state is not None:
        provenance["state"] = state
    digest = _safe_php_digest(value.get("recipe_catalog_digest"))
    if digest is not None:
        provenance["recipe_catalog_digest"] = digest
    parent_digests = value.get("parent_digests")
    if isinstance(parent_digests, Mapping):
        safe_parents = {}
        for role in ("web", "wpcli"):
            digest = _safe_php_digest(parent_digests.get(role))
            if digest is not None:
                safe_parents[role] = digest
        provenance["parent_digests"] = safe_parents
    recipe_ids = value.get("recipe_ids")
    if isinstance(recipe_ids, (list, tuple)):
        provenance["recipe_ids"] = [
            item for item in (_safe_php_value(raw) for raw in recipe_ids)
            if item is not None
        ]
    return provenance


def _public_php_status_map(value: object, keys: tuple[str, ...]) -> dict:
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key in keys:
        state = _safe_php_state(value.get(key))
        if state is not None:
            result[key] = state
    return result


def _public_php_staleness(value: object) -> dict:
    if not isinstance(value, Mapping):
        return {}
    result = {}
    state = value.get("state")
    if isinstance(state, str) and state in {"fresh", "stale"}:
        result["state"] = state
    reason = value.get("reason")
    if isinstance(reason, str) and reason in {
            "all_four_planes_observed", "one_or_more_planes_unavailable"}:
        result["reason"] = reason
    return result


def _public_php_extension_status(report: object) -> dict | None:
    """Project the documented, safe PHP-extension status contract for MCP."""
    from sandbox.application.runtime_service import project_php_extension_status

    return project_php_extension_status(report)


def instance_status(project_dir: str, label: str | None = None,
                    refresh: bool = False) -> dict:
    """Return runtime-neutral status for a project instance."""
    return _typed_invoke(project_dir, label, "status", {"refresh": bool(refresh)})


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


def native_support() -> dict:
    """List truthful local runtime/isolation proof tiers without mutation."""
    from sandbox.runtimes.manifest import RUNTIME_DECLARATIONS
    return {"ok": True, "operation": "native_support", "state": "ready",
            "runtimes": [dict(item) for item in RUNTIME_DECLARATIONS], "mutated": False}


def native_preflight() -> dict:
    """Run read-only effective managed-native prerequisite checks."""
    return _native_preflight().inspect()


def native_install_plan(web_server: str = "nginx") -> dict:
    """Preview exact signed-source package closure; never install or prompt."""
    try:
        plan = _managed_package_planner().plan(web_server=web_server)
    except (OSError, ValueError) as exc:
        return {"ok": False, "operation": "native_install_plan", "state": "blocked",
                "mutated": False,
                "reason": {"code": "version_unavailable", "message": str(exc)}}
    return {"ok": True, "operation": "native_install_plan", "state": "ready",
            "mutated": False, "matrix_id": plan.matrix_id,
            "host_packages": [dict(item) for item in plan.host_packages],
            "image_packages": [dict(item) for item in plan.image_packages],
            "sources": [dict(item) for item in plan.sources],
            "simulation_digest": plan.simulation_digest}
