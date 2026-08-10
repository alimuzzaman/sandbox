# Contract: CLI + MCP tools + bridge routes

## CLI (`sandbox/commands/data.py`, `lifecycle.py`)

- `./sb snapshot <name> [--db-only] [--force]` — `--db-only` skips uploads; writes `mode` to META.
- `./sb restore <name>` — unchanged (mode-aware messaging only).
- `./sb snapshots` — lists each normal snapshot's `mode`, plus the protected
  `@install` baseline separately as reset readiness (not a normal restore/delete target).
- `./sb reset [--yes] [--rebaseline]` — restore the `@install` baseline (db-only); `--yes` skips the confirm prompt; `--rebaseline` re-captures it.
- `ensure_instance` and setup-created instances — capture the db-only `@install`
  baseline and full `install-baseline` snapshot after final provisioning; a successful
  onboarding seed refreshes both so they include the seeded fixture (idempotent otherwise).

## MCP tools (`mcp/wp-server/tools/`)

- `snapshot(name, db_only=false, force=false, *, project_dir)` — captures a
  named snapshot; `db_only` skips uploads and `force` replaces an existing name.
- `wp_reset(confirm, rebaseline=false, *, project_dir)` — requires `confirm=true`; restores the baseline (or re-captures with `rebaseline`); errors with guidance if no baseline.
- snapshot listing tool reports `mode`.

## Dashboard (extends the spec-002 snapshot mu-plugin + `sb web` bridge)

- Capture form gains a **"DB only"** checkbox → bridge `…/snapshot?db_only=1`.
- A **"Reset to fresh install"** button → bridge `…/reset`.
- Both run out-of-band via the existing `bridge_token`-authed flow + completion polling.

## Guarantees

- Restoring a db-only snapshot leaves uploads untouched (restore already skips a missing `uploads.tgz`).
- A forced DB-only replacement removes any uploads archive from the snapshot it replaces.
- The `@install` baseline is protected from ordinary overwrite/delete.
- reset is destructive: CLI confirm unless `--yes`; MCP requires `confirm=true`.
- herd instances: snapshots/reset unsupported in v1 — emit the existing herd notice.
- New MCP tool(s) ⇒ Claude Code restart (gotcha #4).
