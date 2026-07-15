# MCP Baseline Inventory

**Audit point**: `e52eb8d`; composition verification 2026-07-14
**Current exact registration**: 75 tools in 18 deterministic groups, verified through FastMCP with `mcp/wp-server/.venv`.
**Current composition**: `mcp/wp-server/server.py` composes one deterministic `tools/manifest.py` registry. Each group has one manifest entry and one registration path: `instances` and `hermes` bind only declared dependencies; the other groups use a bounded app compatibility wrapper. Duplicate group IDs and duplicate tool ownership fail before registration.

| Group | Tools | Capability class |
|---|---|---|
| `abilities.py` | `wp_eval_live` | WordPress |
| `asyncjobs.py` | `async_job_status`, `async_job_kill` | Infrastructure |
| `cache.py` | `cache_info`, `cache_clear` | Infrastructure |
| `ci.py` | `ci_plan`, `ci_run` | WordPress-oriented |
| `context.py` | `focus_get`, `activate_plugin`, `deactivate_plugin`, `load_context`, `load_workflow`, `load_skill` | Mixed |
| `data.py` | `db_query`, `import_content`, `wp_reset` | WordPress/data |
| `debug.py` | `qm_capture`, `xdebug` | WordPress |
| `e2e.py` | `run_e2e` | WordPress-oriented |
| `fs.py` | `tail_log`, `fs_read`, `fs_write`, `fs_list` | WordPress runtime filesystem |
| `hermes.py` | `hermes_status`, `hermes_run`, `hermes_job_status`, `hermes_job_kill`, `hermes_cron_list`, `hermes_cron_validate`, `hermes_cron_create`, `hermes_cron_route`, `hermes_cron_run`, `hermes_cron_output`, `hermes_health`, `hermes_worktree_list`, `hermes_worktree_inspect`, `hermes_worktree_preserve`, `hermes_repo_sync`, `hermes_gateway_converge`, `hermes_cron_catalog`, `hermes_cron_reconcile`, `hermes_cron_verify` | Agent infrastructure |
| `instances.py` | `ensure_instance`, `destroy_instance`, `recreate_instance`, `setup_domains`, `secure_instance`, `apply_config` | Shared candidate with WP semantics |
| `mail.py` | `mail_list`, `mail_get` | WordPress/Mailpit |
| `net.py` | `http_fetch`, `pixelmatch_diff`, `visit` | URL/shared candidate |
| `plugin_check.py` | `run_plugin_check` | WordPress plugin |
| `remote.py` | `remote_deploy` | WordPress remote |
| `recovery.py` | `recovery_profiles`, `recovery_plan`, `recovery_list`, `recovery_verify`, `recovery_create`, `recovery_restore_plan`, `recovery_restore_apply`, `recovery_schedule_plan`, `recovery_retention_plan` | Scoped recovery |
| `skills.py` | `list_skills`, `skill_write`, `skill_edit`, `skill_delete` | Agent infrastructure |
| `wp.py` | `wp_cli`, `wp_exec`, `wp_rest`, `run_tests`, `wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill` | WordPress |

## Compatibility contract

- Public tool names, required input parameters, registration order, and the current untyped (`null`) FastMCP response schema are snapshot-asserted in `tests/test_mcp.py`.
- `tests/test_mcp_composition.py` asserts the exact group order/ownership manifest, duplicate group/tool rejection, a test-only group, and isolated dependency registration.
- `instances` declares `sandbox_root`, `proxy_tld`, `core`, `load_sandbox_yml`, `project_instance`, `resolve_instance`, `safe_json`, and `site_url`; `hermes` declares only `hermes_service`. Neither imports `app` or registers at import time.
- Each group appears once in the package manifest; duplicate group/tool ownership fails startup.
- Project-provided Python and filesystem discovery are not registration sources.
