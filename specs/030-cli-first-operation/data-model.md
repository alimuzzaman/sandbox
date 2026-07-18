# Data model: CLI-first Sandbox operation

## CLI guide

| Field | Meaning | Validation |
|---|---|---|
| `mode` | Interface preference | always `cli-first` |
| `project_kind` | Selected runtime kind | `compose` or `wordpress` |
| `project_root` | Descriptor root when known | optional canonical path |
| `skill` | Entry point for operating guidance | shipped skill command |
| `commands` | Runtime-specific command catalog | non-empty command/purpose entries |
| `mcp` | MCP availability statement | optional transport guidance |

## Execution request

| Field | Meaning | Validation |
|---|---|---|
| instance | Resolved project instance | must have registered project owner |
| command | argv list | non-empty strings, no NUL byte |
| capability | runtime authorization | `compose.exec` before invocation |
| service | declared Compose public service | resolved by existing descriptor adapter |

The guide is read-only. Execution uses existing runtime result data and does
not introduce persistent state.
