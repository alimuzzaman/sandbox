# wp-mcp

Exposes the Sandbox WordPress runtime to any MCP-speaking LLM.

## Tools

| Tool | Purpose |
|---|---|
| `wp_cli` | run any wp-cli command |
| `wp_rest` | call the WP REST API (auth: Application Password) |
| `db_query` | read-only SQL (SELECT/SHOW/DESCRIBE/EXPLAIN) |
| `tail_log` | tail `wp-content/debug.log` |
| `activate_plugin` / `deactivate_plugin` | toggle plugins by slug |
| `import_content` | import a WXR XML from `runtime/seeds/` |

## Install

```bash
cd ../..    # back to sandbox/ root
make mcp-install
```

This creates `mcp/wp-server/.venv` and installs `mcp` + `httpx`.

## Configure your LLM client

For `wp_rest`, generate an Application Password in WordPress
(`/wp-admin/profile.php` → Application Passwords) and export it as
`WP_APP_PASSWORD`. `wp_cli`, `db_query`, and `tail_log` don't need it.

### Claude Code (`~/.claude/mcp.json`)

```json
{
  "mcpServers": {
    "wp-sandbox": {
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
