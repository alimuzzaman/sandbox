# Feature Specification: Server-First Recovery Capture and Later Drive Promotion

**Feature Branch**: `058-server-first-recovery-capture`

**Created**: 2026-10-08

**Status**: Draft

**Input**: Ready PRD `specs/058-server-first-recovery-capture/prd.md` (feedback 9e54f17b: manual recovery lacks server-first capture and script submission; need a unique server-first capture job with retained status, a complete table inventory, archive hashes, and later encrypted Drive promotion without recapture).

**Amends**: spec 023 (scoped recovery profiles) FR-012 (plaintext staging), FR-014 (archive-first publication) and FR-016 (locally pending). Spec 023's encryption, ciphertext-only Drive, manifest-last and no-raw-SSH (FR-026) rules continue to hold.

## Clarifications

### Session 2026-10-08

Product decisions were delegated to an independent reviewer who was not
available in this session. Each answer below follows from the PRD, spec 023,
the current code or the constitution, and cites that evidence. The open product
choice (FR-034) was then decided by an independent Fable reviewer.

- Q: Is the free-space refusal made synchronously by capture start, or by the server job before it dumps? → A: By the server job, as its first phase (`preflight`), before any dump; the capture ends `failed` with `insufficient_space` and the need, available and shortfall. Evidence: the PRD requires both a 30-second start (Acceptance Outcomes) and a refusal "before dumping"; sizing the WordPress tree can exceed 30 seconds, while the PRD only requires the refusal to precede the dump.
- Q: May two captures run at once on the same remote? → A: No. One active capture per remote; a second start refuses with `capture_in_progress`. Evidence: spec 023 FR-022 requires a single-run lock and no overlap; the free-space check (FR-006) is meaningless if two captures share the space; the current controller already serializes per operation with an exclusive lock (`sandbox/transports/remote_recovery.py`).
- Q: Which server captures count toward the `retention_exceeded` block? → A: Only complete, unpromoted captures made by this feature. Failed captures hold no plaintext (FR-012); interrupted residue and archives left by the earlier one-shot `recovery create --remote` path are listed with their size but do not block. Evidence: the PRD bounds "an unpromoted archive"; docs/recovery.md states old request-named archives "remain retained and are never adopted as a fresh capture", so they are not captures of this feature and cannot be promoted.
- Q: What is the capture's runtime bound? → A: 3600 seconds for the whole job, with each dump/archive step keeping its existing 1800-second bound; exceeding either ends `failed` with `capture_timeout`. Evidence: the current controller bounds the whole capture call at 3600 seconds and each `mariadb-dump`/`tar` step at 1800 seconds (`sandbox/transports/remote_recovery.py`); the PRD describes today's capture as "up to an hour".
- Q: What key identifies a capture for start, status and promote? → A: The remote plus the backup id: one capture per backup id per remote. The request id (derived from backup id, remote, declaration and source binding, as today) is stored with it, and a differing binding is a `capture_binding_conflict`. Evidence: the PRD states "the capture identity derives from the backup id and target"; `HostedRecoveryMaterializer._request_id` already derives the request id from backup operation id, remote, binding and declaration.

- Q: Does this feature enable confirmed retirement of a server capture, or does retirement stay review-only? → A: Confirmed retire, one capture per call, only for `promoted`, `failed` or `interrupted` captures; a complete unpromoted capture stays review-only and promote is its only exit; an integrity-refused promote sets the capture `failed` (`integrity_mismatch`) so it can be retired. Evidence: a complete unpromoted capture is the sole copy, and spec 023 FR-023 forbids deleting the only set; promoted, failed and interrupted captures hold nothing not already safe or already lost. Decided by Fable (delegated by user), 2026-10-08.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start a capture on the server and walk away (Priority: P1)

A recovery operator needs a fresh backup of a supported hosted production site.
They start a capture for a new backup id with explicit confirmation. The command
returns promptly with the capture identity while the capture runs on the server.
No Drive destination or recovery passphrase is needed, and no archive bytes
cross the operator's connection during the capture. The finished capture records
a complete table inventory and hashes for the archive and each member.

