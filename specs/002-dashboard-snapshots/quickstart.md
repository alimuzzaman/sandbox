# Quickstart: Validate Dashboard Snapshots

Live validation that the feature works end-to-end (constitution Principle IV). Run against a
Docker-backed registered instance (e.g. one of the running `templately-*` instances). All
checks are observable on the live stack.

## Prerequisites

- A running Docker-backed instance with the snapshot mu-plugin provisioned
  (`sb up`/`ensure` — which also auto-starts `sb web`).
- Admin access to that instance's wp-admin (the autologin link works).

## Scenario 1 — Take a snapshot from wp-admin (US1)

1. Open wp-admin → Tools → **Sandbox Snapshots**.
2. Enter name `t1`, click **Take snapshot**; wait for the job to report success.
3. Verify cross-visibility on the host: `sb snapshots --instance <inst>` lists `t1`, and
   `runtime/snapshots/<inst>/t1/` contains `db.sql` + `uploads.tgz` + `META`.
   **Expected**: snapshot identical to a CLI-made one (FR-002, SC-001).

## Scenario 2 — Restore from wp-admin (US2)

1. Mutate state (e.g. trash a post, or `sb wp --instance <inst> option update blogname X`).
2. In Sandbox Snapshots, **Restore** `t1`; confirm the destructive prompt; wait for the job.
3. **Expected**: site returns to the captured state (post back / blogname restored); the
   admin request did not error mid-restore (out-of-band), and the result is reported
   (SC-002). Tables created after the snapshot are gone (point-in-time, matches CLI).

## Scenario 3 — List & delete (US3)

1. Take `t2`; confirm both `t1` and `t2` appear in the dashboard list with sizes, matching
   `sb snapshots`.
2. **Delete** `t1` from the dashboard; confirm; verify it disappears from both the dashboard
   and `sb snapshots`, and `runtime/snapshots/<inst>/t1/` is gone.

## Scenario 4 — Auth is enforced (SC-003)

1. From inside the WP container, `curl` the bridge route WITHOUT the token →
   **Expected** 401/403.
2. `curl` with a WRONG token, or the correct token but a DIFFERENT `<inst>` →
   **Expected** 403. Only the correct per-instance token succeeds.
3. In wp-admin, a POST without a valid nonce / as a non-admin → **Expected** rejected.

## Scenario 5 — Herd shows unsupported (SC-005)

1. On a herd instance, open Sandbox Snapshots → **Expected**: a clear "not supported on herd"
   notice; actions disabled (no opaque failure).

## Scenario 6 — Sandbox-only safety (SC-004)

1. Confirm `00-sandbox-snapshots.php` no-ops when the `SANDBOX_BRIDGE_*` constants are absent
   (it must never load/act outside a sandbox instance).

## References

- Routes & payloads: [contracts/bridge-api.md](./contracts/bridge-api.md)
- Entities & token/job storage: [data-model.md](./data-model.md)
- Decisions & rationale: [research.md](./research.md)
