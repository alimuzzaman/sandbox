---

description: "Task list for server-first recovery capture and later Drive promotion"
---

# Tasks: Server-First Recovery Capture and Later Drive Promotion

**Input**: Design documents from `specs/058-server-first-recovery-capture/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Requested (tests first). In every phase the test tasks come first and
must fail before the matching implementation task starts. Run with
`python3 -m unittest <module>`.

**Organization**: Grouped by user story so each story can be built and checked on its own.

**Guardrails for every task**: do not edit `sandbox/commands/hosting.py`,
`sandbox/delivery/hosting.py`, `sandbox/core/_remote.py` or `tests/test_hosting.py`
(import only). No `sb remote ssh`, no `job-start`, no secrets in argv or output.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1–US5 from spec.md

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: test fixtures used by every story

- [x] T001 [P] Create fake executables `docker`, `tar`, `loginctl` (shell scripts driven by env vars for exit code, delay, output fixture) in `tests/fixtures/server_capture/bin/`
- [x] T002 [P] Create MariaDB dump fixtures (`tables_only.sql`, `with_views.sql`, `missing_table.sql`, `extra_table.sql`) and matching inventory TSV outputs in `tests/fixtures/server_capture/dumps/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: shared types, helper skeleton and transport every story builds on

**CRITICAL**: no user story work starts until this phase is complete

- [x] T003 [P] Write tests proving the extracted control-plane declaration builder and remote state probe return exactly what `RegisteredRemoteRecoveryController.capture`/`_state` produce today, in `tests/test_remote_recovery.py`
- [x] T004 Extract the control-plane declaration builder and remote state probe into module-level functions reused by the controller, behavior unchanged, in `sandbox/transports/remote_recovery.py` (depends on T003)
- [x] T005 [P] Write tests for slot derivation (R4), phase ordering, `derive_state` (`incomplete` rules from data-model.md), retention view and retention policy resolution (default 7, per-remote override, 1–365 else `invalid_retention_policy`) in `tests/test_server_capture.py`
- [x] T006 Implement slot derivation, phase enum, `derive_state`, retention view and policy loader (config keys from research R12) in `sandbox/recovery/server_capture.py` (depends on T005)
- [x] T007 [P] Write tests for helper primitives: owner-only create (`O_EXCL|O_NOFOLLOW`, 0600, fsync, rename), owned read rejecting symlink/non-owner/group bits/`nlink != 1`, JSON-only stdout with fixed error codes, unknown op → `request_invalid`, and source size < 64 KiB, in `tests/test_server_capture_helper.py`
- [x] T008 Implement the helper skeleton (op dispatch, primitives, root creation 0700 with owner check) in `sandbox/recovery/server_capture_helper.py` (depends on T007)
- [x] T009 [P] Write tests for `RegisteredServerCaptureTransport`: helper sent via injected `ssh_process` as `python3 -c`, only fixed args in argv, bounded JSON parsing, helper codes mapped to `RecoveryError`, timeout → `remote_unavailable` without replay, and no use of `sb remote ssh` or job APIs, in `tests/test_remote_server_capture.py`
- [x] T010 Implement the transport skeleton (remote lookup, home resolution, op invocation, output bounds) in `sandbox/transports/remote_server_capture.py` (depends on T008, T009)

**Checkpoint**: foundation ready

---

## Phase 3: User Story 1 - Start a capture on the server (Priority: P1) MVP

**Goal**: a confirmed command starts a detached server capture and returns its identity; the job records inventory and hashes.

**Independent Test**: with passphrase and destination unset, start a capture against the fake remote; it returns in < 30 s with no archive bytes transferred; the slot later holds `archive.tar` and a valid `receipt.json`.

### Tests for User Story 1