**Why this priority**: Without a server-side capture there is nothing to inspect
or promote. This is the slice that removes the hour-long blocking call and the
requirement to have Drive and the passphrase ready up front.

**Independent Test**: Against a registered remote with a supported profile and
neither `RECOVERY_PASSPHRASE` nor `RECOVERY_RCLONE_DESTINATION` set, start a
capture; confirm it returns the identity within 30 seconds, that no archive
bytes are transferred, and that the retained server record later holds an
archive with its hashes and table inventory.

**Acceptance Scenarios**:

1. **Given** a supported hosted profile on a registered remote and no Drive or passphrase configured, **When** the operator starts a capture with a new backup id and confirmation, **Then** the command returns within 30 seconds with the capture identity and state `queued` or `running`, and the capture continues on the server.
2. **Given** a capture start without confirmation, **When** the operator runs it, **Then** it refuses with `confirmation_required` and nothing starts on the server.
3. **Given** a backup id that already has a capture with the same source binding, **When** the operator starts a capture for it again, **Then** the existing identity and current state are returned and no second capture runs.
4. **Given** a backup id that already has a capture whose source binding (profile declaration, remote identity, runtime revision) differs, **When** the operator starts a capture for it, **Then** it refuses with `capture_binding_conflict` and never overwrites the existing capture.
5. **Given** a remote whose installed runtime revision mismatches, **When** the operator starts a capture, **Then** it refuses with `remote_runtime_stale` before any server work.
6. **Given** a remote whose free space in the capture location is below the estimated capture size, **When** the operator starts a capture, **Then** the capture's first phase ends it `failed` with `insufficient_space`, naming the estimated need, the available space and the shortfall, before any dump begins.
7. **Given** the source database's table set at dump start differs from the table set present in the finished dump, **When** the capture runs, **Then** it ends `failed` with reason `inventory_mismatch` and the differing table names, and no archive is reported complete.
8. **Given** a capture that completes, **Then** its receipt lists every base table and view the source database reported at dump start with a row-count estimate per table, the archive hash, and a hash and size for each archive member.

---

### User Story 2 - Check capture status at any time (Priority: P1)

The operator asks for the status of a backup id during and after the capture,
including after an interruption, without any Drive or passphrase configuration.
The answer is the capture's retained state and does not change on repeat once the
capture has ended.

**Why this priority**: A detached capture is unusable unless its state can be
read back reliably; status also proves completeness before promotion.

**Independent Test**: Start a capture, poll status during and after it with
the passphrase and destination unset, then kill a second capture mid-run and
check that it reports `incomplete`.

**Acceptance Scenarios**:

1. **Given** a running capture, **When** the operator asks for status repeatedly, **Then** each answer shows `running` with its current phase and start time, and the phase never moves back to an earlier one.
2. **Given** a capture in a terminal state (`complete`, `failed`, `incomplete`), **When** status is requested 3 times, **Then** all 3 answers are identical.
3. **Given** a complete capture, **When** status is requested, **Then** it shows the start and end times, the archive hash and size, each member's hash and size, and the table inventory summary (table and view counts, total estimated rows).
4. **Given** a capture whose process was killed mid-run, **When** status is requested, **Then** it reports `incomplete`, never `complete` or `running`.
5. **Given** an archive whose receipt is missing or malformed, **When** status is requested, **Then** it reports `incomplete`.
6. **Given** a backup id with no capture on that remote, **When** status is requested, **Then** it returns the distinct result `capture_not_found`.
7. **Given** a remote whose installed runtime revision mismatches, **When** status is requested, **Then** it still answers.

---

### User Story 3 - Promote a completed capture to Drive later (Priority: P2)

Later, with the Drive destination and passphrase now configured, the operator
promotes the completed capture with confirmation. The archive is transferred
through the Sandbox remote transport in bounded chunks, re-verified against the
receipt, encrypted on the operator side and uploaded archive-first and
manifest-last. The published manifest ties the Drive set to the server capture
without decryption. Nothing is recaptured.

