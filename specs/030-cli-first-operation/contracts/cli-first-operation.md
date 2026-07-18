# CLI contract: CLI-first operation

## `sb guide`

```text
sb guide [--project-dir DIR] [--json]
```

- Reads a project descriptor when supplied or discoverable from the current
  directory.
- Selects only the WordPress or Compose command catalog.
- `--json` emits `mode`, `project_kind`, optional `project_root`, `skill`,
  `commands`, and `mcp`.
- Does not resolve, create, or mutate an instance.

## `sb exec`

```text
sb exec [--instance NAME] [--label LABEL] [--json] -- <argv...>
```

- Requires an existing resolved instance owned by a Compose project.
- Requires a non-empty argv list without NUL bytes.
- Checks `compose.exec` before invoking the runtime.
- Executes the list in the descriptor's declared public service.
- Prints command output, or an operation envelope with `--json`.
- Rejects WordPress and malformed input before command execution.

## Compatibility

`sb mcp --project-dir DIR` remains valid. It is an optional MCP transport and
continues to use its runtime-scoped catalog.
