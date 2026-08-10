# Contract: Abilities (MCP tools exposed at the instance endpoint)

All abilities are namespaced `sandbox/`, `meta.mcp.public=true`, and gated by a
`permission_callback` requiring a logged-in user with `manage_options`.

## `sandbox/execute-php`

Run PHP in the live WordPress runtime.

- **Input**: `{ code: string (no <?php tags) }`
- **Output**: `{ success: bool, return_value: any, output: string, errors: [{type,message,file,line}], error_message?: string, error_class?: string, execution_time_ms: number }`
- **Behavior**: output-buffer + error-handler capture of E_WARNING/E_NOTICE/E_DEPRECATED (+ user-triggered equivalents) into `errors[]`; `set_time_limit(30)` (default cap, 30s); catch `\Throwable` → `success:false`; JSON-safe the return value. Uncatchable fatals (parse errors, `exit/die`, OOM) are documented as disallowed in the ability instructions.
- **Annotations**: destructive=true, readonly=false, idempotent=false.

## `sandbox/read-file`

- **Input**: `{ path: string }`
- **Output**: `{ path, content, size, encoding }`
- **Behavior**: ABSPATH-jailed via `resolve_path` (rejects symlink escape).
- **Annotations**: readonly=true, idempotent=true.

## `sandbox/write-file`

- **Input**: `{ path, content, mode?: overwrite|append, create_directories?: bool, encoding? }`
- **Output**: `{ path, bytes_written, created, size }`
- **Behavior**: ABSPATH-jailed; **new `.php` files restricted to `wp-content/sandbox-code/`**; rejects symlink final-path escape.
- **Annotations**: destructive=true.

## `sandbox/edit-file`

- **Input**: `{ path, old_string, new_string, replace_all?: bool }`
- **Output**: `{ path, replacements, size }`
- **Annotations**: destructive=true.

## `sandbox/list-directory`

- **Input**: `{ path, depth?: int }`
- **Output**: `{ path, entries: [{name, type, size}] }`
- **Annotations**: readonly=true, idempotent=true.

## Discovery

The bundled adapter supplies standard MCP tool discovery for the registered
`sandbox/*` abilities. Sandbox-specific environment guidance is intentionally not
returned by discovery in the current implementation; use `./sb abilities status`
for the endpoint and enable-state reminder, and the normal Sandbox context tools
for focused-plugin and snapshot information.

## Errors

- Disabled layer or failed permission → standard MCP/REST 403.
- Path outside ABSPATH (or symlink escape) → `path_outside_base` error.
- New `.php` outside sandbox-code/ → `php_sandbox_required` error.
