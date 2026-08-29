"""Explicit deterministic manifest for built-in MCP tool groups."""

from __future__ import annotations

import importlib

from composition import ToolGroupRegistry, ToolGroupSpec


BUILTIN_TOOL_GROUPS = (
    "instances", "domains", "runtime", "jobs", "wp", "net", "data", "fs", "mail", "context", "cache",
    "resources", "feedback",
    "abilities", "skills", "debug", "e2e", "ci", "asyncjobs", "secrets",
    "plugin_check", "remote", "hermes", "recovery", "sync",
)

DEFAULT_MCP_GROUPS = (
    "instances", "domains", "runtime", "jobs", "wp", "net", "data", "fs", "context", "resources", "feedback", "sync",
)

# A scoped server advertises only tools useful to its declared runtime.  The
# unscoped default above remains compatible with the existing one-server,
# many-project registration model; clients that want a precise catalog start
# `sb mcp --project-dir PROJECT` instead.
WORDPRESS_PROJECT_GROUPS = (
    "instances", "domains", "runtime", "jobs", "wp", "net", "data", "fs", "mail", "context", "remote",
    "resources", "feedback", "sync",
)
COMPOSE_PROJECT_GROUPS = (
    "instances", "domains", "runtime", "jobs", "net", "remote", "resources", "feedback", "sync",
)


def project_default_groups(project_kind: str) -> tuple[str, ...]:
    """Return the smallest useful MCP catalog for an explicit runtime kind."""
    groups = {
        "wordpress": WORDPRESS_PROJECT_GROUPS,
        "compose": COMPOSE_PROJECT_GROUPS,
    }.get(project_kind)
    if groups is None:
        raise ValueError(f"no MCP catalog is defined for project kind {project_kind!r}")
    return groups

# Groups in this map are import-safe and bind only the dependencies their public
# functions use. All other groups retain the bounded legacy app compatibility
# bridge until they are migrated independently.
_EXPLICIT_GROUP_DEPENDENCIES = {
    "instances": (
        "sandbox_root", "proxy_tld", "core", "load_sandbox_yml",
        "project_instance", "resolve_instance", "safe_json", "site_url",
        "domain_service",
    ),
    "domains": ("domain_service", "ingress_service"),
    "runtime": ("core", "project_instance", "runtime_service", "native_preflight",
                "managed_package_planner"),
    "wp": (
        "sandbox_root", "compose", "herd_host_env", "host_run", "is_herd",
        "project_instance", "require_project_capability", "resolve_instance",
        "safe_json", "wp_root", "wpcli",
    ),
    "jobs": ("job_service", "target_service", "workspace_service"),
    "hermes": ("hermes_service",),
    "resources": ("resource_service_factory", "reclaim_service_factory",
                  "node_store_service_factory"),
    "feedback": ("feedback_service_factory",),
    "secrets": ("secret_service_factory",),
    "sync": ("sync_service",),
}

