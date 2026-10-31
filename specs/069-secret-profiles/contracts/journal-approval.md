# Contract: on-disk state (controller-local, non-secret)

Root `R = $SANDBOX_HOME/runtime/secret-ownership/` (dirs 0700, files 0600, owner = euid; wrong mode/owner → refuse).

| Path | Content |
|---|---|
| `R/controller-id` | 32 hex chars, created once with O_EXCL |
| `R/approvals/<approval_id>.json` | Approval (data-model), canonical JSON |
| `R/approvals/<approval_id>.consumed` | single-use marker file for cleanup approval (`b"consumed\n"`) |
| `R/approvals/<approval_id>.token_expiry.json` | TokenExpirySidecar (effective expiry min-bound with worker token, controller, operator, map, destination, authority) |
| `R/evidence/<sha256>.json` | copy of admitted evidence document (metadata only) |
| `R/journals/<profile>/<destination_sha256>/journal.jsonl` | header line then records |
| `R/journals/<profile>/<destination_sha256>/journal.lock` | `flock(LOCK_EX|LOCK_NB)` held for the whole run |
| `R/journals/<profile>/<destination_sha256>/cleanup.json` | CleanupProgressSidecar (durable per-name prepared intent with staged dev/inode/keys hash and removed checkpoint) |
| `R/runs/<run_id>.json` | CompletionRecord |

Journal line: `{"seq":n,"at":<epoch float>,"run_id":…,"approval_id":…,"name":…,"state":…,"reason":…}`; header `{"journal":1,"journal_id":…,"controller_id":…,"profile":…,"destination_sha256":…,"authority_digest":…,"created_at":…}`. Each append: `os.write` full line, `os.fsync(fd)`, `os.fsync(dirfd)` on first create. Torn last line on read → ignored only if it is the final line and lacks `\n`; earlier corruption → `journal_corrupt` refuse.

Approval id: `ap_` + controller-id + `_` + 16 random hex. Run id: `run_` + controller-id + `_` + 16 random hex. The public controller prefix provides owner guidance on a foreign controller; it grants no authority and the protected local approval must still validate. `destination_sha256 = sha256(canonical destination descriptor)`.

## Token Expiry Sidecar (`R/approvals/<approval_id>.token_expiry.json`)
Atomic sidecar written before any worker read/create session begins. Binds:
- `approval_id`, `controller_id`, `operator_uid`, `operator_login`
- `map_sha256`, `destination_sha256`, `authority_digest`
- `token_expires_at`: observed worker token expiry
- `effective_expires_at`: `min(existing_effective, float(token_expires_at), float(approval.expires_at))`
`require_current` verifies the sidecar against current environment and approval. An expired worker token fails closed with `approval_expired` before opening any worker session; corrupt or mismatched sidecar yields `approval_mismatch`.

Before opening any worker session in reconciliation or migration, the persisted `effective_expires_at` is read from this sidecar to clamp the session deadline:
`worker_deadline = min(run_deadline, persisted_effective_expires_at)`.
Active worker operations are bounded by `min(run_deadline, effective_expires_at, token_expires_at)`. Clock checks before `exists` and each `readback_equals` enforce this bound, ensuring expired credentials fail closed with `approval_expired` while preserving all prior journal receipts.

## Cleanup Progress Sidecar (`R/journals/<profile>/<destination_sha256>/cleanup.json`)
Durable per-name progress tracking for source cleanup (R3, R10, R11):
- `cleanup`: 1 (schema version, integer)
- `controller_id`: 32 hex characters matching controller ID
- `profile`: profile name
- `destination_sha256`: 64 hex characters
- `map_sha256`: 64 hex characters
- `authority_digest`: `o1-...` (67 characters)
- `items`: `{name: {name: str, key: str, source_alias: str, state: "prepared"|"removed", approval_id: str, dev: int, ino: int, names_sha256: str, updated_at: float}}`

