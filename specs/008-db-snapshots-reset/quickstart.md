# Quickstart: DB-Only Snapshots & Reset — live verification

Prerequisites: a Docker instance installed by the Sandbox (`./sb ensure`/`init`).
All checks are live (constitution IV). (herd: snapshots/reset unsupported in v1.)

## 1. DB-only snapshot

```
./sb snapshot before-migration --db-only
```
- Snapshot dir has `db.sql` + `META` (`mode=db-only`), **no** `uploads.tgz`; finishes
  faster than a full snapshot.
- `./sb snapshots` shows it with mode `db-only`.

## 2. DB-only restore leaves uploads alone

- Dirty the DB (e.g. `wp option update blogname "dirty"`), add an upload.
- `./sb restore before-migration` → DB rolled back; the upload still present (no error
  about a missing `uploads.tgz`).

## 3. Reset to fresh install

- Confirm the `@install` baseline exists (auto-captured at install).
- Dirty the DB; `./sb reset --yes` (or `wp_reset(confirm=true)`) → site back to its
  post-install state (admin, default content, activated plugins); uploads untouched.
- On an instance with no baseline → `reset` prints actionable guidance (run `--rebaseline`).
- `./sb reset --rebaseline` re-captures the baseline from the current DB.

## 4. Guards

- `./sb reset` without `--yes` prompts before dropping the DB; `wp_reset` without
  `confirm=true` refuses.
- Ordinary `./sb snapshot @install` / snapshot-delete cannot overwrite/delete the
  reserved baseline.

## 5. Dashboard

- In wp-admin (spec-002 snapshot screen): capture with **"DB only"** checked → produces
  a db-only snapshot; click **"Reset to fresh install"** → restores the baseline. Both
  complete via the existing out-of-band bridge + polling.
