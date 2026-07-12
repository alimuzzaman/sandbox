# Recovery Interface Inventory

| Surface | Before | Current checkpoint | Addition |
|---|---:|---:|---|
| CLI root commands | 67 | 68 | `recovery` |
| MCP tools | 51 | 53 | `recovery_profiles`, `recovery_plan` |

The CLI command is registered by `sandbox/commands/recovery.py` through `CommandSpec`.
The MCP group is registered by `mcp/wp-server/tools/recovery.py` through the explicit tool-group manifest. No parser or server bootstrap import list was extended directly.