**Why this priority**: Publication is the second half of the backup, but it
depends on a complete capture (US1) and its status (US2).

**Independent Test**: Promote a complete capture into a test Drive destination;
confirm the published set's plaintext hash equals the receipt's archive hash, the
manifest carries the capture's request id, backup operation id, archive hash and
member hashes, `recovery list` shows it complete, and no capture ran.

**Acceptance Scenarios**:

1. **Given** a complete capture and a configured destination and passphrase, **When** the operator promotes its backup id with confirmation, **Then** the archive is transferred in bounded chunks, its hash is re-verified against the receipt, it is encrypted on the operator side, the ciphertext is uploaded and verified before the manifest is written last, and `recovery list` shows the set complete.
2. **Given** a promote interrupted during transfer, **When** the operator promotes the same backup id again, **Then** the transfer resumes from the bytes already received rather than starting over.
3. **Given** a capture that is not `complete`, **When** the operator promotes it, **Then** it refuses with `capture_not_complete` and publishes nothing.
4. **Given** transferred bytes whose hash does not match the receipt, **When** promote verifies them, **Then** it refuses with `transfer_mismatch`, discards the transferred copy and publishes nothing.
5. **Given** no passphrase or no destination, **When** the operator promotes, **Then** it refuses with `missing_passphrase` or `recovery_not_configured` before any transfer.
6. **Given** a backup id already published from this capture, **When** the operator promotes it again, **Then** it reports the existing published set and uploads nothing.
7. **Given** a promote without confirmation, **Then** it refuses with `confirmation_required` and transfers nothing.
8. **Given** a complete capture past its retention bound, **When** the operator promotes it, **Then** promotion proceeds normally.

---

### User Story 4 - Finish a locally pending ciphertext (Priority: P3)

An earlier publication left an encrypted artifact `locally_pending` after a
failed upload. The operator runs promote for that backup id and the pending
ciphertext is uploaded archive-first and manifest-last without recapture or
re-encryption.

**Why this priority**: It gives an existing stuck state an exit, but it affects
only runs that already failed.

**Independent Test**: Seed a pending ciphertext for a backup id, run promote with
confirmation, and confirm the set is published and listed complete and the
pending entry is gone.

**Acceptance Scenarios**:

1. **Given** a `locally_pending` ciphertext for a backup id and a configured destination and passphrase, **When** the operator promotes that backup id with confirmation, **Then** the ciphertext is checked to decrypt with the current passphrase, uploaded and verified, the manifest is written last, and the entry leaves the locally pending list.
2. **Given** a pending ciphertext that does not decrypt with the current passphrase, **When** promote runs, **Then** it refuses with `passphrase_not_current` and leaves the pending ciphertext in place.

---

### User Story 5 - See and bound server-held captures (Priority: P3)

The operator lists the captures held on a remote's server, sees which are
promoted, and is stopped from piling up unpromoted plaintext archives.

**Why this priority**: Plaintext production data at rest is the main risk this
feature introduces; the bound keeps it from accumulating.

**Independent Test**: With captures of different ages and states on a remote,
list them with no Drive configured, then start a new capture while one complete
unpromoted capture is older than the bound.

**Acceptance Scenarios**:

1. **Given** captures on a remote and no Drive or passphrase configured, **When** the operator lists recovery for that remote, **Then** the server captures are listed with their backup id, state, age, archive size and whether each is promoted, and the Drive section reports itself unconfigured instead of failing the whole listing.
2. **Given** a complete unpromoted capture older than the remote's retention bound (default 7 days), **When** the operator asks for its status or lists the remote, **Then** it is flagged `retention_exceeded`.
3. **Given** such a capture, **When** the operator starts a new capture on that remote, **Then** it refuses with `retention_exceeded` naming the blocking backup ids.
4. **Given** a promoted capture, **Then** its server archive is kept (never deleted automatically) and it no longer counts against the retention bound.

