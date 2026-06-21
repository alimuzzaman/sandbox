# Phase 1 Data Model: Dashboard Snapshots

Entities involved in the dashboard snapshot feature. Storage is unchanged from the CLI;
the new state is the per-instance bridge token and the async job records.

## Snapshot (existing — unchanged)

A named point-in-time capture of one instance.

- **name**: string, `^[\w.-]+$` (CLI rule). Blank from UI → generated `snap-YYYYMMDD-HHMMSS`.
- **instance**: owning instance name.
- **location**: `runtime/snapshots/<instance>/<name>/` (host-side, gitignored).
- **contents**: `db.sql` (`wp db export --add-drop-table`), `uploads.tgz` (tar of
  `wp-content/uploads`, symlinks preserved), `META` (`project=…\ninstance=…`).
- **size**: sum of file sizes (shown in list).
- Source of truth for listing in BOTH the CLI (`cmd_snapshots`) and the dashboard.

## Bridge token (new)

Per-instance shared secret authorizing bridge calls.

- **value**: random, high-entropy string (mint like the autologin token).
- **storage**: `sandbox.local.yml` → `instances.<name>.bridge_token` (gitignored).
- **delivery**: injected into the mu-plugin as `SANDBOX_BRIDGE_TOKEN`.
- **lifecycle**: minted on first provision; regenerated on recreate; rotated if deleted.
- **validation**: the bridge server compares the presented Bearer token against the resolved
  instance's stored token (constant-time compare); mismatch → 403.

## Snapshot mu-plugin (new, generated)

`00-sandbox-snapshots.php`, written into `runtime/wp-<instance>/wp-content/mu-plugins/`.

- **injected constants**: `SANDBOX_BRIDGE_URL`, `SANDBOX_BRIDGE_TOKEN`, `SANDBOX_INSTANCE`.
- **guard**: loads/acts only when the sandbox constants are present (sandbox-only).
- **admin screen**: Tools → "Sandbox Snapshots" (`sandbox_*`-prefixed page slug, handles,
  options). Requires `manage_options` + a `sandbox_snapshots` nonce on every action.
- **role**: renders UI; calls the bridge via `wp_remote_post`/`wp_remote_get`; polls jobs.

## Bridge route (new — see contracts/bridge-api.md)

Scoped HTTP routes on the `sb web` server under `/api/instance/<inst>/…`.

- **verbs**: `POST snapshot`, `POST restore`, `GET snapshots`, `DELETE snapshot/<name>`.
- **auth**: `Authorization: Bearer <bridge_token>` matched to `<inst>`.
- **mapping**: snapshot→`sb snapshot`, restore→`sb restore`, snapshots→`sb snapshots`
  (JSON), delete→remove the snapshot dir. NO arbitrary `sb` passthrough.

## Bridge job (new — async)

Tracks an out-of-band capture/restore.

- **job_id**: opaque id.
- **instance**, **op** (`snapshot`|`restore`), **name** (snapshot name).
- **status**: `queued` → `running` → `succeeded` | `failed`.
- **detail**: last message / error.
- **storage**: a job file under `runtime/` (e.g. `runtime/bridge-jobs/<instance>/<job_id>.json`),
  gitignored; written by the detached `sb` process, read by the status route.
- **transitions**: created `queued` on request; `running` when the process starts;
  terminal `succeeded`/`failed` on exit. Polled by the mu-plugin until terminal.

## Relationships

- One **instance** ↔ one **bridge token** ↔ many **snapshots** ↔ many **bridge jobs**.
- The **mu-plugin** belongs to exactly one instance (constants pin it).