- [x] T011 [P] [US1] Helper `start` tests: writes `request.json`, `declarations.json`, `state.json{queued}`; returns within 5 s while the fake job sleeps; `job.lock` and `active.lock` held by the detached job; password not present in any file under the root, in `tests/test_server_capture_helper.py`
- [x] T012 [US1] Helper `start` refusal tests: same request id → `existing: true` with current state, including while that slot's own job holds `active.lock`; two racing new starts for one id create one slot; different request id → `capture_binding_conflict` and files unchanged; another slot active → `capture_in_progress` with its backup id; fake `loginctl` reporting `KillUserProcesses=yes` → `detach_unsupported`, in `tests/test_server_capture_helper.py`
- [x] T013 [US1] Helper job `preflight` tests: estimate `2 × (D + F) × 1.10` above fake free space → `failed: insufficient_space` with need/available/shortfall and no dump invoked, in `tests/test_server_capture_helper.py`
- [x] T014 [US1] Helper job inventory tests using T002 fixtures: matching tables and views → receipt `inventory.tables` with types and row estimates and summary counts; missing or extra table → `failed: inventory_mismatch` with both name lists (≤200 each), in `tests/test_server_capture_helper.py`
- [x] T015 [US1] Helper job archive tests: member and archive SHA-256 equal independent hashes; receipt carries request id, backup operation id, source binding, declarations hash and start/end times (FR-010); receipt written after archive; second tar stream compared by digest (no third file on disk); changed tree → `failed: source_changed`; step over 1800 s or job over 3600 s (time-scaled via env) → `failed: capture_timeout`; every failure removes work files, in `tests/test_server_capture_helper.py`
- [x] T016 [P] [US1] Service start gate tests: order and codes from contracts/cli-mcp.md (`confirmation_required`, `missing_*`, `invalid_set_id`, `unsupported_materialization`, `missing_database_credential`, `remote_unavailable`, `remote_runtime_stale`, `retention_exceeded` with `data.blocking`); a replay of an existing backup id is not blocked by the retention guard; no helper `start` call on any refusal; works with `RECOVERY_PASSPHRASE` and `RECOVERY_RCLONE_DESTINATION` unset, in `tests/test_server_capture.py`
- [x] T017 [P] [US1] Transport start tests: stdin carries the password line then the declarations JSON; argv contains neither; request id binds the hosted source request and canonical declaration hash; start issues no `read-chunk`/`read-receipt` call (no archive bytes cross during capture, SC-001), in `tests/test_remote_server_capture.py`
- [x] T018 [P] [US1] CLI `recovery capture` and MCP `recovery_capture` tests: confirmation required on both, identical envelopes for the same fake outcome, human output prints backup id, request id and state, in `tests/test_recovery_cli_server_capture.py`

### Implementation for User Story 1

- [x] T019 [US1] Implement helper `start` op (locks, replay/conflict, loginctl check, double fork + `setsid`, password kept in memory) in `sandbox/recovery/server_capture_helper.py` (depends on T011, T012)
- [x] T020 [US1] Implement job phases `preflight` → `inventory` → `dump` → `files` → `verify` → `archive` → `receipt` with streaming hashes, deadlines and cleanup, per contracts/server-capture-helper.md, in `sandbox/recovery/server_capture_helper.py` (depends on T013–T015, T019)
- [x] T021 [US1] Implement transport `start` (revision gate mapped to `remote_runtime_stale`, brokered stdin) in `sandbox/transports/remote_server_capture.py` (depends on T004, T017)
- [x] T022 [US1] Implement `ServerCaptureService.start` gates and retention guard (uses helper `list` raw facts) in `sandbox/recovery/server_capture.py` and wire it in `sandbox/recovery/context.py` without requiring passphrase or destination (depends on T016, T021)
- [x] T023 [US1] Add the `capture` action and human output in `sandbox/commands/recovery.py` (depends on T018, T022)
- [x] T024 [US1] Add `recovery_capture` to `mcp/wp-server/tools/recovery.py`, list it in `BUILTIN_TOOL_NAMES["recovery"]` in `mcp/wp-server/tools/manifest.py`, and update the schema snapshot in `tests/test_mcp.py` (depends on T022)