### Edge Cases

- A capture is already active on the same remote under another backup id: the new start refuses with `capture_in_progress` naming the active backup id.
- The remote is unreachable: capture start, status and promote fail with `remote_unavailable`; no local state claims a capture exists that the server cannot confirm.
- Status during the queued window (accepted but not yet started) reports `queued` with the acceptance time.
- The server would end the capture when the operator's session closes (the host kills a user's processes at logout): capture start refuses with `detach_unsupported` instead of starting a job that would die.
- The database credential is missing from the brokered channel: capture start refuses with `missing_database_credential` before anything starts on the server.
- The capture exceeds its runtime bound: it ends `failed` with reason `capture_timeout`; partial plaintext is removed.
- A failed capture removes its partial plaintext; an interrupted capture may leave residue, which status and list report with its size.
- Archives left by the earlier one-shot `recovery create --remote` path are listed as legacy server archives: visible, not promotable, and not counted against the retention bound.
- Drive already holds a set with the same backup id that does not trace to this capture: promote refuses with `set_id_conflict` and uploads nothing.
- Promote finds a partial local transfer from a different capture identity under the same backup id: it discards it and starts over.
- Operator-side free space is below what promote needs (the transferred archive, the set archive that wraps it, and its ciphertext): promote refuses with `insufficient_space` before transfer.
- Interruption between upload and manifest write leaves the ciphertext `locally_pending`; the next promote finishes it.

## Requirements *(mandatory)*

### Functional Requirements

**Capture**

- **FR-001**: The system MUST provide a confirmed capture command that starts a capture of a supported hosted profile on a registered remote as a server-side job and returns its identity without waiting for the capture to finish.
- **FR-002**: Capture start MUST NOT require the recovery passphrase or a Drive destination, and MUST NOT transfer archive bytes over the operator's connection.
- **FR-003**: A capture MUST be keyed by remote plus backup id (one capture per backup id per remote), and its request id MUST derive from the backup id, remote, selected profile declaration and source binding, so a repeated request for the same backup id is recognized rather than duplicated.
- **FR-004**: A repeated start for a backup id with an existing capture MUST return the existing identity and state when the source binding matches and MUST refuse with `capture_binding_conflict` when it differs; it MUST never overwrite or recapture.
- **FR-005**: Capture start MUST refuse with `remote_runtime_stale` when the remote's installed runtime revision does not match, before any server work.
- **FR-006**: Before dumping, the capture's first phase (`preflight`) MUST compare an estimate of the capture's on-server size (database plus files, including temporary space the capture needs) with free space at the capture location and end the capture `failed` with `insufficient_space`, naming need, available and shortfall, when the estimate exceeds it.
- **FR-007**: At most one capture MUST be active per remote; a start that would create a second capture refuses with `capture_in_progress`. A repeated start for the backup id of the active capture follows FR-004 instead.
- **FR-008**: The capture MUST take a table inventory at dump start listing every base table and view the source database reports, with a row-count estimate per table.
- **FR-009**: The capture MUST compare the table set present in the finished dump with the inventory and end `failed` with `inventory_mismatch` and the differing names when they disagree.
- **FR-010**: A complete capture's receipt MUST record the archive hash and size, a hash and size for each archive member, the table inventory, the request id, the backup operation id, the source binding and start and end times.
- **FR-011**: The receipt MUST be written after the archive; an archive with a missing or malformed receipt MUST be treated as `incomplete`.
- **FR-012**: A failed capture MUST remove its partial plaintext; a capture MUST end `failed` with `capture_timeout` when the whole job exceeds 3600 seconds or a single dump or archive step exceeds 1800 seconds.
- **FR-013**: The database credential MUST reach the capture only through the brokered secret channel and MUST NOT appear in arguments, command text, logs, retained records or output; it MUST NOT be written to the server's disk.
- **FR-014**: Server capture files and records MUST be owner-only.

