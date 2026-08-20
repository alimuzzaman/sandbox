# wp-mcp

Exposes the Sandbox WordPress runtime to any MCP-speaking LLM.

The MCP startup instructions are intentionally compact. Use `./sb` for routine
setup, status, configuration, and tests; use MCP for live runtime evidence such
as authenticated REST, browser inspection, database access, and instance logs.
Load the full operating guide or a focused skill only when the task requires it.
Log and file reads are bounded by default; request a larger explicit limit only
when omitted content is relevant.
Browser reports also cap repetitive iframe, console, network, and error entries
at 100 items while preserving failure detection and a truncation flag.

The default MCP catalog is the 32-tool core set: `instances,runtime,wp,net,
data,fs,context`. To enable extra groups, set `SANDBOX_MCP_GROUPS` before
starting the server, for example `instances,runtime,wp,net,data,fs,context,
debug,mail`. Set it to `all` for the complete catalog. Available groups are
listed in `mcp/wp-server/tools/manifest.py`.

For a focused project registration, start the server with `./sb mcp
--project-dir /path/to/project`. Sandbox resolves the project's explicit runtime
before registration: a Compose project receives only `instances,runtime,net,remote`,
while WordPress receives `instances,wp,net,data,fs,mail,context,remote`. This keeps
WordPress tools out of generic project catalogs and generic runtime exec/log tools out
of WordPress catalogs. `SANDBOX_MCP_GROUPS` remains an explicit operator override.

## Tools

| Tool | Purpose |
|---|---|
| `wp_cli` | run any wp-cli command |
| `wp_rest` | call the WP REST API (auth: Application Password) |
| `db_query` | read-only SQL (SELECT/SHOW/DESCRIBE/EXPLAIN) |
| `snapshot` / `wp_reset` | capture a named snapshot (`db_only: true` skips uploads) / reset to protected `@install` (`confirm: true`) |
| `tail_log` | tail `wp-content/debug.log` |
| `activate_plugin` / `deactivate_plugin` | toggle plugins by slug |
| `import_content` | import a WXR XML from `runtime/seeds/` |
| `resource_cleanup_plan` / `resource_cleanup_apply` | review a legacy cache/stale scope or manually plan/apply one `safe`, `tmp`, or `all` reclaim tier; tier application requires `confirm: true` |

The resource tools require exactly one selector: `scope` (`cache` or `stale`)
for the legacy planner, or `tier` (`safe`, `tmp`, or `all`) for tiered
reclamation. A tier application plans and executes the requested tier only when
`confirm: true`; it never exposes the scheduled automatic-cleanup path.

`snapshot` is capability-gated before it resolves or invokes an instance. Its
`db_only` and `force` flags are forwarded to the CLI, and successful responses
contain only the safe instance/snapshot identifier, mode, force flag, and
bounded outcome. CLI progress (host paths, command lines, or archive/database
bytes) is not returned through MCP. `wp_reset` requires `confirm: true` for a
destructive reset; `rebaseline: true` only captures the current DB baseline.
The dashboard bridge's named restore route also requires `confirm: true` before
it accepts a job.

## Install

```bash
cd ../..    # back to sandbox/ root
make mcp-install
```

This creates `mcp/wp-server/.venv` and installs the pinned MCP 1.25.0 SDK plus
`httpx`. The pin preserves the `FastMCP.streamable_http_app()` transport used by
remote VPS hosting; do not upgrade it independently of `server.py` and its
fresh-venv transport test.

## Configure your LLM client

For `wp_rest`, generate an Application Password in WordPress
(`/wp-admin/profile.php` → Application Passwords) and export it as
`WP_APP_PASSWORD`. `wp_cli`, `db_query`, and `tail_log` don't need it.

### Claude Code (`~/.claude/mcp.json`)

```json
{
  "mcpServers": {
    "sandbox": {
      "command": "/abs/path/to/sandbox/mcp/wp-server/.venv/bin/python",
      "args": ["/abs/path/to/sandbox/mcp/wp-server/server.py"],
      "env": {
        "WP_URL": "http://localhost:8088",
        "WP_ADMIN_USER": "admin",
        "WP_APP_PASSWORD": "xxxx xxxx xxxx xxxx"
      }
    }
  }
}
```

### Cursor / Cline / Continue / Zed

Same shape — point `command` at the venv python and `args` at `server.py`.

## Try it without an LLM

```bash
make mcp-run
```

This launches the stdio server in the foreground. Use [MCP Inspector](https://github.com/modelcontextprotocol/inspector)
to poke at it manually.