Protocol:
1. Dry-run (`--confirm` absent) NEVER modifies disk, creates journals, or saves progress. In-memory recovery (`persist=False`) calculates cleanup plan.
2. Confirmed cleanup acquires existing migration Journal lock (`journal.lock`), reloads progress under lock, captures fresh source continuities, validates human approval, and persists validated recovery before executing writes.
3. `prepare(name, key, source_alias, staged, approval_id)`: written and fsynced BEFORE `os.replace` via `writer.remove_keys(before_replace=...)`. Staged continuity is always captured from the staged temporary descriptor (`os.fstat(temporary_fd)`), recording `dev`, `ino`, and `names_sha256`.
4. `before_replace` callback verifies that current continuities across all approved sources (via `read_names`) match expected continuities across previous removals. Deadline guards are checked upon entry, after prepare fsync, after the callback, and crucially after the final current-source nofollow observation immediately before replacement.
5. Final current-source observation and deadline check (R10): `writer.remove_keys` captures fresh nofollow metadata of the target path AFTER the `before_replace` callback, verifying reliability and `same_as(expected)`. Immediately after this final validation and immediately BEFORE destructive `os.replace`, the effective deadline is rechecked (`if deadline is not None and time.time() >= deadline: raise SecretBrokerError("approval_expired", "cleanup authority expired")`). This ensures no unmonitored filesystem I/O boundary crosses expiry into destructive replacement. If expired or drifted, replacement is aborted with zero destructive write.
6. Atomic replacement, staged rename derivation, and baseline capture (R11): `os.replace` replaces the destination path atomically. Immediately after replacement, the writer captures `renamed` metadata from the open staged descriptor (`os.fstat(temporary_fd)`). Permitted continuity is derived strictly from `staged_continuity`, allowing ONLY legitimate `ctime` changes (`replace(staged_continuity, ctime_ns=renamed.ctime_ns)`) while validating all other metadata against the staged write. This `renamed` observation establishes the verified baseline BEFORE directory fsync (`os.fsync(directory_fd)`).
7. Post-fsync verification and continuation adoption (R11): after `os.fsync(directory_fd)`, the writer captures current path metadata via `os.stat(follow_symlinks=False)` and verifies that it is reliable and strictly matches the rename baseline (`same_as(renamed)`). If an external editor modified or replaced the file during directory fsync, the writer raises `source_changed` and NEVER adopts an untracked external editor's state as the continuation for subsequent removals. If replacement succeeded but post-fsync drift is detected, prepared recovery evidence remains safely retained in `cleanup.json`; subsequent removal under the old approval is stopped, and recovery requires a fresh human approval. Atomicity against the unavoidable last-instruction race is not claimed.
8. `checkpoint_removed(name, key, source_alias, dev, ino, approval_id, ...)`: written and fsynced AFTER `os.replace`, directory fsync, and verified baseline comparison.
9. Crash Recovery (`load_or_recover`):
   - Strict parsing: exact keys, qualified IDs, non-negative integer dev/ino, valid mapped `(NAME, source_alias, key)` tuple matching full profile map, finite updated_at.
   - Observations via registered `source_view.read_names` descriptor metadata (not raw `os.stat` following symlinks).
   - If crash occurs after `os.replace` but before checkpoint write, the recovered source file's `(dev, ino)` matches the staged file's inode and remaining keys hash, and key is absent: promotes `prepared` to `removed`.
   - If replacement did not occur and key is present: leaves pending INTENT for safe retry under fresh human approval.
   - If key is unexpectedly missing without matching staged dev/inode/names: fails closed with `cleanup_ineligible`/`authority_mismatch`.
   - Full same-file multiple-removal chains: historical `removed` entries validate key absence without requiring inode match against current file.
   - Reintroduced keys: if a previously removed key is re-introduced into the source file, it is not skipped.

## Completion Record & Dispatch Persistence (`R/runs/<run_id>.json`)
Completion records record run metadata upon operation finish. If writing the completion record encounters an error, the operation attaches `persistence="persistence_failed"` and `completion_record="persistence_failed"` to the payload, preserving all known operation state and revocation guidance without re-throwing or re-executing `_finish`. A previously successful operation returns `provider_failed` with exit 4 and retains its original code in `operation_code`; an existing failure keeps its primary code and exit status.

Similarly, if audit outcome logging fails during CLI ownership dispatch, the dispatch handler preserves the primary operation payload (names, run ID, errors, revocation), marks `audit="audit_unavailable"`, sets `ok=False` and `code="audit_unavailable"` only if the operation had succeeded, and exits with a consistent exit code without leaking backend exception details.

## Pre-Send Migration Guard
Immediately before appending `attempted` and releasing `GO` to the worker for each entry, `before_send` re-reads names on all aliases in `approval.sources`, validates `require_current` across all approval sources and destination/authority digests, and re-checks the monotonic/epoch clock. Any discrepancy aborts with `source_changed` without issuing `GO`/`POST`.

Retention: only `sb secrets ownership retention --confirm` deletes; never a journal with any `attempted`, `acceptance_unknown`, `unresolved` or `conflict` name (FR-052). Audit events reuse `audit.py` schema_version 1 with new operation names `ownership_classify|ownership_approve|ownership_migrate|ownership_cleanup|ownership_run` (names and counts only).

A final torn record is treated conservatively: a surviving attempted state is
unknown, never pending. Truncate only an incomplete final fragment under the
exclusive lock before appending; refuse any other corruption. Write every byte
and fsync successfully before releasing the worker's create request.

Retention preserves any journal with a cleanup progress sidecar, including completed cleanup, because that sidecar is independent recovery evidence. The current retention command never discards cleanup progress based solely on settled migration states or a migration completion record. A future policy must prove cleanup completion explicitly before removing that evidence.