**Status and listing**

- **FR-015**: The system MUST provide a status command for a backup id on a remote that returns `queued`, `running` (with phase), `complete`, `failed` (with a typed reason) or `incomplete`, with acceptance, start and end times as applicable.
- **FR-016**: Status for a terminal state MUST return identical data on repeat, the only exception being the retention flag when the capture crosses its retention bound between calls; for a running capture, the reported phase MUST never regress.
- **FR-017**: A capture whose job is no longer alive without having recorded a terminal state MUST report `incomplete`, and MUST never be reported complete or be promotable.
- **FR-018**: Status for an unknown backup id MUST return `capture_not_found`, distinct from errors.
- **FR-019**: Status and the server capture listing MUST work with neither `RECOVERY_PASSPHRASE` nor `RECOVERY_RCLONE_DESTINATION` set, and status MUST answer when the remote runtime revision mismatches.
- **FR-020**: The recovery listing for a remote MUST include the server captures (backup id, state, age, archive size, promoted flag, retention flag) and MUST still return them when Drive is not configured, reporting the Drive section as unconfigured.

**Promotion**

- **FR-021**: The system MUST provide a confirmed promote command that publishes an existing complete capture to Drive without recapturing.
- **FR-022**: Promote MUST refuse before transferring when the passphrase or destination is missing; the destination comes from a reviewed argument or `RECOVERY_RCLONE_DESTINATION`, and the passphrase only from the inherited environment channel (023 FR-013).
- **FR-023**: Promote MUST refuse with `capture_not_complete` for any capture not in state `complete`.
- **FR-024**: Promote MUST transfer the archive through the Sandbox remote transport in bounded chunks, resumable for the same backup id and capture identity.
- **FR-025**: Promote MUST re-verify the transferred archive's hash and size against the receipt before encryption and refuse with `transfer_mismatch`, publishing nothing, on any difference.
- **FR-026**: Promote MUST encrypt on the operator side and publish ciphertext first and the manifest last, verifying the uploaded object before writing the manifest (023 FR-014).
- **FR-027**: The published manifest MUST record the capture's request id, backup operation id, archive hash and member hashes, and its table inventory summary, so the Drive set traces to the server capture without decryption.
- **FR-028**: A promote of a backup id already published from the same capture MUST report the existing set without uploading; a Drive set with that id from a different capture MUST cause refusal with `set_id_conflict`.
- **FR-029**: Promote MUST finish a `locally_pending` ciphertext for the backup id: confirm it decrypts with the current passphrase, upload and verify it, write the manifest last and remove it from the pending list, without recapture or re-encryption.
- **FR-030**: Operator-side plaintext MUST be owner-only under Sandbox machine state, removed after successful publication, and reported (not silently deleted) when a promote stops before publication.
- **FR-031**: After a successful promote, the server capture MUST be marked promoted; its archive MUST NOT be deleted automatically.

**Retention bound**

- **FR-032**: Each complete unpromoted server capture made by this feature MUST carry a retention bound, 7 days by default and overridable per remote; past it, status and listing MUST report `retention_exceeded`. Interrupted residue and legacy one-shot archives are listed with their size but carry no bound.
- **FR-033**: A start that would create a new capture MUST refuse with `retention_exceeded`, naming the blocking backup ids, while the remote holds a complete unpromoted capture made by this feature past its bound; no other server file blocks a capture.
- **FR-034**: Removing a server capture MUST go only through the reviewed-and-confirmed retention path; nothing is deleted automatically. `recovery retention` without `--confirm` MUST list every server capture on the remote with its state, age, size, promoted flag and whether it is retirable. A confirmed retire (`recovery retention --confirm --remote <r> --backup-id <id>`, one capture per call, on CLI and MCP alike) MUST be accepted only for a capture whose state is `promoted`, `failed` or `interrupted`; a `complete` unpromoted capture, an active (`queued`/`running`) capture and a legacy one-shot archive MUST be refused with `not_retirable`, so promotion is the only way a complete unpromoted capture clears a `retention_exceeded` block. Before deleting, the apply MUST re-read the capture from the server and refuse with `retire_candidate_changed` if its state, archive hash or size differs from the reviewed plan. A retire removes the archive, residue and receipt for that capture; the capture record is kept with state `retired` and the time. A promote refused for an archive-versus-receipt integrity mismatch MUST set the capture `failed` with reason `integrity_mismatch`, which makes it retirable.

