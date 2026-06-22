# Data Model: DB-Only Snapshots & Reset-to-Fresh-Install

Extends the existing snapshot layout under `runtime/snapshots/<instance>/`.

## Snapshot

| Field | Description |
|-------|-------------|
| name | user-given (slugified) or the reserved `@install` |
| db.sql | `wp db export --add-drop-table` (always present) |
| uploads.tgz | tar of `wp-content/uploads` — **present only for full snapshots**, omitted for db-only |
| META | `project=…`, `instance=…`, **new** `mode=db-only\|full` |

## Baseline (`@install`)

| Field | Description |
|-------|-------------|
| storage | reserved protected dir `runtime/snapshots/<instance>/__install__/`; `@install` is a user-facing label only (not a valid snapshot name, so no collision) [F5] |
| mode | always db-only |
| captured | automatically in the ensure/onboard flow **after** plugin/theme wiring + seed import (not in `cmd_install`) [F1] |
| protection | cannot be overwritten/deleted via CLI (`data.py`), bridge (`_bridge.py`), or dashboard (`_dash.py`); only `reset --rebaseline` replaces it [F3] |

## Reset

| Operation | Effect |
|-----------|--------|
| `reset` | restore the `@install` baseline (db-only): `wp db reset --yes` + import `db.sql`; uploads untouched; instance/containers/ports kept |
| `reset --rebaseline` | re-capture the baseline from the current DB instead of restoring |
| guards | destructive → CLI confirm unless `--yes`; MCP `wp_reset` requires `confirm=true`; no baseline → actionable guidance |

## Listing

`./sb snapshots` / the MCP listing report each snapshot's `mode` (db-only / full); the
`@install` baseline is listed separately as the baseline.

## Constraints

- Restoring a db-only snapshot never deletes/alters existing uploads.
- herd instances: snapshots/reset unsupported in v1 (herd-gated, existing notice).
