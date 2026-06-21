# Phase 0 Research: Per-Project-First & Modular `sb`

Decisions resolving the technical-context choices. Grounded in the three exploration passes
(command map, core-helpers map, packaging map).

## R1 — Packaging-safe modularization

- **Decision**: Keep `sb` as the single polyglot ENTRY file; move logic into a `sandbox/`
  Python package imported via the existing `sys.path.insert(0, ROOT)`.
- **Rationale**: The global symlink (`sb global`), npm `bin/sandbox.js` shim, and the
  `make-release.sh` tarball all resolve `sb` as ONE file; turning `sb` into a directory breaks
  all three. A sibling package imported by the thin entry is the same mechanism already used
  for `sandbox_core.py`, so it is proven safe.
- **Alternatives**: `sb` as a package dir (breaks symlink/shim/tarball); pluggable
  entry-points (unneeded — no external feature plugins); stay monolithic (fails the modular
  goal).

## R2 — App-password unification

- **Decision**: Read/write `instances.<name>.app_password` for EVERY instance in both `sb`
  and `server.py`; delete the legacy `main`→`mcp.wp.application_password` branch.
- **Rationale**: Non-`main` instances already use the per-instance key; the legacy key only
  served the synthesized `main`. Unifying first (Stage A) preserves auth parity before `main`
  is removed (constitution VI).
- **Alternatives**: keep the legacy key (blocks `main` removal); migrate the value (no value
  to migrate once `main` is gone).

## R3 — `resolve_instances` source

- **Decision**: Source instances from the registry (+ their `sandbox.local.yml` blocks);
  remove the `{main: …}` synthesis.
- **Rationale**: The registry is already the source of truth (constitution II); the synthesis
  existed only for the single-instance legacy model. Per-project blocks written by
  `ensure_instance` provide all config the synthesized `main` used to default.
- **Alternatives**: keep synthesis (reintroduces `main`); read only sandbox.yml `instances:`
  (drops registry-only instances).

## R4 — Resolution when no project resolves

- **Decision**: `--instance` → `$SANDBOX_INSTANCE` → cwd-project (registry) → **error** with
  guidance. PROJECT_ROUTED commands (`init`/`ensure`/`test`/`mcp`/`smoke`, `apply
  --project-dir`) stay project-routed and are exempt.
- **Rationale**: Eliminates the silent `main` fallback (FR-001/002) — the headline bug.
- **Alternatives**: fallback to `main` (the bug); auto-create an instance (surprising side
  effect).

## R5 — `main` surface inventory (what must change)

- **Decision / facts** (from exploration): `resolve_instances` synthesis (`sb` ~L459-465);
  delete guards `cmd_instance` ~L5168, dashboard ~L5991, web ~L6455; name reservation
  `_derive_instance_name` ~L4105; web UI `src/web/src/render.ts` L49 + `instance.ts` L53
  (rebuild `config/sandbox-web.js`); `server.py` `DEFAULT_INSTANCE` L46 + app-pw branch ~L191;
  legacy migration `migrate_legacy_layout` ~L1476-1557 + `_legacy_stack_running` ~L1462 + call
  ~L7036.
- **Rationale**: A complete inventory prevents a half-removed `main` (worse than either state).

## R6 — Required-arg conversion

- **Decision**: After deleting `DEFAULT_INSTANCE`, make `instance` a required parameter on
  `compose`/`wpcli`/`save_local_app_password`/`_active_project_name` (+ `server.py`
  equivalents), after auditing every call site passes one.
- **Rationale**: The default `=DEFAULT_INSTANCE` silently targeted `main`; a required arg
  surfaces any missed call site at the boundary instead of silently mis-targeting.
- **Alternatives**: keep a default sentinel (reintroduces an implicit instance).

## R7 — Staged ordering (parity before removal before refactor)

- **Decision**: Stage A (feature-migrate) → Stage B (remove model) → Stage C (extract
  package); each a separate, live-verified commit.
- **Rationale**: Constitution VI (parity before removal); refactor last so the package is
  extracted from already-correct code (avoid moving then mutating). De-risks critical shared
  tooling.
- **Alternatives**: refactor-first (moves code then changes behavior — harder to verify);
  big-bang (unreviewable, high blast radius).

## R8 — Web dashboard bundle

- **Decision**: Remove the `name === "main"` delete-guard in `src/web/src/{render,instance}.ts`
  and rebuild the vendored bundle via `scripts/build-web-js.sh` → `config/sandbox-web.js` in
  the SAME change.
- **Rationale**: The committed `config/sandbox-web.js` is what `sb web` serves; editing TS
  without rebuilding would leave the guard live (constitution V, docs/build-with-code).
