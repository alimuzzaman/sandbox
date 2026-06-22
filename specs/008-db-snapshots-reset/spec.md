# Feature Specification: DB-Only Snapshots & Reset-to-Fresh-Install

**Feature Branch**: `feat/agent-tooling-specs`

**Created**: 2026-06-22

**Status**: Draft

**Input**: "Add `wp reset` like db reset option to reset WP to initial state after install.
Also only db snapshots — most of the time resetting the DB is enough. Also add this to the
mu-plugin." Extends specs 002 (dashboard snapshots) and the `sb snapshot/restore` CLI
([sandbox/commands/data.py](../../sandbox/commands/data.py)).

## Summary

Two related additions to the snapshot/restore system:

1. **DB-only snapshots** — a snapshot that captures only `db.sql`, skipping the
   `uploads.tgz` archive. For most dev/test loops the database *is* the state worth
   saving/rolling back; uploads rarely change and the tar is the slow, large part.
2. **Reset-to-fresh-install** — automatically capture a **baseline** db-only
   snapshot right after install/provision, and add `./sb reset` (+ `wp_reset` MCP +
   a dashboard button) that restores it. This returns WP to its *initial installed
   state* (admin user, default content, activated plugins, generated options) —
   like `wp db reset` but to the **installed baseline**, not an empty schema.

Both surface in the spec-002 **dashboard snapshot mu-plugin** (a DB-only toggle +
a "Reset to fresh install" action).

This is cheap to add: `cmd_restore` already runs `wp db reset --yes` then imports,
and already **skips uploads when `uploads.tgz` is absent** — so a db-only snapshot
restores correctly today with no restore-side change.

## Clarifications

### Session 2026-06-22

- Q: What does "reset" restore to? → A: The **post-install baseline** (state right after `cmd_install` finishes: admin, default content, activated plugins), **not** an empty DB. Captured automatically as a reserved db-only snapshot at install.
- Q: Snapshot scope for this feature? → A: **DB-only** is the focus (and the default for the baseline + reset). Full snapshots (DB + uploads) remain available via the existing path; db-only is a new mode/flag, not a replacement.
- Q: Surface it where? → A: CLI + MCP **and** the spec-002 dashboard mu-plugin (DB-only toggle + "Reset to fresh install" button).

## User Scenarios & Testing *(mandatory)*

### User Story 1 — DB-only snapshot (Priority: P1)

A dev saves just the database before a risky migration.

**Acceptance**:
1. **When** `./sb snapshot before-migration --db-only` (or `snapshot(...,
   db_only=true)` MCP), **Then** the snapshot dir contains `db.sql` + `META`
   (with `mode=db-only`) and **no** `uploads.tgz`; it completes faster than a full
   snapshot.
2. **When** restored, **Then** the DB is replaced point-in-time and uploads are
   left untouched (no error about missing `uploads.tgz`).
3. `./sb snapshots` shows the mode (db-only / full) per entry.

### User Story 2 — Reset to fresh install (Priority: P1)

A dev (or agent) has dirtied the DB and wants the clean post-install state back in
seconds, without recreating the instance.

**Acceptance**:
1. **Given** an instance installed by the Sandbox, **Then** a reserved db-only
   baseline snapshot (`@install`) was captured automatically at install time.
2. **When** `./sb reset` (or `wp_reset` MCP, or the dashboard button), **Then** the
   DB is restored to that baseline; the site is back to its initial installed
   state. Uploads are left as-is (db-only).
3. **When** no baseline exists (older instance), **Then** the command explains how
   to create one (`./sb reset --rebaseline`) instead of failing opaquely.
4. `./sb reset --rebaseline` re-captures the baseline from the current DB (e.g.
   after intentionally changing the activated plugin set).

### User Story 3 — From the WP dashboard (Priority: P2)

**Acceptance**:
1. The spec-002 snapshot mu-plugin Tools screen gains a **"DB only"** checkbox on
   the capture form and a **"Reset to fresh install"** button.
2. Both call the existing `sb web` bridge out-of-band (token-authed) and poll for
   completion, exactly like the current snapshot/restore flow.

### User Story 4 — Reset is destructive, so it's gated (Priority: P1)

**Acceptance**:
1. `./sb reset` without `--yes` prompts for confirmation (it drops the current DB).
   The MCP `wp_reset` requires an explicit `confirm=true`.
2. The reserved baseline cannot be overwritten or deleted by ordinary
   `snapshot`/snapshot-delete operations (only `--rebaseline` replaces it).

## Requirements

- **FR-1** `cmd_snapshot` gains `--db-only`: skip the uploads tar; write
  `mode=db-only` into `META` (full snapshots write `mode=full`). MCP `snapshot`
  tool gains `db_only: bool=False`.
