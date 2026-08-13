# Phase 1 Data Model: Single Swappable Per-User Base

This feature has no database schema; the "data model" is the path-derivation graph and the
artifact-classification that governs migration.

## Entity: Base (`SANDBOX_HOME`)

| Field | Value / Rule |
|-------|--------------|
| `BASE` | `Path(env SANDBOX_HOME or "~/sandbox").expanduser().resolve()` |
| `RUNTIME_DIR` | `BASE / "runtime"` |
| `CONFIG_FILE` | `BASE / "config.json"` (user-global config) |
| `LOCAL_YML` | `BASE / "sandbox.local.yml"` (per-machine instance blocks + secrets) |
| `ENV_LOCAL` | `BASE / ".env.local"` (secrets; mode `600`) |
| Creation | `BASE` and `RUNTIME_DIR` created on demand (idempotent `mkdir -p`) |
| Invariant | Every machine-state path derives from `BASE`; no `ROOT/"runtime"` survives |

## Entity: Path map (derived constants)

All of these rebase from `ROOT/"runtime"` (or legacy config locations) onto
`RUNTIME_DIR`/`BASE`. Single source = `_paths.py`; mirrored in `app.py`.

| Constant | Old | New |
|----------|-----|-----|
| `COMPOSE_DIR` | `ROOT/runtime/compose` | `RUNTIME_DIR/compose` |
| `WP_DIR` / `wp_dir(inst)` | `ROOT/runtime/wp[-inst]` | `RUNTIME_DIR/wp[-inst]` |
| `SNAPSHOTS_DIR` | `ROOT/runtime/snapshots` | `RUNTIME_DIR/snapshots` |
| `SEEDS_DIR` | `ROOT/runtime/seeds` | `RUNTIME_DIR/seeds` |
| `DL_CACHE_DIR` | `ROOT/runtime/dl-cache` | `RUNTIME_DIR/dl-cache` |
| `TOOLS_VENV` | `ROOT/runtime/.venv-tools` | `RUNTIME_DIR/.venv-tools` |
| `PROXY_DIR` (+certs/Caddyfile/compose) | `ROOT/runtime/proxy` | `RUNTIME_DIR/proxy` |
| `_HTTPS_OFFER_MARKER` | `ROOT/runtime/.https-offer-declined` | `RUNTIME_DIR/.https-offer-declined` |
| `TEST_SUITE_DIR` / `TEST_TOOLS_DIR` | `ROOT/runtime/test-*` | `RUNTIME_DIR/test-*` |
| PHP extension build cache | `ROOT/runtime/build/php-extensions/<digest>` (new) | `RUNTIME_DIR/build/php-extensions/<digest>` |
| registry | `ROOT/runtime/registry.json` | `RUNTIME_DIR/registry.json` |
| herd shims | `ROOT/runtime/herd-shims/<inst>` | `RUNTIME_DIR/herd-shims/<inst>` |
| `wp-cli.phar` | `ROOT/runtime/wp-cli.phar` | `RUNTIME_DIR/wp-cli.phar` |
| `CONFIG_LOCAL` | `ROOT/sandbox.local.yml` | `LOCAL_YML` |
| secrets env | `ROOT/.env.local` | `ENV_LOCAL` |
| user-global config | `~/.config/sandbox/config.json` | `CONFIG_FILE` |

`ROOT`-relative **code/asset** constants are unchanged: `ENTRY`(sb), `CONFIG`(sandbox.yml),
`MCP_DIR`, `MCP_VENV`, `CLI_VENV`, `TOOLS_DIR`, skills/workflows, `CLAUDE.md`.

## Entity: Artifact classification (governs migration)

| Class | Artifacts | Migration action |
|-------|-----------|------------------|
| **Pure data** (move as-is) | `wp-<inst>/`, `snapshots/`, `dl-cache/`, `seeds/`, `registry.json`, `test-suite/`, `test-tools/`, proxy `certs/`, `sandbox.local.yml`, `.env.local`, `config.json` | `shutil.move` into base; preserve perms (esp. `.env.local` 600) |
| **Regenerated** (rebuild from config) | compose files (`compose/`), herd shims, `Caddyfile`/`proxy.yml` | regenerate post-move (absolute mounts) |
| **Recreated** (baked interpreter path) | `.venv-tools`, PHP extension build contexts | delete/rebuild under the active base; extension contexts are content-addressed and carry safe provenance |
| **Unaffected** (not under base) | DB named volumes, plugin sources (gotcha #3), sudoers/launchd→repo, `.cli-venv`, `mcp/.venv` | none |

## Entity: Registry entry (unchanged shape)

Keyed by **project-root path** (the plugin checkout) → instance name/ports/server/php.
Project-root paths are independent of `BASE` and remain valid across migration. Only the
registry *file location* moves (`RUNTIME_DIR/registry.json`).

## State transitions: Migration

```
UNMIGRATED  (state in <repo>/runtime + ~/.config + repo-root; new base empty)
   │  sb migrate  /  lazy first-run hook
   ▼
MOVING      (pure-data moved item-by-item; re-runnable on interruption)
   ▼
REGENERATING (.venv-tools recreated; compose/herd/caddy regenerated absolute)
   ▼
VERIFIED    (each registered instance boots + serves)  ──► MIGRATED
   
MIGRATED    (base populated, <repo>/runtime gone)  ── re-run ──► no-op (idempotent)

CONFLICT    (both base AND <repo>/runtime populated) ──► abort, base authoritative,
            surface guidance (no merge/overwrite)
```

## Validation rules

- `BASE` MUST resolve to one absolute path (expanduser + resolve); parent created or
  actionable error.
- Migration MUST NOT delete source before target is in place (move = atomic rename on same
  FS; copy+verify+delete across FS).
- Secret files MUST retain mode `600`; contents MUST NOT be logged/echoed.
- After migration, zero machine-state paths under the repo (verifiable via clean
  `git status` + absence of `runtime/`, `sandbox.local.yml`, `.env.local`).
- CLI and MCP MUST compute identical `BASE` for a given environment.
- PHP extension build-cache paths MUST derive from the same `BASE`; a digest change
  invalidates reuse without touching database volumes, uploads, snapshots, or project
  files, and cache metadata MUST contain no secrets.
