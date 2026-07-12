# MCP Baseline Inventory

**Audit point**: `e52eb8d`
**Count**: 51 tools in 17 groups
**Current composition**: `mcp/wp-server/server.py` manually imports groups; tool modules register by import side effect and wildcard-import `app` except where noted.

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
| `hermes.py` | `hermes_status`, `hermes_run`, `hermes_job_status`, `hermes_job_kill` | Agent infrastructure |
| `instances.py` | `ensure_instance`, `destroy_instance`, `recreate_instance`, `setup_domains`, `secure_instance`, `apply_config` | Shared candidate with WP semantics |
| `mail.py` | `mail_list`, `mail_get` | WordPress/Mailpit |
| `net.py` | `http_fetch`, `pixelmatch_diff`, `visit` | URL/shared candidate |
| `plugin_check.py` | `run_plugin_check` | WordPress plugin |
| `remote.py` | `remote_deploy` | WordPress remote |
| `skills.py` | `list_skills`, `skill_write`, `skill_edit`, `skill_delete` | Agent infrastructure |
| `wp.py` | `wp_cli`, `wp_exec`, `wp_rest`, `run_tests`, `wp_cli_async`, `wp_cli_job`, `wp_cli_job_kill` | WordPress |

## Compatibility contract

- Public tool names and required input parameters are compatibility requirements.
- Existing response keys/error envelopes are captured by focused schema/result tests before each group migrates.
- Registration order becomes deterministic but is not itself a public protocol contract.
- Each group appears once in the package manifest; duplicate group/tool ownership fails startup.
- Project-provided Python and filesystem discovery are not registration sources.
