# MCP Contract

The `secrets` tool group is explicitly registered but absent from default, WordPress, and Compose catalogs. Operators must opt into the group, and project configuration must separately authorize each source mode or use profile.

## `secret_inspect`

Inputs:

- `project_dir` (required)
- `source` (required registered alias)
- `keys` (optional bounded list)
- `mode`: `keys`, `metadata`, or `masked`; default `keys`
- `exact_length`: boolean; only metadata plus one key

Returns the transport-neutral inspection result. It never returns plaintext or a source path.

## `secret_validate`

Inputs: `project_dir`, `source`, one `key`, and reviewed `profile`.

Returns check states and `live_checked=false`.

## `secret_use_profile`

Inputs: `project_dir` and registered `profile` only.

The profile fixes source, key, direct command, destination, timeout, and output budget. The tool returns only bounded redacted output and non-secret completion metadata.

## Deliberately absent tools

- No plaintext reveal or `get_secret_value`.
- No arbitrary command use.
- No tool argument containing a candidate secret.
- No ordinary form elicitation for passwords, API keys, or tokens.
- No arbitrary source path.

MCP may gain out-of-band update preparation, registered-reference copy, or generation later without changing the prohibition on plaintext tool arguments.

## Authorization behavior

- Enabling the group alone grants no source access.
- `secret_inspect` and `secret_validate` require their mode in the source's `mcpModes`.
- `secret_use_profile` requires both `mcp=true` on the profile and `use` in the
  source's `mcpModes`.
- A scoped service factory binds `project_dir` to the configured MCP project root when one exists.
- Every request records intent before source processing and outcome afterward.
