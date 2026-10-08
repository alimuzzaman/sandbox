# Contract: CLI and MCP surface

All results use the existing envelope from `sandbox/recovery/errors.py:result`:
`{ok, action, remote, status, data, error: {code, message, retryable}}`, with
`redact()` applied. CLI `--json` prints it; human output prints one summary line
plus the fields listed below. Exit status is 1 when `ok` is false.

## `sb recovery capture`

```text
sb recovery capture --remote R --backup-id B --profile P [--profile …] --confirm [--json]
```

MCP: `recovery_capture(remote: str, backup_id: str, profiles: list[str], confirm: bool = False)`.

Preconditions, in this order (first failure wins, nothing starts on the server):

| Check | Error code |
|-------|------------|
| `--confirm` present | `confirmation_required` |
| `--remote`, `--backup-id`, ≥1 `--profile` | `missing_remote`, `missing_backup_id`, `missing_profiles` |
| backup id valid (`_valid_set_id`, ≤128) | `invalid_set_id` |
| profiles supported by the hosted controller | `unsupported_materialization` |
| brokered `SANDBOX_RECOVERY_DB_PASSWORD` present | `missing_database_credential` |
| remote reachable and provisioned | `remote_unavailable` |
| runtime revision state is `match` | `remote_runtime_stale` |
| helper `list`: slot for B exists → replay path, skip the next two rows | – |
| new capture only: no complete unpromoted capture past its bound | `retention_exceeded` (`data.blocking`: backup ids) |
| new capture only, helper `start`: no other active capture | `capture_in_progress` (`data.active_backup_id`) |
| replay, helper `start`: existing slot binding equal, else | `capture_binding_conflict` |
| helper `start`: session can detach | `detach_unsupported` |

Success: `status` = `queued` | `running` | `complete` | `failed` | `incomplete`
(the last three only when the slot already existed), `data` =
`{backup_id, request_id, slot, state, phase, accepted_at, existing: bool}`.
The request id binds the hosted source request and canonical control-plane declaration hash,
so either source or declaration drift under the same backup id returns `capture_binding_conflict`.
Never requires `RECOVERY_PASSPHRASE` or a destination. Never transfers archive bytes.

## `sb recovery status`

```text
sb recovery status --remote R --backup-id B [--json]
```

MCP: `recovery_capture_status(remote: str, backup_id: str)`.

No confirmation, no passphrase, no destination. Does not check the runtime
revision. `ok=false, error.code=capture_not_found` for an unknown backup id.
`data`:

```text
{backup_id, request_id, state, phase, reason, detail,
 accepted_at, started_at, ended_at,
 archive: {sha256, size} | null,
 members: [{name, sha256, size}] | null,
 inventory_summary: {table_count, view_count, rows_estimate_total} | null,
 promoted: bool, promoted_set_id | null,
 retention_days, retention_exceeded: bool,
 residue_bytes | null, local_transfer_bytes | null}
```

`retention_exceeded` and `age` are the only fields that change for a terminal
capture, and only across the bound; SC-002 tests run inside the bound.
Human status output prints only allowlisted preflight byte counts and bounded inventory mismatch
name lists from `detail`; it never renders arbitrary helper diagnostics.

## `sb recovery list` (extended)

`--remote R` adds `data.server_captures`: a list of
`{backup_id, state, age_seconds, archive_size, promoted, retention_exceeded, retirable}`
and `data.legacy_server_archives`: `[{name, size}]` from
`runtime/recovery-controller/*.tar`. When Drive is not configured and `--remote`
is given, the call succeeds with `data.drive = {"configured": false}` and empty
Drive categories. Without `--remote` and without Drive, behavior is unchanged
(`recovery_not_configured`).

## `sb recovery promote`

```text
sb recovery promote --remote R --backup-id B --confirm [--destination D] [--json]
```

MCP: `recovery_promote(remote: str, backup_id: str, destination: str | None = None, confirm: bool = False)`.

The passphrase comes only from inherited `RECOVERY_PASSPHRASE`. Order:

| Check | Error code |
|-------|------------|
| `--confirm` | `confirmation_required` |
| passphrase present | `missing_passphrase` |
| destination present (`--destination` or env) | `recovery_not_configured` |
| local pending ciphertext for B and Drive manifest for B with the same `ciphertext_sha256` → the upload already finished; local leftovers removed, nothing uploaded | success, `status=already_published`, `data.from_pending` |
| local pending ciphertext for B and Drive manifest for B with another `ciphertext_sha256` → refused before any transfer, pending files kept | `set_id_conflict` |
| local pending ciphertext for B, no Drive manifest → finish it (FR-029) | `passphrase_not_current`, `pending_artifact_invalid`, `drive_upload_failed`, `drive_verification_failed` |
| Drive manifest for B exists and traces to this capture | success, `status=already_published` |
| Drive manifest for B from another capture | `set_id_conflict` |
| Drive holds ciphertext for B without manifest and no local pending | `incomplete_remote_set` |
| capture state `complete` with valid receipt | `capture_not_complete` |
| operator free space ≥ 3 × archive size − bytes already received (transfer copy, set archive, ciphertext) | `insufficient_space` |
| chunked transfer and final hash/size match | `transfer_mismatch`, `remote_unavailable` |
| declaration hash matches receipt | `transfer_mismatch` |
| publish (encrypt, upload, verify, manifest last) | existing 023 codes |

Success: `status=published`, `data = {set_id, manifest, request_id, archive_sha256, resumed_from_bytes}`.
After success the server slot gets `promoted.json`; the archive is kept.
A transfer whose bytes do not match the receipt hash sets the capture `failed`
with reason `integrity_mismatch` (then retirable, FR-034).

## `sb recovery retention` (server captures, FR-034)

```text
sb recovery retention --remote R [--json]                              # plan, read-only
sb recovery retention --remote R --backup-id B --confirm [--json]      # retire one capture
```

MCP: `recovery_retention(remote, backup_id=None, confirm=False)` with the same rules.
The plan lists every server capture with `retirable`. Retire order:

| Check | Error code |
|-------|------------|
| `--confirm` and `--backup-id` | `confirmation_required` |
| capture exists | `capture_not_found` |
| state `complete` unpromoted, `queued` or `running` | `not_retirable` |
| server re-read: state, valid receipt digest, current archive sha256 and size equal the reviewed candidate | `retire_candidate_changed` |

Success: `status=retired`, `data = {backup_id, retired_at, removed_bytes, previous_state}`.
The record stays with `state: retired`. Without `--remote` the existing
Drive-set retention review is unchanged.

## MCP group

`mcp/wp-server/tools/recovery.py` adds the three tools; `mcp/wp-server/tools/manifest.py`
`BUILTIN_TOOL_NAMES["recovery"]` lists them; `tests/test_mcp.py` schema snapshot
is updated. The group stays opt-in (not in `DEFAULT_MCP_GROUPS`).