- **FR-2** `cmd_restore` records/uses `mode` for messaging but needs no behavior
  change — it already `db reset --yes` + imports and skips absent `uploads.tgz`.
  It must NOT delete/replace existing uploads for a db-only restore (current
  behavior — keep it).
- **FR-3** `./sb snapshots` and the MCP listing report `mode` per snapshot.
- **FR-4** **Baseline capture**: at the end of `cmd_install` (after content seed +
  plugin activation), automatically take a db-only snapshot under a **reserved**
  name `@install` (stored in a protected dir, e.g. `__install__/`, so
  slug/validation rules don't collide and user ops can't clobber it). Idempotent:
  re-running install refreshes it.
- **FR-5** `./sb reset [--yes] [--rebaseline]` + MCP `wp_reset(confirm, *,
  project_dir)`: restore the `@install` baseline (db-only). `--rebaseline`
  re-captures the baseline from the current DB instead of restoring.
- **FR-6** Guardrails: `reset` is destructive → CLI confirm unless `--yes`, MCP
  requires `confirm=true`. The reserved baseline is excluded from
  overwrite/delete by `snapshot`/snapshot-delete; only `--rebaseline` replaces it.
- **FR-7** **Dashboard mu-plugin (extends spec 002)**: add a "DB only" capture
  toggle and a "Reset to fresh install" button; both go through the existing
  `bridge_token`-authed `sb web` routes, run out-of-band, and poll for completion.
  New bridge routes: `…/snapshot?db_only=1` and `…/reset`.
- **FR-8** Herd parity: snapshots/reset stay **unsupported on herd** in v1 (same
  as spec 002) — `reset`/`--db-only` emit the existing herd-unsupported notice
  pointing at `wp db export`/`import`. (Captured as a known limitation, not a gap.)

## Design notes

- **Why db-only is nearly free**: `db.sql` is exported with `--add-drop-table` and
  restore runs `wp db reset --yes` first (a true point-in-time replacement, see
  CLAUDE.md gotcha #12). Dropping the uploads tar changes only capture; restore
  already no-ops on a missing `uploads.tgz`.
- **Baseline timing**: capture *after* `cmd_install` seeds content and activates
  plugins so "initial state" matches what the dev actually provisioned. A config
  change that alters the activated plugin set makes the baseline stale → that's
  what `--rebaseline` is for (documented).
- **Reserved name**: `@install` is a logical alias; on disk it lives outside the
  user snapshot namespace (`__install__/`) so `_slug_snapshot_name` /
  `_valid_snapshot_name` and overwrite/delete never touch it. `./sb snapshots`
  lists it separately as the baseline.
- **`reset` vs `recreate_instance`**: `reset` is a fast, in-place DB rollback to
  baseline (seconds, keeps uploads, containers, ports); `recreate_instance` wipes
  and rebuilds the whole instance (minutes). `reset` is the everyday "give me a
  clean DB" verb the user asked for.

## Integration points

- CLI: extend `cmd_snapshot` (`--db-only`), add `cmd_reset` in
  [sandbox/commands/data.py](../../sandbox/commands/data.py); hook baseline capture
  into `cmd_install` ([sandbox/commands/lifecycle.py](../../sandbox/commands/lifecycle.py)).
- MCP: `snapshot` tool `db_only` param + new `wp_reset`; extend the snapshot
  listing (`mode`). New tool ⇒ Claude Code restart (gotcha #4).
- Dashboard: extend the spec-002 snapshot mu-plugin + its `sb web` bridge routes.
- Docs: CLAUDE.md (snapshot section + a `reset` common-loop entry + MCP table),
  `skills/snapshot/SKILL.md`, `specs/002-dashboard-snapshots` cross-reference.

## Out of scope (v1)

- Herd support (tracked with spec 002's herd limitation).
- Uploads-only or selective-table snapshots.
- Multiple named baselines (one `@install` baseline per instance; `--rebaseline`
  replaces it).

## Tasks

1. `cmd_snapshot --db-only` + `META mode=…`; MCP `snapshot(db_only=…)`; `snapshots`
   listing shows mode.
2. Baseline capture at end of `cmd_install` → reserved `@install` (`__install__/`),
   idempotent; excluded from user overwrite/delete.
3. `./sb reset [--yes] [--rebaseline]` + MCP `wp_reset(confirm)`.
4. Dashboard mu-plugin: "DB only" toggle + "Reset to fresh install" button + bridge
   routes (`snapshot?db_only=1`, `reset`), out-of-band + poll.
5. Live verification: db-only snapshot omits `uploads.tgz` and restores without
   error; dirty the DB then `./sb reset` returns the post-install state; dashboard
   reset round-trips; herd emits the unsupported notice.
6. Docs: CLAUDE.md + snapshot skill + 002 cross-reference.
