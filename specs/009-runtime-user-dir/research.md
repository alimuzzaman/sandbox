# Phase 0 Research: Single Swappable Per-User Base

All unknowns resolved against the existing codebase audit (94 refs in `sandbox/`, ~15 in
`mcp/wp-server/app.py`) and the user's stated design.

## D1 — Base location & override

**Decision**: A single base `SANDBOX_HOME`, resolved as
`Path(os.environ.get("SANDBOX_HOME", "~/sandbox")).expanduser().resolve()`. From it:
`RUNTIME_DIR = BASE/"runtime"`, `CONFIG_FILE = BASE/"config.json"`,
`LOCAL_YML = BASE/"sandbox.local.yml"`, `ENV_LOCAL = BASE/".env.local"`. The base dir
(and `runtime/`) is created on demand (`mkdir -p`).

**Rationale**: The user explicitly chose `~/sandbox` over XDG; one variable keeps the
mental model and the swappability invariant trivially testable (point it elsewhere → all
state follows). `expanduser().resolve()` handles `~`, relative paths, and symlinks to one
absolute location.

**Alternatives considered**: XDG `$XDG_DATA_HOME/sandbox` (rejected — user prefers a
visible top-level `~/sandbox`); a separate `SANDBOX_RUNTIME_DIR` distinct from config
(rejected — splits the base again, the very problem we're removing); per-instance bases
(rejected — registry already isolates instances).

## D2 — One canonical resolver, two processes

**Decision**: `_paths.py` is the canonical resolver for the CLI package; every constant
and inline builder derives from `BASE`/`RUNTIME_DIR`. `mcp/wp-server/app.py` keeps its own
small copy (it must run standalone) but resolves the **same** `SANDBOX_HOME` with the same
default. `SANDBOX_ROOT` in the MCP server stays = repo root for *code* assets (skills,
workflows, CLAUDE.md, visit.py, sandbox_core import) but state paths switch to
`RUNTIME_DIR`. On `sb setup`, write `"env": {"SANDBOX_HOME": "<resolved base>"}` into the
generated `.mcp.json` for the `sandbox` server so the MCP process inherits the exact base;
absent that, both default to `~/sandbox`.

**Rationale**: The two processes already duplicate path logic; the failure mode (FR-006,
SC-005) is them disagreeing. Same env + same default = guaranteed agreement. Writing the
env into `.mcp.json` covers non-default bases.

**Alternatives considered**: importing `_paths.py` into the MCP server (rejected — the MCP
server is intentionally standalone with its own venv; a hard import couples their startup);
a shared dotfile read by both (rejected — env var is simpler and already the override).

## D3 — Compose absolute mounts

**Decision**: Generated compose volume sources become **absolute** paths under
`RUNTIME_DIR` (e.g. `/Users/<u>/sandbox/runtime/wp-<inst>:/var/www/html`,
`/Users/<u>/sandbox/runtime/dl-cache/...`, `/Users/<u>/sandbox/runtime/wp-cli.phar:...:ro`).
`docker compose --project-directory` is pointed at the **compose file's dir**
(`RUNTIME_DIR/compose`) rather than the repo ROOT. The plugin-source bind mount keeps its
same-absolute-host-path form (gotcha #3) — unaffected.

**Rationale**: Today relative `./runtime/...` sources are pinned by
`--project-directory ROOT`; once `runtime/` leaves ROOT that breaks. Absolute sources are
location-independent and regenerate cleanly on any base swap. Compose files are
regenerated artifacts, never moved.

**Alternatives considered**: keep relative sources and move `--project-directory` to
`RUNTIME_DIR` (rejected — fragile: any future relative path silently re-pins; absolute is
explicit and audit-proof).

## D4 — Migration (idempotent, one-time)

**Decision**: A `sb migrate` command (also invoked lazily on first command when old state
is detected and the new base is empty) that:
1. Resolves `BASE`; `mkdir -p BASE/runtime`.
2. **Pure-data move** (constitution: move-as-is): `<repo>/runtime/{wp-*,snapshots,
   dl-cache,seeds,registry.json,test-suite,test-tools,proxy,herd-shims,locks,markers,
   wp-cli.phar}` → `BASE/runtime/`; `<repo>/sandbox.local.yml` → `BASE/sandbox.local.yml`;
   `<repo>/.env.local` → `BASE/.env.local` (preserve mode 600);
   `~/.config/sandbox/config.json` → `BASE/config.json`.
3. **Recreate baked artifacts**: delete + rebuild `runtime/.venv-tools`
   (`ensure_tools_venv`); regenerate every instance's compose with absolute mounts;
   regenerate herd shims; regenerate the Caddyfile/proxy compose.
4. **Verify**: for each registered instance, confirm it resolves and boots
   (`sb ensure` / `ensure_instance`) and serves (HTTP 200 / `wp option get siteurl`).
5. Idempotent: if `BASE/runtime` already populated and `<repo>/runtime` gone, no-op.
   On **conflict** (both populated), base is authoritative; abort with guidance, never
   merge/overwrite.
6. Use `shutil.move`; guard against partial moves (move into a temp then rename, or move
   item-by-item with existence checks) so an interrupted run is re-runnable.

**Rationale**: Matches the audited classification (only `.venv-tools` bakes paths among
runtime artifacts; compose/herd/caddy are regenerated from config anyway). Lazy auto-hook
satisfies "existing setup keeps working with no manual steps" (US1).

**Alternatives considered**: copy-then-delete (rejected for large WP installs — slow,
double disk; `mv` on same filesystem is atomic-ish and instant). Note: if `~/sandbox` is
on a *different* filesystem than the repo, `shutil.move` falls back to copy+delete — handle
by attempting move and surfacing progress.

## D5 — Backward-compat fallback

**Decision**: Config/registry/secret readers try the new base path first, then fall back
to the legacy location (`~/.config/sandbox/config.json`, `<repo>/sandbox.local.yml`,
`<repo>/.env.local`, `<repo>/runtime/registry.json`) when the new one is absent. Fallback
reads are logged once (not the contents). Removal of fallback is deferred (constitution VI)
until migration is proven on the live stack.

**Rationale**: An un-migrated environment (or a half-pulled change) must not hard-break
before migration runs (FR-015).

## D6 — Tools venv recreation

**Decision**: `runtime/.venv-tools` is never moved; `ensure_tools_venv` deletes a venv
whose `bin/python` shebang/`pyvenv.cfg` points outside the current base and recreates it.
The MCP server's `TOOLS_VENV_PY` resolves under `RUNTIME_DIR`.

**Rationale**: venv shebangs and `pyvenv.cfg home` bake the absolute interpreter/venv path;
moving them yields a broken `python`. Recreation is cheap (stdlib `venv` + the few tool
deps already pinned).

## D7 — Docs & gitignore

**Decision**: Drop the `runtime/` entry from `.gitignore` (state no longer in tree; keep
ignoring any transient repo files that remain). Update `CLAUDE.md` folder-layout +
gotchas that name `runtime/...` (#3 absolute mount still holds; #10 compose env; #15
dl-cache; #18 wp-cli.phar), `docs/sandbox-config-reference.md` (base + consolidated config
+ user-global layer now `BASE/config.json`), and the constitution's `runtime/registry.json`
references → base-relative (PATCH bump with rationale in the Sync Impact Report).

**Rationale**: Constitution V docs-with-code; stale paths in the agent guide would
mislead every future session.

## D8 — Out of scope / unaffected

- **Databases**: in Docker named volumes, not under `runtime/` — no DB migration.
- **Externals** (`/etc/sudoers.d/sandbox-*`, the lo0 launchd plist): point at the repo
  (`tools/*.sh`), unaffected by a runtime move; only a repo move would touch them.
- **Repo-internal venvs** (`.cli-venv`, `mcp/wp-server/.venv`): stay in the repo (code
  artifacts), not relocated.
- **Herd v1 limitations** (snapshots/xdebug/mailpit) unchanged.
