# Contract: CLI + proxy MCP tools

## `./sb abilities <on|off|status> [--instance <name>]`

- Resolves the instance (cwd project → registry; or `--instance`/`$SANDBOX_INSTANCE`).
- `on` / `off`: set the `sandbox_abilities_enabled` option + mirror to
  `sandbox.local.yml`; idempotent.
- `status`: print enabled/disabled + the endpoint URL + WP-version support.
- Errors with guidance if run outside a registered project (constitution I/II).

## `./sb connect [--instance <name>] [--client claude|cursor|windsurf|cline|raw]`

- Prints, for the resolved instance: the MCP endpoint URL, an Application Password
  (interactive display only — never written to a tracked file/commit/memory), and a
  ready-to-paste per-client config block (npx mcp-remote / direct HTTP).
- herd instances: emit the `https://<instance>.test/wp-json/...` URL.

## Proxy MCP tools (`mcp/wp-server/tools/abilities.py`)

### `wp_eval_live(code: str, *, project_dir: str) -> dict`
- Resolves the instance, POSTs `code` to `sandbox/execute-php` at the instance
  endpoint (app-password auth), returns the structured result.

### `wp_file_read(path, *, project_dir)` / `wp_file_write(path, content, …)` / `wp_file_list(path, …)`
- Thin proxies to the corresponding file abilities, for in-session convenience.
- (The Sandbox-native `fs_*` tools remain unchanged; these target the instance
  endpoint so behavior matches what external clients get.)

All proxy tools take the mandatory `project_dir` and require `ensure_instance` first,
consistent with the rest of the MCP surface. New tools require a Claude Code restart
(CLAUDE.md gotcha #4).
