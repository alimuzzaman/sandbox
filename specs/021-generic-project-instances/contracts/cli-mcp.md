# Contract: CLI and MCP Surface

## CLI

Shared commands dispatch by capability: `init`, `ensure`, `apply --project-dir`, `up`, `down`, `status`, `logs`, `shell`, `open`, `secure`, and `instance delete/recreate`.

New initialization forms:

```text
sb init --type compose
sb init --type astro
```

`--type compose` validates or gathers the explicit Compose contract. `--type astro` proposes and writes an Astro-flavored Compose contract. Neither path starts the project until the user runs or confirms ensure.

Status JSON adds, without removing existing fields:

```json
{
  "instance": "alimuzzaman-me",
  "display_name": "alimuzzaman.me",
  "kind": "compose",
  "adapter": "compose/1",
  "service": "web",
  "capabilities": ["instance.ensure", "instance.status"],
  "http_port": 43210,
  "url": "https://alimuzzaman-me.tst",
  "status": "ready"
}
```

WordPress CLI commands (`wp`, `seed`, `snapshot`, `restore`, `reset`, `xdebug`, `qm`, `plugin-check`, and related surfaces) fail with the adapter capability error for generic projects.

## MCP

Existing lifecycle names remain stable: `ensure_instance`, `destroy_instance`, `recreate_instance`, `apply_config`, `secure_instance`, `setup_domains`, `http_fetch`, `visit`, and `pixelmatch_diff` become kind-neutral where their contracts allow it.

Add kind-neutral tools:

- `instance_status(project_dir, label?)`
- `instance_logs(project_dir, label?, lines=200, service?)`
- `instance_exec(command, project_dir, label?, service?, timeout=60)`

All lifecycle returns include `kind`, `adapter`, and `capabilities`. Existing WordPress tools keep their names and parameters but add a preflight capability error for generic projects. No WordPress tool is silently aliased to a generic tool.

## Compatibility

- Existing MCP clients that ignore additive fields continue working.
- Existing WordPress project calls produce the same values and side effects.
- Tool descriptions change from “WordPress instance” to “project instance” only for kind-neutral tools; WordPress-only tools stay explicit.
- MCP server bootstrap loads tool groups through one package-owned loader; this does not permit project-supplied tool modules.

## Convergence amendment — 2026-08-13: identity parity and state freshness

CLI and MCP lifecycle calls resolve one canonical project identity before
dispatch. Equivalent requests MUST expose byte-equivalent `identity`, canonical
root, label, kind, adapter, and capabilities (apart from presentation-only
fields). A plugin-shaped fallback is not permitted for generic projects.

Every status/state response includes an observation timestamp and generation (or
an explicit `stale:true` marker). A new session or mutation invalidates prior
state; `status --refresh` bypasses the cache. The adapters must not report a
plugin active/inactive state from an old session as current. These rules close
`cf5e49ed`, `2b080bf5`, and `108318d9`.
