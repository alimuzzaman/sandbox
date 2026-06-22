# Research: DB-Only Snapshots & Reset-to-Fresh-Install

## Decision: db-only is a capture-side change only (nearly free)

- **Decision**: `--db-only` skips the `uploads.tgz` tar; restore is unchanged.
- **Rationale**: `cmd_restore` already runs `wp db reset --yes` then imports `db.sql`, and **only restores uploads if `uploads.tgz` exists** (verified in `sandbox/commands/data.py`). So a db-only snapshot restores correctly with zero restore-side changes; `db.sql` already uses `--add-drop-table` and the pre-reset makes it a true point-in-time DB replacement (gotcha #12).
- **Alternatives considered**: a separate db-only restore path — unnecessary.

## Decision: auto-capture the baseline (db-only) AFTER wiring + seed (analysis F1)

- **Decision**: Capture the reserved db-only baseline in the `ensure_instance` flow **after** `_wire_project_plugins`/`_wire_project_themes` AND after `_onboard_instance` seed import — NOT inside `cmd_install`.
- **Rationale (codebase-verified)**: `cmd_install` (`lifecycle.py`) does **not** seed content or activate project plugins; plugin/theme wiring runs in `_instances.py` after install returns, and seed import runs in `_onboard_instance` (`_misc.py`, via `cmd_seed`). Hooking into `cmd_install` would capture a pre-activation, pre-seed DB — failing FR-004's "post-provision state." So the hook belongs at the end of the ensure/onboard sequence.
- **Timing note**: a later config change to the activated plugin set makes the baseline stale → `./sb reset --rebaseline` re-captures it.

## Decision: reserved baseline, stored protected (analysis F3, F5)

- **Decision**: The baseline dir is `runtime/snapshots/<instance>/__install__/`; `@install` is only a user-facing **label** (it is not a valid snapshot name under `_valid_snapshot_name` `[A-Za-z0-9][\w.-]*`, so it can never collide with a real snapshot). It is exempt from `_slug_snapshot_name`/`_valid_snapshot_name` and protected from ordinary overwrite/delete; only `--rebaseline` replaces it.
- **Code location (F3)**: `_valid_snapshot_name`/`_slug_snapshot_name` live in `sandbox/core/_bridge.py` (not `data.py`). Baseline protection must be enforced at **all three** snapshot-mutation sites: `cmd_snapshot`/delete in `data.py`, the bridge `_bridge_handle` (POST `/snapshot`, DELETE `/snapshot/<name>`), and the browser dashboard `_web_do_action` in `_dash.py`.
- **Rationale**: users can't accidentally clobber the reset baseline via CLI, bridge, or dashboard.

## Decision: reset is a fast in-place DB rollback (not recreate)

- **Decision**: `./sb reset` restores the `@install` baseline (db-only); keeps uploads, containers, ports. `recreate_instance` remains the full wipe/rebuild.
- **Rationale**: the everyday "give me a clean DB" verb the user asked for — seconds, not minutes.

## Decision: gating + herd

- **Decision**: reset is destructive → CLI confirm unless `--yes`; MCP `wp_reset` requires `confirm=true`. herd snapshots/reset stay unsupported in v1 (emit the existing herd-unsupported notice; `cmd_snapshot`/`cmd_restore` already `_is_herd_instance`-gate).
- **Rationale**: prevent accidental data loss; consistent with the current snapshot feature's herd limitation.

## Open questions

None — scope, baseline timing, and protection resolved (clarifications + spec).
