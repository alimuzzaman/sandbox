# Contract: MCP tools + CLI

## MCP tools (`mcp/wp-server/tools/`)

### `tail_log(file="debug", lines=…, *, project_dir)` (extended)
- `file ∈ {debug (default, current behavior), dump, qm}` → tails `debug.log` / `debug-dump.log` / `qm.jsonl`.

### `qm_capture(url, collectors=None, *, project_dir)`
- If QM is inactive, auto-activate it (idempotent), then `http_fetch(url)`, read the **last** `qm.jsonl` line, return parsed JSON filtered to `collectors` (default trims `hooks`).
- Works for anonymous requests (collector read bypasses the QM cap gate).

### `xdebug(action="on|off|status", *, project_dir)`
- Wraps `cmd_xdebug`; works on Docker and herd (or returns an actionable message on herd if host PHP can't be toggled). Documents the `XDEBUG_TRIGGER` requirement.

## CLI (`sandbox/commands/`)

- `./sb dump [--follow] [--clear]` — tail/clear `debug-dump.log`.
- `./sb qm <url> [--collectors db_queries,timing,php_errors] [--clear] [off]` — capture / clear / deactivate.
- `./sb xdebug on|off|status` — extended to herd (existing Docker behavior unchanged).

## mu-plugins (provisioned, idempotent)

- `00-sandbox-dump.php` — `dump()`/`dd()` via vendored VarDumper `CliDumper` → `debug-dump.log`; dev-gated; `function_exists`-guarded.
- `00-sandbox-qm.php` — `shutdown`@`PHP_INT_MAX` reads `QM_Collectors` → `qm.jsonl`; whitelist collectors; never define `QM_DISABLED`, do define `QM_HIDE_SELF`.

## Notes
- New MCP tool(s) ⇒ Claude Code restart (gotcha #4).
- `?_envelope` REST path documented as the zero-config alternative for REST-scoped QM data.
- `debug-dump.log` / `qm.jsonl` are runtime, gitignored.
