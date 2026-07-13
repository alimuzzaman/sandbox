# Tasks: Scoped Recovery Profiles

**Input**: Design documents from `/specs/023-scoped-recovery-profiles/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required by the specification; tests precede implementation and include failure injection.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable only with a separate non-overlapping file owner
- **[Story]**: User story mapping

## Phase 1: Setup and Baseline

**Purpose**: Preserve existing behavior and establish safe fixture boundaries.

- [X] T001 Record exact existing Hermes local/Drive backup CLI and MCP envelopes in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [X] T002 Record read-only remote inventory needed to locate candidate production services, repositories, volumes, and roots through `./sb` in `specs/023-scoped-recovery-profiles/profile-inventory.md`
- [X] T003 [P] Create secret-free recovery fixtures and sentinel trees in `tests/fixtures/recovery/`
- [X] T004 [P] Capture exact CLI and MCP inventories before recovery registration in `specs/023-scoped-recovery-profiles/interface-inventory.md`
- [X] T005 Create `sandbox/recovery/` package skeleton and public exports in `sandbox/recovery/__init__.py`

---

## Phase 2: Foundational Contracts

**Purpose**: Shared immutable models, errors, fakes, and composition required by every story.

- [X] T006 [P] Add failing profile/artifact/set/restore/schedule/retention model tests in `tests/test_recovery_models.py`
- [X] T007 [P] Add failing stable redacted result/error envelope tests in `tests/test_recovery_service.py`
- [X] T008 [P] Add recording crypto/Drive/lock/clock/database/filesystem fakes in `tests/fakes/recovery.py`
- [X] T009 Implement immutable domain models and state transitions in `sandbox/recovery/models.py`
- [X] T010 Implement recovery errors and recursive redaction in `sandbox/recovery/errors.py`
- [X] T011 Implement dependency container and service composition skeleton in `sandbox/recovery/context.py` and `sandbox/recovery/service.py`
- [X] T012 Add architecture guards preventing recovery modules from importing WordPress policy, raw Docker, or legacy broad Drive builders in `tests/test_architecture_boundaries.py`

**Checkpoint**: Recovery has no live side effects and all mechanisms are injectable.

---

## Phase 3: User Story 1 — Declare Valuable State (Priority: P1) MVP

**Goal**: Validate a committed catalog and generate a complete side-effect-free plan.

**Independent Test**: Plan all initial profiles; every source is included/excluded/deferred with rationale and zero process/network writes.

### Tests for User Story 1

- [X] T013 [P] [US1] Add failing catalog schema, duplicate, unknown field/adapter, cycle, shell-string, and secret-field tests in `tests/test_recovery_catalog.py`
- [X] T014 [P] [US1] Add failing allowed-root, symlink escape, absent source, full/partial, and deterministic dependency-order tests in `tests/test_recovery_planner.py`
- [X] T015 [P] [US1] Add failing initial-profile inclusion/exclusion policy tests in `tests/test_recovery_catalog.py`

### Implementation for User Story 1

- [X] T016 [US1] Implement catalog loader and strict v1 validation in `sandbox/recovery/catalog.py`
- [X] T017 [US1] Implement side-effect-free target resolver and artifact planner in `sandbox/recovery/planner.py`
- [X] T018 [US1] Add four initial secret-free profiles in `config/recovery-profiles.json`
- [X] T019 [US1] Implement service `profiles` and `plan` operations in `sandbox/recovery/service.py`
- [X] T020 [US1] Add feature-owned `sb recovery profiles|plan` parser/handlers in `sandbox/commands/recovery.py` and register the module in `sandbox/commands/manifest.py`
- [X] T021 [US1] Add MCP recovery group with read-only profile/plan tools in `mcp/wp-server/tools/recovery.py` and `mcp/wp-server/tools/manifest.py`
- [X] T022 [US1] Run fixture and read-only remote plans; finalize discovered paths without capturing data in `config/recovery-profiles.json` and `specs/023-scoped-recovery-profiles/profile-inventory.md`

**Checkpoint**: The complete intended backup scope is reviewable without mutation.

---

## Phase 4: User Story 2 — Create Verified Encrypted Recovery Sets (Priority: P1)

**Goal**: Capture, validate, encrypt, upload, and publish complete manifests.

**Independent Test**: Fixture set round-trips with current secret channel; every injected failure leaves zero complete manifest.

### Tests for User Story 2

- [X] T023 [P] [US2] Add failing PostgreSQL/MariaDB consistency, empty dump, DDL/non-transactional warning, credential-channel, and validation tests in `tests/test_recovery_database.py`
- [X] T024 [P] [US2] Add failing full/partial tar membership, traversal, links, ownership, ACL/xattr fallback, and source-change tests in `tests/test_recovery_filesystem.py`
- [X] T025 [P] [US2] Add failing Git remote/revision, unpublished bundle/patch, ignored-secret, dirty-file classification, and bundle verification tests in `tests/test_recovery_git.py`
- [X] T026 [P] [US2] Add failing passphrase argv/output/process-list, encryption/decrypt/hash, plaintext cleanup, and interruption tests in `tests/test_recovery_crypto.py`
- [X] T027 [P] [US2] Add failing Drive upload/check/manifest-last/idempotency/pending retry/list classification tests in `tests/test_recovery_drive.py`
- [X] T028 [P] [US2] Add end-to-end capture failure matrix and prior-set preservation tests in `tests/test_recovery_capture.py`

### Implementation for User Story 2

- [X] T029 [US2] Implement database capture adapter through bounded process services in `sandbox/recovery/database.py`
- [X] T030 [P] [US2] Implement allowlisted full/partial filesystem adapter in `sandbox/recovery/filesystem.py`
- [X] T031 [P] [US2] Implement Git provenance and critical unpublished-state adapter in `sandbox/recovery/git.py`
- [X] T032 [US2] Implement owner-only staging and artifact coordinator in `sandbox/recovery/capture.py`
- [X] T033 [US2] Implement GnuPG descriptor-based encrypt/decrypt verification in `sandbox/recovery/crypto.py`
- [X] T034 [US2] Implement immutable Drive object upload, remote verification, manifest-last publication, list, and pending retry in `sandbox/recovery/drive.py`
- [X] T035 [US2] Implement recovery-set create/list/verify orchestration in `sandbox/recovery/service.py`
- [X] T036 [US2] Add protected CLI create/list/verify operations in `sandbox/commands/recovery.py`
- [X] T037 [US2] Add MCP create/list/verify tools without passphrase arguments in `mcp/wp-server/tools/recovery.py`
- [X] T038 [US2] Run fixture capture/decrypt verification and record secret/redaction/failure evidence in `specs/023-scoped-recovery-profiles/implementation-evidence.md`

**Checkpoint**: A new encrypted fixture recovery set is verifiably restorable; no live production capture yet.

---

## Phase 5: User Story 3 — Restore Safely From Scratch (Priority: P1)

**Goal**: Non-mutating restore plans and checkpointed disposable restore/rollback.

**Independent Test**: Fixture plan writes nothing; apply restores correctly and injected verification failure rolls back.

### Tests for User Story 3

- [X] T039 [P] [US3] Add failing manifest/schema/hash/compatibility/free-space/target/dependency restore-plan tests in `tests/test_recovery_restore.py`
- [X] T040 [P] [US3] Add failing checkpoint/quiesce/stage/swap/import/verify/resume/rollback ordering tests in `tests/test_recovery_restore_apply.py`
- [X] T041 [P] [US3] Add zero-side-effect CLI and MCP restore-plan tests in `tests/test_recovery_interfaces.py`

### Implementation for User Story 3

- [X] T042 [US3] Implement manifest download, integrity, compatibility, and restore planning in `sandbox/recovery/restore.py`
- [X] T043 [US3] Implement checkpointed database/filesystem/control-plane/Git restore adapters in `sandbox/recovery/restore.py`
- [X] T044 [US3] Implement selected-profile dependency ordering and rollback coordinator in `sandbox/recovery/service.py`
- [X] T045 [US3] Add plan-default and confirm-required CLI restore in `sandbox/commands/recovery.py`
- [X] T046 [US3] Add plan-default and confirm-required MCP restore tools in `mcp/wp-server/tools/recovery.py`
- [X] T047 [US3] Run disposable fixture restore and rollback drill; record exact evidence in `specs/023-scoped-recovery-profiles/implementation-evidence.md`

**Checkpoint**: Restore works in disposable targets; no production target has been changed.

---

## Phase 6: User Story 4 — Retain and Schedule Without Collisions (Priority: P2)

**Goal**: Reusable non-overlapping schedule and conservative retention plan/apply.

**Independent Test**: Fake-clock/lock tests prove one active run, resource skips, and protected-set retention floors.

### Tests for User Story 4

- [ ] T048 [P] [US4] Add failing lock/resource/timeout/retry/random-delay/timer rendering tests in `tests/test_recovery_scheduler.py`
- [ ] T049 [P] [US4] Add failing destination-boundary/classification/newest/only/current-passphrase/candidate freshness tests in `tests/test_recovery_retention.py`

### Implementation for User Story 4

- [ ] T050 [US4] Implement single-run lock, resource gate, and schedule plan/render/remove in `sandbox/recovery/scheduler.py`
- [ ] T051 [US4] Implement conservative retention classification and plan/apply in `sandbox/recovery/retention.py`
- [ ] T052 [US4] Add protected CLI schedule/retention operations in `sandbox/commands/recovery.py`
- [ ] T053 [US4] Add protected MCP schedule/retention tools in `mcp/wp-server/tools/recovery.py`
- [ ] T054 [US4] Verify schedule and retention plans remotely without activation/deletion in `specs/023-scoped-recovery-profiles/implementation-evidence.md`

**Checkpoint**: Automation is implemented but remains disabled pending a verified real set and separate confirmation.

---

## Phase 7: User Story 5 — Rebuild the Control Plane (Priority: P2)

**Goal**: Complete the actual disaster-recovery path and fresh-server drill.

**Independent Test**: Clean disposable host/root plus checkout, approved secrets, and set ID reconstructs selected profiles.

### Tests for User Story 5

- [ ] T055 [P] [US5] Add control-plane safe-state/credential exclusion and Cloudflare declaration tests in `tests/test_recovery_control_plane.py`
- [ ] T056 [P] [US5] Add clean-root bootstrap, prerequisite, profile selection, and acceptance tests in `tests/test_recovery_fresh_server.py`

### Implementation for User Story 5

- [ ] T057 [US5] Implement control-plane capture/restore adapter over Sandbox/Hermes backup and shared service contracts in `sandbox/recovery/control_plane.py`
- [ ] T058 [US5] Add fresh-server bootstrap and verification orchestration in `sandbox/recovery/bootstrap.py`
- [ ] T059 [US5] Document operator-safe recovery and per-profile restore in `docs/recovery.md`
- [ ] T060 [US5] Create one real scoped encrypted set with the current passphrase through `./sb recovery create` and verify download/decrypt/integrity in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T061 [US5] Run a disposable fresh-server drill and re-run Hermes/public-dashboard/hosting acceptance checks in `specs/023-scoped-recovery-profiles/implementation-evidence.md`

**Checkpoint**: A current-passphrase real recovery set and fresh-server proof exist.

---

## Phase 8: Final Safety, Documentation, and Protected Operations

- [ ] T062 [P] Update recovery/module architecture and command references in `README.md`, `AGENTS.md`, `CLAUDE.md`, and `docs/sandbox-config-reference.md`
- [ ] T063 [P] Add recovery workflow guidance under `.agents/skills/` or `workflows/` and verify it calls only Sandbox commands
- [ ] T064 Add exact CLI/MCP inventories and no-central-growth guards in `tests/test_command_composition.py`, `tests/test_mcp_composition.py`, and `tests/test_architecture_boundaries.py`
- [ ] T065 Run focused recovery suites, full unit discovery, MCP tests, `./sb selftest`, `git diff --check`, and quickstart scenarios; record results in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T066 Perform fresh correctness/regression review and separate security/data-loss review; resolve findings in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T067 Re-run Spec-Kit analyze/converge and append any missing work to `specs/023-scoped-recovery-profiles/tasks.md`
- [ ] T068 Prepare but do not apply the remote schedule activation plan in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T069 Prepare the legacy Drive deletion candidate plan only after T060 and leave apply blocked on explicit confirmation in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T070 Confirm no production restore, deletion, schedule activation, public-access mutation, commit, or push occurred without its specific approval in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T071 Apply the exact reviewed legacy Drive deletion plan after T060 using the user's explicit deletion authorization, verify only legacy objects were removed, and record evidence in `specs/023-scoped-recovery-profiles/implementation-evidence.md`
- [ ] T072 Activate the reviewed non-overlapping recovery schedule after T060 and T061 using the user's explicit scheduling authorization, monitor its first run, and record evidence in `specs/023-scoped-recovery-profiles/implementation-evidence.md`

---

## Dependencies & Execution Order

```text
Setup -> Foundation -> US1 Plan -> US2 Capture -> US3 Restore
                                      └----------> US4 Schedule/Retention
