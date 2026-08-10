# Contract: CLI + proxy MCP tools

## `./sb abilities <on|off|status|connect> [--instance <name>]`

- Resolves the instance (cwd project → registry; or `--instance`/`$SANDBOX_INSTANCE`).
- `on` / `off`: set the `sandbox_abilities_enabled` option + mirror to
  `sandbox.local.yml`; idempotent.
- `status`: print enabled/disabled, the endpoint URL, any persisted enable-state
  mirror, and the development/staging reminder.
- `connect`: print the endpoint and a paste-ready `mcp-remote` configuration. It
  identifies the gitignored local Application Password location rather than
  printing the secret.
- Errors with guidance if run outside a registered project (constitution I/II).

## Proxy MCP tools (`mcp/wp-server/tools/abilities.py`)

### `wp_eval_live(code: str, *, project_dir: str) -> dict`
- Resolves the instance, POSTs `code` to `sandbox/execute-php` at the instance
  endpoint (app-password auth), returns the structured result.

### In-session file tools

The existing `fs_read`, `fs_write`, and `fs_list` tools remain the supported
host-side file surface. External MCP clients use the direct instance abilities
(`sandbox/read-file`, `sandbox/write-file`, `sandbox/edit-file`, and
`sandbox/list-directory`); no separate `wp_file_*` proxy tools are registered.

`wp_eval_live` and the `fs_*` tools take the mandatory `project_dir` and require
`ensure_instance` first, consistent with the rest of the MCP surface. New MCP tools
require a Claude Code restart (CLAUDE.md gotcha #4).
