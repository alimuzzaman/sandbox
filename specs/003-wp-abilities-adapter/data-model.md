# Data Model: In-Instance WP Abilities + MCP Adapter Layer

No database tables. Entities are WP options, files, and registered abilities.

## Ability

A named, discoverable WP capability registered via `wp_register_ability('sandbox/<name>', …)`.

| Field | Description |
|-------|-------------|
| name | `sandbox/<verb>` (e.g. `sandbox/execute-php`) |
| input_schema / output_schema | JSON Schema for args + result |
| permission_callback | requires logged-in user AND `manage_options` |
| meta.mcp.public | `true` to expose over MCP |
| annotations | `readonly`, `destructive`, `idempotent` |

Set: `execute-php`, `read-file`, `write-file`, `edit-file`, `list-directory`, plus
the `mcp-adapter/discover-abilities` override.

Annotation matrix: `execute-php` → destructive, not readonly, not idempotent;
`write/edit-file` → destructive; `read-file`/`list-directory` → readonly + idempotent.

## Enable flag

| Field | Description |
|-------|-------------|
| option `sandbox_abilities_enabled` | per-instance on/off, read by the loader + every permission_callback |
| mirror | `sandbox.local.yml` `instances.<name>.abilities_enabled` (operator-visible, set by `./sb abilities`) |

State: `on` (default at provision) ↔ `off`. When off, the loader registers nothing
and the endpoint exposes no abilities.

## Instance MCP endpoint

| Field | Description |
|-------|-------------|
| url | `<instance-base>/wp-json/<adapter-namespace>` (docker: `http://localhost:<port>/…`; herd: `https://<instance>.test/…`) |
| auth | Application Password (Basic), gated on `is_ssl() \|\| WP_ENVIRONMENT_TYPE=local` |

Derived from the registry + `sandbox.local.yml`; emitted by `./sb connect`.

## Sandbox-code folder + safe-mode marker

| Field | Description |
|-------|-------------|
| dir | `wp-content/sandbox-code/` — only place `write-file` may create new `.php` |
| loaded | each `*.php` required behind the crash-recovery shutdown handler |
| `.crashed` marker | JSON of the last fatal (file, message); presence ⇒ safe mode (skip all sandbox files) |
| manual override | `?sb_safe_mode=1` forces safe mode for one request |

State machine: normal → (fatal in a sandbox file) → `.crashed` written → safe mode
until the marker is removed (admin notice explains how).

## Relationships

- Enable flag gates the loader → which registers Abilities → exposed at the endpoint.
- `write-file` is path-jailed to ABSPATH; new `.php` constrained to sandbox-code/.
- The crash-recovery loader governs sandbox-code/ independently of the abilities
  (sandbox files load even when abilities are off, without the recovery overhead).