**Surfaces and safety**

- **FR-035**: Capture, status and promote MUST be available on the Sandbox CLI and through the recovery MCP tool group with matching parameters and result envelopes; capture and promote MUST require explicit confirmation on both, status none.
- **FR-036**: All recovery operations in this feature MUST run through Sandbox's remote transport, never through `sb remote ssh`, `job-start` or operator-run raw SSH, container or database commands (023 FR-026).
- **FR-037**: Results MUST use the existing recovery result envelope, redact secrets and bound child output (023 FR-027).
- **FR-038**: The existing one-shot `recovery create --remote` behavior MUST remain available and unchanged.

### Key Entities

- **Server capture**: one capture of one backup id on one remote (key: remote + backup id). Identity (request id derived from backup id, remote and declaration), backup operation id, source binding, state, phase, acceptance/start/end times, failure reason, promoted flag, retention flag.
- **Capture receipt**: written after the archive on the server. Archive hash and size, member records (name, hash, size), table inventory, request id, backup operation id, source binding, times.
- **Table inventory**: each base table and view at dump start with its type and row-count estimate; summary counts.
- **Promotion**: the operator-side transfer, verification, encryption and publication of one capture; resumable transfer progress keyed to the capture identity.
- **Published recovery set**: the existing 023 Drive set (ciphertext then manifest), whose manifest now also carries the server capture's identifiers, hashes and inventory summary.
- **Locally pending ciphertext**: the existing 023 state, now with an exit through promote.
- **Retention bound**: per-remote age limit for complete unpromoted captures made by this feature; default 7 days.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Capture start returns within 30 seconds with the capture running on the server, and zero archive bytes cross the operator's connection during capture.
- **SC-002**: For a capture in a terminal state, 3 repeated status calls return identical data; for a running capture, repeat calls never regress to an earlier phase; a capture killed mid-run reports `incomplete`.
- **SC-003**: Status and server-side listing succeed with `RECOVERY_PASSPHRASE` and `RECOVERY_RCLONE_DESTINATION` both unset.
- **SC-004**: A complete capture's receipt lists 100% of the base tables and views the source reported at dump start, each with a row-count estimate; the dump's table set equals that list; the archive and every member carry a hash.
- **SC-005**: Every capture start performs the free-space check before any dump, and a start below the estimate is refused with the shortfall stated.
- **SC-006**: Promote of a complete capture publishes a set whose recorded hash for the captured archive equals the receipt's archive hash, checkable from the manifest without decryption, with zero recaptures.
- **SC-007**: For a 2 GB archive, peak memory of the server capture job and of the operator-side promote each stays under 256 MB.
- **SC-008**: An interrupted promote resumes and transfers no more than one chunk of already-received data again.
- **SC-009**: The database credential and recovery passphrase appear in none of the arguments, command text, retained records, logs or outputs produced by capture, status or promote.

## Assumptions

- Supported profiles are those the hosted controller supports today (the reviewed `amarsonar-bangla-prod` WordPress/MariaDB declaration and its control-plane dependency); no new hosted profile is added.
- Server-side plaintext at rest between capture and promote is accepted as recorded in the PRD decisions, bounded by the retention limit and owner-only permissions.
- The operator's machine has room for the plaintext archive and its ciphertext during promote; promote checks this.
- Live production proof requires explicit approval and the brokered database credential; tests without approval use fake remotes and fixtures.
- PostgreSQL recovery, restore apply, schedule activation and legacy Drive deletion are unchanged (023 T061, T069, T071, T072).
