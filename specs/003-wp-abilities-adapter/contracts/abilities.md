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

The Sandbox server exposes the adapter's `mcp-adapter/discover-abilities` tool.
Its ordinary `abilities` list is returned unchanged, with one additional bounded
object:

```json
{
  "sandbox_environment": {
    "focused_plugin": "plugin-slug-or-null",
    "instance_url": "https://instance.test/",
    "snapshot_reminder": "Before destructive changes, use the supported Sandbox snapshot workflow."
  }
}
```

The host writes only a validated focused-plugin slug (or `null`) to the in-instance
context document. WordPress rebuilds `instance_url` from `home_url()` while dropping
userinfo, query, and fragment data. Enrichment is scoped to the `sandbox` MCP server;
other servers, other tools, malformed discovery results, and explicit failure/error
envelopes are unchanged. The Sandbox server supplies the adapter's public
transport-permission callback, which requires an authenticated user with
`manage_options` without changing permissions on unrelated adapter servers.

## Errors

- Disabled layer or failed permission → standard MCP/REST 403.
- Path outside ABSPATH (or symlink escape) → `path_outside_base` error.
- New `.php` outside sandbox-code/ → `php_sandbox_required` error.
