"""Explicit deterministic manifest for built-in MCP tool groups."""

from __future__ import annotations

import importlib

from composition import ToolGroupRegistry, ToolGroupSpec


BUILTIN_TOOL_GROUPS = (
    "instances", "wp", "net", "data", "fs", "mail", "context", "cache",
    "abilities", "skills", "debug", "e2e", "ci", "asyncjobs",
    "plugin_check", "remote", "hermes", "recovery",
)

# Groups in this map are import-safe and bind only the dependencies their public
# functions use. All other groups retain the bounded legacy app compatibility
# bridge until they are migrated independently.
_EXPLICIT_GROUP_DEPENDENCIES = {
    "instances": (
        "sandbox_root", "proxy_tld", "core", "load_sandbox_yml",
        "project_instance", "resolve_instance", "safe_json", "site_url",
    ),
    "hermes": ("hermes_service",),
}

# Exact registration ownership and order, kept separate from implementation
# imports so duplicate ownership fails before FastMCP is initialized.
BUILTIN_TOOL_NAMES = {
    "instances": ("ensure_instance", "destroy_instance", "recreate_instance", "setup_domains", "secure_instance", "apply_config"),
    "wp": ("wp_cli", "wp_exec", "wp_rest", "run_tests", "wp_cli_async", "wp_cli_job", "wp_cli_job_kill"),
    "net": ("http_fetch", "pixelmatch_diff", "visit"),
    "data": ("db_query", "import_content", "wp_reset"),
    "fs": ("tail_log", "fs_read", "fs_write", "fs_list"),
    "mail": ("mail_list", "mail_get"),
    "context": ("focus_get", "activate_plugin", "deactivate_plugin", "load_context", "load_workflow", "load_skill"),
    "cache": ("cache_info", "cache_clear"),
    "abilities": ("wp_eval_live",),
    "skills": ("list_skills", "skill_write", "skill_edit", "skill_delete"),
    "debug": ("qm_capture", "xdebug"),
    "e2e": ("run_e2e",),
    "ci": ("ci_plan", "ci_run"),
    "asyncjobs": ("async_job_status", "async_job_kill"),
    "plugin_check": ("run_plugin_check",),
    "remote": ("remote_deploy",),
    "hermes": ("hermes_status", "hermes_run", "hermes_job_status", "hermes_job_kill", "hermes_cron_list", "hermes_cron_validate", "hermes_cron_create", "hermes_cron_route", "hermes_cron_run", "hermes_cron_output", "hermes_health", "hermes_worktree_list", "hermes_worktree_inspect", "hermes_worktree_preserve", "hermes_repo_sync", "hermes_gateway_converge", "hermes_cron_catalog", "hermes_cron_reconcile", "hermes_cron_verify"),
    "recovery": ("recovery_profiles", "recovery_plan", "recovery_list", "recovery_verify", "recovery_create", "recovery_restore_plan", "recovery_restore_apply", "recovery_schedule_plan", "recovery_retention_plan"),
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


def built_in_tool_registry() -> ToolGroupRegistry:
    registry = ToolGroupRegistry()
    for order, group_id in enumerate(BUILTIN_TOOL_GROUPS):
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