**Checkpoint**: US1 passes on its own (status can be read from the helper tests' files)

---

## Phase 4: User Story 2 - Check capture status at any time (Priority: P1)

**Goal**: status returns the retained state, identical on repeat once terminal, `incomplete` after interruption.

**Independent Test**: poll status through a fake capture; kill a second fake job; ask 3 times for each terminal state; all with passphrase and destination unset and a stale revision.

### Tests for User Story 2

- [x] T025 [P] [US2] Helper `status` tests: returns request, state, `lock_free`, `receipt_valid`, archive size and residue bytes; never writes (directory mtimes and file hashes unchanged after 3 calls); malformed or missing receipt → `receipt_valid: false`, in `tests/test_server_capture_helper.py`
- [x] T026 [P] [US2] Service status tests: killed job (lock free, non-terminal) → `incomplete`; archive without receipt → `incomplete`; `queued` window reports acceptance time; unknown id → `capture_not_found`; phase sequence from repeated polls never regresses; 3 terminal calls identical; complete shows archive, members and inventory summary; stale revision still answers; `remote_unavailable` mapped, in `tests/test_server_capture.py`
- [x] T027 [P] [US2] CLI `recovery status` and MCP `recovery_capture_status` parity tests in `tests/test_recovery_cli_server_capture.py`

### Implementation for User Story 2

- [x] T028 [US2] Implement helper `status` op in `sandbox/recovery/server_capture_helper.py` (depends on T025)
- [x] T029 [US2] Implement transport and `ServerCaptureService.status` without the revision gate in `sandbox/transports/remote_server_capture.py` and `sandbox/recovery/server_capture.py` (depends on T026, T028)
- [x] T030 [US2] Add the `status` action in `sandbox/commands/recovery.py` and `recovery_capture_status` in `mcp/wp-server/tools/recovery.py` + `mcp/wp-server/tools/manifest.py` + `tests/test_mcp.py` snapshot (depends on T027, T029)

**Checkpoint**: US1 + US2 form the MVP (capture and observe, no Drive)

---

## Phase 5: User Story 3 - Promote a completed capture to Drive later (Priority: P2)

**Goal**: publish a complete capture to Drive, encrypted operator-side, archive-first and manifest-last, traceable to the capture, without recapture.

**Independent Test**: promote a complete fake capture into the in-memory Drive; manifest artifact hash equals the receipt; `recovery list` shows it complete; helper `start` was never called.

### Tests for User Story 3

- [x] T031 [P] [US3] Helper read-op tests: `read-receipt` ≤1 MiB, `read-declaration` ≤64 KiB, `read-chunk` header + exact bytes with per-chunk SHA-256, length > 16 MiB or offset past end refused, `mark-promoted` idempotent and refused unless receipt valid, in `tests/test_server_capture_helper.py`
- [x] T032 [P] [US3] Promote gate tests in contract order: `confirmation_required`, `missing_passphrase`, `recovery_not_configured`, `already_published` (no upload), `set_id_conflict`, `incomplete_remote_set`, `capture_not_complete` (for `failed`, `incomplete`, `running`), operator `insufficient_space` (3 × archive − received); none transfers a byte, in `tests/test_recovery_promote.py`
- [x] T033 [US3] Transfer tests: 16 MiB chunks appended to `$SANDBOX_HOME/recovery/promote/<slot>/archive.part` (0600 in 0700); interrupted promote resumes from `received` and re-reads at most one chunk; different request id or archive hash discards the partial; final hash or size mismatch → `transfer_mismatch`, partial removed, nothing published, in `tests/test_recovery_promote.py`
- [x] T034 [US3] Publication tests: `publish_files` called once with both artifacts under the owned `materialized` root; fake Drive records ciphertext upload and verify before the manifest write; `provenance.server_capture` matches contracts/manifest-provenance.md; artifact hash equals receipt `archive_sha256`; `mark-promoted` only after the manifest verifies; operator plaintext removed on success and reported via `local_transfer_bytes` on failure; capture past its retention bound promotes normally, in `tests/test_recovery_promote.py`
- [x] T035 [P] [US3] CLI `recovery promote` (with `--destination`) and MCP `recovery_promote` parity tests in `tests/test_recovery_cli_server_capture.py`

### Implementation for User Story 3

- [x] T036 [US3] Implement helper `read-receipt`, `read-declaration`, `read-chunk`, `mark-promoted` in `sandbox/recovery/server_capture_helper.py` (depends on T031)
- [x] T037 [US3] Implement chunked resumable reader and read ops in `sandbox/transports/remote_server_capture.py` (depends on T033, T036)
- [x] T038 [US3] Implement `ServerCaptureService.promote` (gates, Drive idempotency, transfer, declaration check, `publish_files` with provenance, mark promoted, cleanup) in `sandbox/recovery/server_capture.py` (depends on T032, T034, T037)
- [x] T039 [US3] Add the `promote` action in `sandbox/commands/recovery.py` and `recovery_promote` in `mcp/wp-server/tools/recovery.py` + `mcp/wp-server/tools/manifest.py` + `tests/test_mcp.py` snapshot (depends on T035, T038)

**Checkpoint**: US3 passes on its own against a fixture capture

---

## Phase 6: User Story 4 - Finish a locally pending ciphertext (Priority: P3)

**Goal**: promote gives `locally_pending` an exit without recapture or re-encryption.

**Independent Test**: seed a pending ciphertext (with and without sidecar), promote, and see it published and gone from the pending list.

### Tests for User Story 4

- [x] T040 [P] [US4] Capture coordinator tests: manifest computed before upload; a failed upload after verified ciphertext preserves both `<set>.archive.tar.gpg` and `<set>.manifest.json` (0600); existing pending behavior otherwise unchanged, in `tests/test_recovery_capture.py`
- [x] T041 [P] [US4] Pending promote tests: sidecar path uploads the same ciphertext bytes, verifies, writes the manifest last and removes both files; wrong passphrase → `passphrase_not_current` with files kept; ciphertext hash ≠ sidecar → `pending_artifact_invalid`; no sidecar → manifest derived from decrypted members with `"pending_recovered": true`; no server contact needed, in `tests/test_recovery_promote.py`

### Implementation for User Story 4

- [x] T042 [US4] Compute the manifest before upload, write the sidecar in `_preserve_pending`, and add `publish_pending` in `sandbox/recovery/capture.py` (depends on T040)
- [x] T043 [US4] Route promote through the pending path first in `sandbox/recovery/server_capture.py` (depends on T038, T041, T042)

---

## Phase 7: User Story 5 - See and bound server-held captures (Priority: P3)

**Goal**: list server captures without Drive, flag and enforce the retention bound, never delete automatically.

**Independent Test**: fixture slots of mixed age/state plus legacy archives; list with no Drive; start blocked only by a complete unpromoted capture past its bound.

### Tests for User Story 5

- [x] T044 [P] [US5] Helper `list` tests: returns slot facts and `legacy` entries from `runtime/recovery-controller/*.tar` (name, size only), bounded to 500 slots and 256 KiB, in `tests/test_server_capture_helper.py`
- [x] T045 [P] [US5] Service list tests: `RecoveryService.list(remote)` adds `server_captures` and `legacy_server_archives`; Drive unconfigured with a remote → `ok` with `drive.configured: false`; without a remote the old `recovery_not_configured` result is unchanged, in `tests/test_recovery_service.py`
- [x] T046 [P] [US5] Retention tests: complete unpromoted past bound → `retention_exceeded` in status and list and blocks start naming backup ids; promoted, failed, incomplete residue and legacy archives never block; promote leaves `archive.tar` in place; no code path other than the confirmed retire (T050) deletes server files, in `tests/test_server_capture.py`

### Implementation for User Story 5

- [x] T047 [US5] Implement helper `list` op in `sandbox/recovery/server_capture_helper.py` (depends on T044)
- [x] T048 [US5] Merge server captures into `RecoveryService.list` and tolerate missing Drive with a remote in `sandbox/recovery/service.py`; print the server section in `_emit` in `sandbox/commands/recovery.py` (depends on T045, T047)
- [x] T049 [US5] Finalize the retention view and start guard against helper `list` facts in `sandbox/recovery/server_capture.py` (depends on T046, T048)
- [x] T050 [US5] Server-capture retirement per FR-034: read-only helper `retire-plan` fingerprints eligible state, receipt digest, actual archive bytes and size (including failed/incomplete archives without valid receipts); confirmed helper `retire` locks and rechecks all four fields before removing archive, residue and receipt, then keeps the record as `retired` with time. Retention lists `retirable`; CLI and MCP retire one eligible capture, refusing `not_retirable` for complete unpromoted, queued/running and legacy archives. Promote integrity mismatch remains failed and retirable. Tests cover state and same-size byte drift refusals plus failed/incomplete retirement, in `tests/test_server_capture.py` and `tests/test_server_capture_helper.py` (depends on T049)

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T051 [P] Memory test: 2 GB sparse archive through the helper job and through promote transfer and publication with fakes; assert peak RSS < 256 MB for each (SC-007), in `tests/test_server_capture_memory.py`
- [x] T052 [P] Secret-leak test: sentinel DB credential and passphrase never appear in any captured argv, helper file, state/receipt, envelope or log line across capture, status, list and promote (SC-009, FR-013), in `tests/test_server_capture.py`
- [x] T053 [P] Regression: existing `recovery create --remote` tests pass unchanged (FR-038): run `python3 -m unittest tests.test_remote_recovery tests.test_recovery_hosted tests.test_recovery_materialize tests.test_recovery_service tests.test_recovery_capture`
- [x] T054 Update `docs/recovery.md` (server-first capture, status, promote, retention bound, amended 023 FR-012/014/016), the README "Scoped recovery" section and `workflows/recovery/WORKFLOW.md`
- [x] T055 Run the full quickstart step 1–2 suite and `git diff --stat origin/latest -- sandbox/commands/hosting.py sandbox/delivery/hosting.py sandbox/core/_remote.py tests/test_hosting.py` (must be empty)
- [ ] T056 After explicit approval only: live verification per quickstart steps 3–7 on the approved remote; record evidence (timings, repeated status outputs, manifest hashes, peak memory) in `specs/058-server-first-recovery-capture/implementation-evidence.md`

---

## Dependencies & Execution Order

- Setup (T001–T002) → Foundational (T003–T010) → stories.
- US1 (P1) → US2 (P1): US2's service status reuses the slot written by US1's helper, but its tests use fixture slots, so US2 can start after Foundational in parallel with US1 implementation.
- US3 (P2) needs US1 + US2 (complete captures and status).
- US4 (P3) needs US3's promote entry (T038).
- US5 (P3) needs Foundational; T049 needs T022. T050 needs T049.
- Polish after the stories it measures.

## Parallel Examples

- Foundational: T003, T005, T007, T009 together (four test files).
- US1 tests: T011 (then T012–T015 in the same file, sequentially) alongside T016, T017, T018 (three other files).
- US3 tests: T031, T032 (then T033–T034 in the same file), T035.
- Polish: T051, T052, T053.

## Implementation Strategy

1. MVP = Setup + Foundational + US1 + US2: capture runs on the server and is observable with no Drive or passphrase.
2. Add US3 (promote) — the backup reaches Drive.
3. Add US4 and US5 — pending exit and the retention bound.
4. Polish, docs, then the approved live run (T056) as the constitution's proof of done.