# Exact registration ownership and order, kept separate from implementation
# imports so duplicate ownership fails before FastMCP is initialized.
BUILTIN_TOOL_NAMES = {
    "instances": ("ensure_instance", "destroy_instance", "recreate_instance", "setup_domains", "secure_instance", "apply_config"),
    "domains": ("domain_status", "domain_plan", "domain_apply", "domain_cleanup", "domain_support", "ingress_status", "ingress_support", "ingress_plan", "ingress_cleanup", "ingress_reconcile", "ingress_reconsider", "ingress_apply"),
    "runtime": ("instance_status", "instance_logs", "instance_exec",
                "native_support", "native_preflight", "native_install_plan"),
    "jobs": ("job_start", "job_matrix", "job_status", "job_list", "job_output", "job_follow", "job_metrics", "job_reconcile", "job_retention", "job_cancel", "job_artifacts", "job_artifact_get", "job_retry", "job_cleanup", "workspace_create", "workspace_list", "workspace_status", "workspace_reset", "workspace_destroy", "workspace_migration_plan", "workspace_migration_apply"),
    "wp": ("wp_cli", "wp_exec", "wp_rest", "run_tests", "wp_cli_async", "wp_cli_job", "wp_cli_job_kill"),
    "net": ("http_fetch", "pixelmatch_diff", "visit"),
    "data": ("db_query", "import_content", "snapshot", "wp_reset"),
    "fs": ("tail_log", "fs_read", "fs_write", "fs_list"),
    "mail": ("mail_list", "mail_get"),
    "context": ("focus_get", "activate_plugin", "deactivate_plugin", "load_context", "load_workflow", "load_skill"),
    "cache": ("cache_info", "cache_clear"),
    "resources": (
        "resource_status", "resource_cleanup_plan", "resource_cleanup_apply",
    ),
    "feedback": ("feedback_submit", "feedback_list"),
    "abilities": ("wp_eval_live",),
    "skills": ("list_skills", "skill_write", "skill_edit", "skill_delete"),
    "debug": ("qm_capture", "xdebug"),
    "e2e": ("run_e2e",),
    "ci": ("ci_plan", "ci_run"),
    "asyncjobs": ("async_job_status", "async_job_kill"),
    "plugin_check": ("run_plugin_check",),
    "remote": ("remote_deploy",),
    "hermes": ("hermes_status", "hermes_run", "hermes_job_status", "hermes_job_kill", "hermes_cron_list", "hermes_cron_validate", "hermes_cron_create", "hermes_cron_route", "hermes_cron_run", "hermes_cron_output", "hermes_authorization_sync", "hermes_authorization_list", "hermes_authorization_show", "hermes_authorization_request", "hermes_authorization_approve", "hermes_health", "hermes_worktree_list", "hermes_worktree_inspect", "hermes_worktree_preserve", "hermes_repo_sync", "hermes_gateway_converge", "hermes_cron_catalog", "hermes_cron_reconcile", "hermes_cron_verify"),
    "recovery": ("recovery_profiles", "recovery_plan", "recovery_list", "recovery_verify", "recovery_create", "recovery_restore_plan", "recovery_restore_apply", "recovery_schedule_plan", "recovery_retention_plan"),
    "sync": ("sync_once", "sync_status", "sync_start", "sync_stop", "sync_resolve"),
    "secrets": (
        "secret_source_info", "secret_inspect", "secret_validate", "secret_use_profile",
    ),
}


def _import_group(group_id: str):
    """Return the bounded legacy decorator bridge for one named group.

    Group modules still expose their established FastMCP-decorated functions;
    the manifest owns their import order and requires the exact server object
    they register against. This preserves registration behavior while avoiding
    bootstrap-side wildcard imports and makes a later dependency injection
    migration local to the group.
    """
    def register(server, dependencies):
        if dependencies.require("app") is not server:
            raise ValueError(f"MCP app dependency does not match server for group {group_id!r}")
        importlib.import_module(f"tools.{group_id}")
    return register


def _explicit_group(group_id: str):
    """Load an import-safe group and let it register against declared inputs."""
    def register(server, dependencies):
        module = importlib.import_module(f"tools.{group_id}")
        module.register(server, dependencies)
    return register


def built_in_tool_registry(group_ids: tuple[str, ...] | None = None) -> ToolGroupRegistry:
    """Build the registry, optionally loading only selected tool groups.

    Clients can set ``SANDBOX_MCP_GROUPS`` to a comma-separated allowlist before
    starting the server; ``all`` selects the complete catalog.
    """
    selected = set(group_ids) if group_ids is not None else set(BUILTIN_TOOL_GROUPS)
    unknown = selected.difference(BUILTIN_TOOL_GROUPS)
    if unknown:
        raise ValueError(f"unknown MCP tool group(s): {', '.join(sorted(unknown))}")
    registry = ToolGroupRegistry()
    for order, group_id in enumerate(BUILTIN_TOOL_GROUPS):
        if group_id not in selected:
            continue
        dependencies = _EXPLICIT_GROUP_DEPENDENCIES.get(group_id, ("app",))
        register = (_explicit_group(group_id)
                    if group_id in _EXPLICIT_GROUP_DEPENDENCIES
                    else _import_group(group_id))
        registry.add(ToolGroupSpec(
            group_id=group_id,
            register=register,
            owner=f"tools.{group_id}",
            dependencies=dependencies,
            tool_names=BUILTIN_TOOL_NAMES[group_id],
            order=order,
        ))
    return registry
