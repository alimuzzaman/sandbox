# Quickstart: DB-Only Snapshots & Reset — live verification

Prerequisites: a Docker instance installed by the Sandbox (`./sb ensure`/`init`).
All checks are live (constitution IV). (herd: snapshots/reset unsupported in v1.)

## 1. DB-only snapshot

```
./sb snapshot before-migration --db-only
```
- Snapshot dir has `db.sql` + `META` (`mode=db-only`), **no** `uploads.tgz`; finishes
  faster than a full snapshot.
- `./sb snapshots` shows it with mode `db-only`; it also reports the protected
  `@install` reset baseline separately when one is present.

## 2. DB-only restore leaves uploads alone

- Dirty the DB (e.g. `wp option update blogname "dirty"`), add an upload.
- `./sb restore before-migration --yes` (or answer the interactive default-deny
  prompt with `yes`) → DB rolled back; the upload still present (no error about
  a missing `uploads.tgz`).

## 3. Reset to fresh install

- Confirm the `@install` baseline exists (auto-captured after final instance
  provisioning; a successful onboarding seed becomes part of that baseline).
- Dirty the DB; `./sb reset --yes` (or `wp_reset(confirm=true)`) → site back to its
  post-install state (admin, default content, activated plugins); uploads untouched.
- On an instance with no baseline → `reset` prints actionable guidance (run `--rebaseline`).
- `./sb reset --rebaseline` re-captures the baseline from the current DB.
- `snapshot(name, db_only=true, project_dir=...)` exposes the same fast capture
  through MCP; pass `force=true` to replace an existing snapshot safely.

## 4. Guards

- `./sb reset` without `--yes` prompts before dropping the DB; `wp_reset` without
  `confirm=true` refuses.
- Named restore also requires `--yes` (or an explicit interactive `yes`), and
  bridge restore/reset calls require `confirm=true` before a job is accepted.
- Ordinary `./sb snapshot @install` / `./sb restore @install` and bridge
  snapshot-delete cannot overwrite, restore, or delete the reserved baseline.
- MCP mutation results are safe JSON metadata only; they do not include command
  lines, host paths, credentials, or snapshot contents.

## 5. Dashboard

- In wp-admin (spec-002 snapshot screen): capture with **"DB only"** checked → produces
  a db-only snapshot; click **"Reset to fresh install"** → restores the baseline. Both
  carry explicit confirmation through the nonce/capability-checked bridge and
  complete via the existing out-of-band bridge + polling.

## Source-ready versus live gates

The focused source/tests cover DB-only overwrite, protected baseline listing and
guards, MCP registration/forwarding, and confirmation boundaries. Live proof of
new-instance seed ordering and the wp-admin bridge restart/polling round trip
remains open (T016/T019; T013 tracks the same dashboard restart dependency).
