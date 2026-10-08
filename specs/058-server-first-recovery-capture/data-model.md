# Data Model: Server-First Recovery Capture

All server files live under `<remote $SANDBOX_HOME>/runtime/recovery-captures/`,
directory mode 0700, files 0600, owned by the remote user, written with
`O_EXCL|O_NOFOLLOW` then `fsync` and atomic `rename`. Readers reject symlinks,
non-owner files, group/world bits and `nlink != 1` (pattern from
`sandbox/recovery/postgres_helper.py:101-112`).

```text
runtime/recovery-captures/
├── active.lock                 # held by the one running capture on this remote
└── <slot>/                     # slot = "capture-" + sha256({remote, backup_id})
    ├── request.json            # immutable after start
    ├── state.json              # replaced atomically at each phase change
    ├── job.lock                # held for the job's lifetime
    ├── declarations.json       # control-plane declaration sent at start
    ├── archive.tar             # combined archive, written before the receipt
    ├── receipt.json            # written last; marks completeness
    └── promoted.json           # written by promote after publication
```

## CaptureRequest (`request.json`)

| Field | Type | Rule |
|-------|------|------|
| `schema_version` | int | `1` |
| `slot` | str | matches directory name |
| `remote` | str | registered remote name |
| `backup_id` | str | `_valid_set_id`, ≤128 chars |
| `backup_operation_id` | str | equals `backup_id` |
| `request_id` | str | `recovery-[0-9a-f]{64}` from `HostedRecoveryMaterializer._request_id` |
| `profile_id` / `artifact_id` | str | from the catalog plan |
| `source_binding` | object | `machine_identity`, `revision`, `source_digest` |
| `accepted_at` | float | epoch seconds |
| `declarations_sha256` | str | hash of `declarations.json` |

## CaptureState (`state.json`)

| Field | Type | Rule |
|-------|------|------|
| `state` | enum | `queued`, `running`, `complete`, `failed` |
| `phase` | enum | `preflight` < `inventory` < `dump` < `files` < `verify` < `archive` < `receipt` |
| `accepted_at`, `started_at`, `ended_at` | float/null | monotonic order |
| `reason` | str/null | typed code when `failed` |
| `detail` | object/null | bounded: need/available/shortfall, or mismatch name lists (≤200 each) |

`incomplete` is never stored. It is derived by status (R3) when:
`state ∈ {queued, running}` and `job.lock` is free; or `state = complete` and
`receipt.json` is missing, malformed or disagrees with `archive.tar` size; or
`archive.tar` exists with no `state.json`.

### State transitions

```text
queued ──► running(preflight) ──► running(inventory) ──► … ──► running(receipt) ──► complete
   │              │                                                         
   └──────────────┴──► failed(reason)   [insufficient_space | inventory_mismatch |
                                         dump_failed | files_failed | source_changed |
                                         capture_timeout | archive_failed]
any non-terminal + lock free ──► (derived) incomplete
complete ──► (promoted.json written) complete + promoted
```

Phase only moves forward. `failed` removes the slot's work files except
`request.json`, `state.json` and `declarations.json`.

## CaptureReceipt (`receipt.json`)

| Field | Type | Rule |
|-------|------|------|
| `schema_version` | int | `1` |
| `request_id`, `backup_operation_id`, `source_digest` | str | equal `request.json` |
| `archive_sha256` | str | hex64 of `archive.tar` |
| `archive_size` | int | bytes |
| `members` | list | `[{name, sha256, size}]` for `database.sql`, `wordpress.tar` |
| `declarations_sha256` | str | equals `request.json` |
| `inventory` | object | see TableInventory |
| `started_at`, `completed_at` | float | `completed_at ≥ started_at` |

Size bound: 1 MiB (an inventory of a few thousand tables fits).

## TableInventory

| Field | Type | Rule |
|-------|------|------|
| `taken_at` | float | before the dump starts |
| `tables` | list | `[{name, type: table|view, rows_estimate: int|null}]`, sorted by name |
| `summary` | object | `table_count`, `view_count`, `rows_estimate_total` |
| `dump_matches` | bool | always `true` in a receipt (mismatch fails the capture) |

## PromotionMarker (`promoted.json`)

`{schema_version: 1, request_id, set_id, ciphertext_sha256, promoted_at}`.
Written only after the Drive manifest exists and verifies. Its presence removes
the capture from the retention guard. Nothing deletes the archive.

## PromoteProgress (operator side)

`$SANDBOX_HOME/recovery/promote/<slot>/` (0700): `archive.part`, `progress.json`
`{request_id, archive_sha256, archive_size, received}`, then
`declarations.json`. Removed after successful publication; reported by status
(`local_transfer_bytes`) while present.

## Pending sidecar (operator side)

`$SANDBOX_HOME/recovery/pending/<set>.manifest.json` beside
`<set>.archive.tar.gpg`: the manifest that would have been published. Non-secret.

## Published manifest addition

`provenance.server_capture` (contracts/manifest-provenance.md). No change to
`schema_version` or required manifest fields.

## Retention view (derived, operator side)

`retention_days` (resolved per remote), `age_seconds` (now − `completed_at`),
`retention_exceeded` = complete ∧ ¬promoted ∧ age > bound.
