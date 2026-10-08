# Research: Server-First Recovery Capture and Later Drive Promotion

Each decision cites the code it is grounded in. Paths are repository-relative.

## R1. How server-side code reaches the remote

**Decision**: A new stdlib-only helper program, `sandbox/recovery/server_capture_helper.py`,
is read locally and sent as `python3 -c <source> <op> <args...>` through
`sandbox.core._remote.ssh_process`, exactly as the PostgreSQL path sends
`postgres_helper.py` (`sandbox/transports/remote_postgres_recovery.py:216,226`).

**Rationale**: This is the reviewed delivery path already used by both hosted
recovery controllers (`sandbox/transports/remote_recovery.py:804-808` sends its
capture program the same way). It is not `sb remote ssh` (whose 4096-character
cap lives in `sandbox/commands/remote.py:291`) and not `job-start`, which the
PRD excludes. The helper version is pinned by the local checkout, and capture
start still refuses on a stale remote runtime revision.

**Alternatives rejected**:
- Installing the helper with `sb remote service migrate` and running it from
  `$HOME/sandbox/sb-src`: couples recovery to the MCP unit installer in
  `sandbox/core/_remote.py:4406-4595`, which this feature must not modify.
- Remote durable jobs (`sandbox/transports/remote_jobs.py`): excluded by the PRD
  non-goal ("running capture through ... `job-start`").
- An authenticated control-plane route: none exists for recovery today; adding
  one widens the remote service surface owned by another thread.

**Constraint**: keep the helper under 64 KiB so the argv stays well inside the
Linux 128 KiB single-argument limit (the PostgreSQL helper is 84,756 bytes).

## R2. Detaching the capture so start returns within 30 seconds

**Decision**: The helper's `start` op reads the database password from stdin,
validates and writes the request and a `queued` state, takes the per-remote
`active.lock` and the per-capture `job.lock`, then double-forks with `setsid`,
closes stdio and lets the grandchild run the capture holding both locks (lock
file descriptions survive `fork`). The SSH call returns when the first parent
exits. The password exists only in the job's memory; it never touches disk.

**Rationale**: Keeps the brokered stdin handoff that exists today
(`remote_recovery.py:760-764,807-808`). `setsid` detaches from the SSH session
so closing the connection does not hang up the job.

**Risk and check**: hosts with logind `KillUserProcesses=yes` kill session
processes at logout. The `start` op checks `loginctl show-user` (when
available) and refuses with `detach_unsupported` instead of starting a job that
would die. Live verification (quickstart step 3) confirms the job survives
disconnect on the target remote.

**Alternatives rejected**: `systemd-run --user` cannot receive the password on
stdin without `--pipe`, which ties the unit to the caller; `nohup` alone does
not leave the session.

## R3. Liveness and the `incomplete` state