US2 + US3 + US4 -> US5 Fresh-server proof -> Final safety/protected plans
```

- US1 blocks all capture because scope must be inspectable first.
- US2 blocks real recovery sets and supplies artifacts to US3.
- US3 must pass disposable rollback before any real capture is treated as disaster-ready.
- US4 may be implemented after US2 but activation is blocked until US5.
- US5 blocks legacy prune candidates and schedule activation.
- One writer owns `sandbox/recovery/service.py`; `[P]` tasks never overlap that file.

## Parallel Opportunities

- Fixture creation and baseline inventory can run independently.
- Within US2, database/filesystem/Git adapter tests and implementations use separate files.
- Scheduler and retention tests/modules are independent after shared models stabilize.
- Correctness and security reviews are independent and must not edit their own targets.

## Implementation Strategy

1. Deliver US1 planning as the MVP and inspect real scope.
2. Prove capture/crypto/Drive entirely with fixtures.
3. Prove restore and rollback in disposable roots.
4. Implement but do not activate schedule/retention.
5. Create and verify one real scoped set, then perform fresh-server drill.
6. Present schedule activation and legacy deletion as separate protected actions.

## Notes

- Existing legacy Drive code is compatibility-only and cannot be called by new profiles.
- All server/database/Drive operations are invoked through Sandbox interfaces.
- Commit and push require separate explicit user approval.
