# Contract: Snapshot Bridge API (on the `sb web` server)

Scoped routes the mu-plugin calls. All under `/api/instance/<inst>/…`. Every request MUST
carry `Authorization: Bearer <bridge_token>`; the server resolves `<inst>` and 403s unless
the token matches that instance's `bridge_token`. The server NEVER accepts an arbitrary `sb`
command — only the verbs below, mapped to fixed `sb` invocations for `<inst>`.

Common error shapes:

- `401/403 {"ok": false, "error": "unauthorized"}` — missing/invalid token.
- `404 {"ok": false, "error": "unknown instance"}` — `<inst>` not registered.
- `409 {"ok": false, "error": "unsupported", "reason": "herd"}` — herd instance (v1).
- `400 {"ok": false, "error": "<validation message>"}` — bad name, etc.

## POST `/api/instance/<inst>/snapshot`

Take a snapshot (out-of-band).

- Request: `{"name": "<optional; ^[\\w.-]+$>", "force": false}`
- Behavior: validates name (blank → `snap-YYYYMMDD-HHMMSS`); if exists and `!force` → 409
  `{"error":"exists"}`; else spawns `sb snapshot <name> --instance <inst> [--force]` detached.
- Response: `202 {"ok": true, "job_id": "<id>", "name": "<name>"}`

## POST `/api/instance/<inst>/restore`

Restore a snapshot (out-of-band; required because restore resets the serving DB).

- Request: `{"name": "<existing snapshot>"}`
- Behavior: 404 if the snapshot is absent; else spawns `sb restore <name> --instance <inst>`
  detached.
- Response: `202 {"ok": true, "job_id": "<id>", "name": "<name>"}`

## GET `/api/instance/<inst>/snapshots`

List snapshots (synchronous; mirrors `sb snapshots`).

- Response: `200 {"ok": true, "snapshots": [{"name","size_kb","meta"}, …]}`
  (the server may shell `sb snapshots` or read `runtime/snapshots/<inst>/` directly).

## DELETE `/api/instance/<inst>/snapshot/<name>`

Delete one snapshot.

- Behavior: 404 if absent; else remove `runtime/snapshots/<inst>/<name>/`.
- Response: `200 {"ok": true}`

## GET `/api/instance/<inst>/job/<job_id>`

Poll an async job (snapshot/restore).

- Response: `200 {"ok": true, "status": "queued|running|succeeded|failed", "op": "...",
  "name": "...", "detail": "<last message or error>"}`
- The mu-plugin polls until a terminal status, then surfaces success/failure to the admin.

## CLI contract (host side)

No new public `sb` subcommands are required; the bridge reuses `sb snapshot/restore/
snapshots`. Internal additions (not user-facing): minting/persisting
`instances.<name>.bridge_token`, writing the job file, and the `_write_snapshot_muplugin`
generator. Any new internal flag (e.g. `--json` on `snapshots` if not present) is additive
and backward-compatible.