**Decision**: `status` derives liveness by attempting a non-blocking exclusive
`flock` on `job.lock`. If the lock is free and `state.json` is not terminal, the
capture is `incomplete`. Status never writes, so 3 repeated calls return
identical data. An archive with a missing or malformed receipt is `incomplete`
(spec FR-011, as documented in `docs/recovery.md`: "an interruption between
those writes is an incomplete capture").

**Rationale**: The lock is taken before the fork and held by the job for its
whole life, so there is no window where a live job looks dead. PIDs are not
used: they are reused and do not survive a reboot meaningfully.

## R4. Identity, replay and binding conflicts

**Decision**: Slot key = `capture-` + sha256 of `{"schema_version":1,"remote":R,"backup_id":B}`;
directory `<remote $SANDBOX_HOME>/runtime/recovery-captures/<slot>/`. The request id
is a deterministic hash of `HostedRecoveryMaterializer._request_id(remote, artifact, binding, backup_id)`
(`sandbox/recovery/hosted.py:409-438`) and the canonical control-plane declaration hash.
`request.json` stores the resulting request id, backup operation id, profile, artifact id,
source binding and declaration hash. A start whose request id equals the stored one returns
the existing state; any source or declaration drift under the same backup id returns
`capture_binding_conflict` (the existing script exits 8 on the same condition,
`remote_recovery.py:736-737`).

**Rationale**: matches clarification Q5 and preserves the hosted request derivation as the
source-binding component while preventing a changed control-plane declaration from replaying
an old capture.

## R5. Table inventory and the dump comparison

**Decision**: Phase `inventory` runs, inside the database container,
`mariadb -N -B -e "SELECT TABLE_NAME, TABLE_TYPE, TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()"`
with the same brokered `MYSQL_PWD` environment as the dump, immediately before
`mariadb-dump --single-transaction` (`remote_recovery.py:766-769`). Rows with
`TABLE_TYPE` in {`BASE TABLE`, `VIEW`, `SYSTEM VERSIONED`} are kept; the type is
normalised to `table` or `view`. After the dump, a streaming line scan of
`database.sql` collects names from `CREATE TABLE \`name\`` and from
`VIEW \`name\` AS` in the final view definitions. Set difference in either
direction ends the capture `failed` with `inventory_mismatch` and both lists of
names (bounded to 200 names each).

**Rationale**: `information_schema.TABLES.TABLE_ROWS` is the row-count estimate
the PRD asks for. Line scanning keeps memory constant. `mariadb-dump` writes a
temporary `CREATE TABLE` stand-in for each view and later its final
`CREATE ... VIEW`; collecting both forms into one set and comparing names (not
statement counts) handles that.

## R6. Free-space estimate (phase `preflight`)

**Decision**: estimate = `2 × (D + F) × 1.10`, where D = `SUM(DATA_LENGTH + INDEX_LENGTH)`
for the schema and F = `du -sb /var/www/html` inside the WordPress container.
Free space = `os.statvfs(capture root)`, `f_bavail × f_frsize`. Insufficient →
`failed: insufficient_space` with `need_bytes`, `available_bytes`, `shortfall_bytes`.

**Rationale**: peak on-disk use is the work copies of the dump (D) and tree tar
(F) plus the combined archive (D + F). Today's script also writes a second full
`wordpress.check.tar` (`remote_recovery.py:779-789`); this design hashes the
second tar stream through a pipe instead of writing it, removing that third
copy. The 10% margin covers dump text overhead relative to InnoDB sizes.

## R7. Memory bound on the server (SC-007)

**Decision**: every hash is computed with 1 MiB reads; the consistency check
pipes the second `tar` stream into a hasher; the archive is built with
`tarfile.add` (file streaming); nothing is written to stdout except small JSON.

**Rationale**: the current script reads whole files into memory twice
(`check_tar.read_bytes()`, `wordpress_tar.read_bytes()` at `remote_recovery.py:788`
and `output.read_bytes()` at `:803`). The PRD's risk section records this.

## R8. Chunked, resumable transfer for promote

**Decision**: Op `read-chunk <slot> <offset> <length>` writes one JSON header line
(`offset`, `length`, `sha256`) followed by exactly `length` bytes. Chunk size is
16 MiB. The operator side appends to
`$SANDBOX_HOME/recovery/promote/<slot>/archive.part` (0600, directory 0700) and
records `progress.json` {request_id, archive_sha256, archive_size, received}.
A resume continues from `received` only when request id and archive hash match;
otherwise the partial file is discarded. After the last chunk the whole file is
hashed and compared with the receipt (`transfer_mismatch` on any difference).

**Rationale**: `ssh_process` returns stdout in memory
(`sandbox/core/_remote.py:853-874`), so the chunk size is the memory bound;
16 MiB keeps the promote process far under 256 MB and makes a 2 GB archive 128
calls over the multiplexed control socket. No existing helper offers ranged
remote-to-local transfer (`scp_run` has no resume).

## R9. Encryption and publication

**Decision**: Promote calls the existing `StagingCaptureCoordinator.publish_files`
(`sandbox/recovery/capture.py:475-548`) with two artifacts placed under the owned
`materialized` root: `amarsonar-bangla-prod/amarsonar-bangla.tar` (the transferred
server archive) and `control-plane/control-plane-declarations.json`. Provenance
gains a `server_capture` object (see contracts/manifest-provenance.md).

**Rationale**: reuses streaming GnuPG file encryption, ciphertext verification,
archive-first upload, Drive verification and manifest-last write unchanged
(023 FR-013/FR-014). `verify_manifest` (`sandbox/recovery/restore.py:54-99`)
does not constrain provenance, so restore and listing keep working without a
manifest schema bump. The artifact record for the server archive carries the
same SHA-256 as the receipt's archive hash, which is how SC-006 is checked;
the set's own `plaintext_sha256` covers the wrapping set archive.

## R10. Control-plane declaration without recapture

**Decision**: At capture start the operator side builds the control-plane
declaration exactly as today (`remote_recovery.py:846-863`, extracted into a
shared function) and sends it to the server after the password on stdin; the job
stores it as `declarations.json` and records its hash in the receipt. Promote
fetches it with `read-declaration` and checks the hash.

**Rationale**: the declaration then describes the capture moment, not the
promote moment, and promote does no capture of any kind.

## R11. Locally pending ciphertext

**Decision**: `publish_files` computes the full manifest before uploading and,
when it preserves a pending ciphertext (`capture.py:543-546,550-564`), also writes
`<set>.manifest.json` (0600) beside it. Promote with a pending entry for the
backup id: checks the ciphertext hash against the sidecar, decrypts once to an
owner-only temporary file to prove the current passphrase works, uploads with
`put_file` (rclone `--immutable` accepts an identical object already present),
verifies, writes the manifest last, then removes both pending files. A pending
ciphertext without a sidecar (created before this feature) is decrypted into the
owned staging root; artifact records and profiles are derived from its members,
profile bindings from the catalog, and provenance records `"pending_recovered": true`.

## R12. Retention bound configuration

**Decision**: `recovery.server_capture_retention_days` (default 7) with a per-remote
override `recovery.remotes.<remote>.server_capture_retention_days` in the merged
Sandbox config (`sandbox.yml` → `$SANDBOX_HOME/sandbox.local.yml`), read through
the existing config loader. Valid range 1–365; anything else is
`invalid_retention_policy`. The bound is evaluated operator-side from the
receipt's `completed_at`. The start guard is evaluated operator-side from a
`list` read immediately before `start` is sent; the helper's `active.lock` still
prevents two captures running at once if two operators race.

**Rationale**: the remote registration record is owned by `sandbox/core/_remote.py`,
which this feature must not modify; machine-level overrides belong in
`sandbox.local.yml` per the repository file-placement table.

## R13. Error code for a stale runtime

**Decision**: the new capture path reports `remote_runtime_stale` (PRD name) where
the existing controller raises `remote_revision_mismatch` (`remote_recovery.py:617-618`).
The mapping is done in the new transport; the old path keeps its code (FR-038).

## R14. Live proof

The only supported hosted profile is a production site. Unit and contract tests
use injected fakes (the pattern in `tests/test_remote_recovery.py`). A live run
needs explicit approval and the brokered credential; it is listed as a pending
decision rather than assumed.
