---

description: "Task list for Agent-Aware Remote Development Sync"

---

# Tasks: Agent-Aware Remote Development Sync

**Input**: Design documents from `/specs/033-agent-aware-remote-sync/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`,
`contracts/cli-mcp.md`, and `quickstart.md`.

**Organization**: Tasks are grouped by user story and ordered by dependency.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish feature-owned modules and test seams without changing
existing deploy or job behavior.

- [X] T001 Create the `sandbox/sync/` package and module exports in `sandbox/sync/__init__.py`.
- [X] T002 [P] Add focused test module skeletons in `tests/test_sync_manifest.py`, `tests/test_sync_state.py`, `tests/test_sync_capture.py`, `tests/test_sync_projection.py`, `tests/test_sync_coordinator.py`, `tests/test_sync_transport.py`, `tests/test_sync_cli.py`, and `tests/test_sync_mcp.py`.
- [X] T003 [P] Add the feature-owned CLI/MCP registration placeholders in `sandbox/commands/sync.py` and `mcp/wp-server/tools/sync.py` without registering behavior yet.
- [X] T004 [P] Add the feature 033 contract fixtures and redaction test fixtures under `tests/fixtures/sync/`.

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the identity, redaction, journal, and manifest boundaries
required by every user story.

- [X] T005 Implement relationship, source-generation, participant, pinned-job, and divergence value objects in `sandbox/sync/models.py`.
- [X] T006 [P] Implement safe identifier, mode, lifecycle, bounded-count, and timestamp validation in `sandbox/sync/models.py`.
- [X] T007 Implement transactional relationship journal storage under `SANDBOX_HOME/runtime/sync` in `sandbox/sync/repository.py`, including private permissions and atomic replacement.
- [X] T008 Implement replay-safe request lookup, request-digest conflict detection, and monotonic generation sequencing in `sandbox/sync/repository.py`.
- [X] T009 [P] Add relationship journal CRUD, concurrent-writer, corruption, and replay tests in `tests/test_sync_state.py`.
- [X] T010 Implement stable local manifest capture with Git-relative paths, file metadata, aggregate digest, bounded size/path limits, and a second-view race check in `sandbox/sync/capture.py`.
- [X] T011 Implement ordinary exclusions for `.git`, node modules, build output, runtime state, databases, uploads, caches, logs, unsafe paths, and symlinks in `sandbox/sync/policy.py`.
- [X] T012 Implement fail-closed credential screening across tracked, modified, untracked, and explicitly included inputs in `sandbox/sync/policy.py`; credential findings must reject the complete generation.
- [X] T013 [P] Add manifest, exclusion, tracked-secret, untracked-secret, explicit-include, symlink, size-limit, and unstable-capture tests in `tests/test_sync_manifest.py` and `tests/test_sync_capture.py`.
- [X] T014 Add bounded redacted success/failure envelope helpers matching `specs/033-agent-aware-remote-sync/contracts/cli-mcp.md` in `sandbox/sync/models.py`.
- [X] T015 [P] Add contract redaction and malformed-envelope tests in `tests/test_sync_transport.py`.

**Checkpoint**: Foundation is ready when manifest validation and journal tests
pass without contacting a remote or mutating a project checkout.

## Phase 3: User Story 1 - Keep a Disposable Workspace Current (Priority: P1) 🎯 MVP

**Goal**: Transfer one screened local generation to an explicitly selected
disposable remote workspace without full apply/service recreation.

**Independent Test**: `sync once` accepts one generation in a disposable remote
workspace, repeats idempotently, and reports pending/error state when the remote
is unavailable.

### Tests for User Story 1

- [X] T016 [P] [US1] Add one-time generation acceptance and idempotent replay tests in `tests/test_sync_transport.py`.
- [X] T017 [P] [US1] Add remote-unavailable, transport-unknown, and no-service-recreation contract tests in `tests/test_sync_transport.py`.
- [X] T018 [P] [US1] Add `sync once` and `sync status` parser/dispatch tests in `tests/test_sync_cli.py`.

### Implementation for User Story 1

- [X] T019 [US1] Implement staged generation packaging and shell-safe transfer in `sandbox/transports/remote_sync.py`, using the existing bounded remote runner and never writing directly into the active workspace.
- [X] T020 [US1] Implement remote manifest validation, atomic generation publication, and typed acceptance/unknown envelopes in `sandbox/transports/remote_sync.py`.
- [X] T021 [US1] Implement `SyncService.once()` and `SyncService.status()` orchestration in `sandbox/sync/service.py`, including relationship preflight and journal transitions.
- [X] T022 [US1] Implement the application boundary in `sandbox/application/sync_service.py` so CLI and MCP share target, ownership, and redaction semantics.
- [X] T023 [US1] Register `sync once` and `sync status` in `sandbox/commands/sync.py` and `sandbox/commands/manifest.py` with explicit project/remote/workspace selectors.
- [X] T024 [US1] Register equivalent MCP tools in `mcp/wp-server/tools/sync.py` and the MCP composition manifest.
- [X] T025 [US1] Add `sync once` usage, apply-reset warning, and deploy-src interaction documentation in `docs/remote-hosting.md`.
- [ ] T026 [US1] Run the one-time disposable remote acceptance from `specs/033-agent-aware-remote-sync/quickstart.md` and retain generation/request/cleanup evidence.
- [X] T026a [US1] Add the hosted-app `host sync` adapter, project-relative archive manifest, in-place managed-file publication, deletion ownership, and watch CLI contract with focused tests.
- [ ] T026b [US1] Run disposable hosted-app acceptance proving an edit reaches `deploy-src/hosts/<project>` without service restart and that later `host apply` restores the committed revision.

2026-08-29 acceptance note: the revision-matched remote and focused local gate
were ready, and the credential-like negative correctly refused before mutation.
The one-time transfer remained pending after `remote_unavailable` followed by
`transport_unknown` on the exact request replay. The disposable workspace lease
was released. The no-production T026b fixture had no deployed host state, and
the only documented provisioning path would change public routing/DNS, so no
host apply was attempted. Detailed redacted IDs and cleanup evidence are in
`quickstart.md`; T026 and T026b remain unchecked.

**Checkpoint**: User Story 1 is complete only when focused tests pass and the
disposable remote accepts a generation without `host apply` or service
recreation.

## Phase 4: User Story 2 - Choose Deliberate Synchronization Boundaries (Priority: P1)

**Goal**: Add off, checkpoint, live-start, and stop semantics while preserving
deploy-only behavior when synchronization is off.

**Independent Test**: Checkpoint transfers only on explicit request; off mode
does not transfer edits or commits; stop leaves pending state visible.

### Tests for User Story 2

- [X] T027 [P] [US2] Add mode-transition and off-mode non-regression tests in `tests/test_sync_state.py`.
- [X] T028 [P] [US2] Add start/stop/checkpoint CLI tests in `tests/test_sync_cli.py`.
- [X] T029 [P] [US2] Add CLI/MCP mode and stop parity tests in `tests/test_sync_mcp.py`.

### Implementation for User Story 2

- [X] T030 [US2] Implement mode transitions, explicit checkpoint requests, stop behavior, and pending-state preservation in `sandbox/sync/service.py`.
- [X] T031 [US2] Implement bounded live trigger/debounce ownership with one in-flight generation per relationship in `sandbox/sync/coordinator.py`.
- [ ] T032 [US2] Add commit-trigger integration that never blocks, amends, creates, or pushes commits in `sandbox/sync/service.py` and the existing Git event seam.
- [X] T033 [US2] Register `sync start`, `sync stop`, and `sync once --checkpoint` in `sandbox/commands/sync.py` and expose the same operations through MCP.
- [X] T034 [US2] Add documented off/checkpoint/live behavior and apply reset semantics to `docs/remote-hosting.md` and the relevant CLI guide.

**Checkpoint**: User Story 2 is complete when mode tests pass and a disposable
acceptance proves no automatic transfer in checkpoint/off mode.

## Phase 5: User Story 3 - Share One Source Relationship Safely (Priority: P1)

**Goal**: Coordinate agents sharing one canonical worktree and reject competing
worktree ownership before remote mutation.

**Independent Test**: Two same-identity participants coalesce one generation;
different project identity is rejected with no remote source mutation.

### Tests for User Story 3

- [ ] T035 [P] [US3] Add resolved identity, symlink, relocation, fresh-clone, and ownership-conflict tests in `tests/test_sync_state.py`.
- [ ] T036 [P] [US3] Add concurrent participant and duplicate-generation tests in `tests/test_sync_transport.py`.
- [ ] T037 [P] [US3] Add redacted ownership-conflict parity tests in `tests/test_sync_cli.py` and `tests/test_sync_mcp.py`.

### Implementation for User Story 3

- [X] T038 [US3] Implement authoritative relationship lookup by project identity, remote name, and durable workspace ID in `sandbox/sync/repository.py`.
- [X] T039 [US3] Implement participant registration/heartbeat and relationship-level serialization in `sandbox/sync/coordinator.py`.
- [X] T040 [US3] Integrate existing project/workspace identity resolution into `sandbox/application/sync_service.py` without reading registry JSON directly.
- [ ] T041 [US3] Add conflict refusal before transfer and bounded redacted ownership status in `sandbox/transports/remote_sync.py`.
- [X] T042 [US3] Document shared-worktree participation and fresh-clone adoption boundaries in `docs/remote-hosting.md`.

**Checkpoint**: User Story 3 is complete when concurrent local tests pass and
remote acceptance proves a competing identity cannot mutate the workspace.

## Phase 6: User Story 4 - Run Jobs Against Stable Source (Priority: P1)

**Goal**: Pin jobs to accepted generations, queue new jobs behind the newest
pending generation, and protect managed source with read-only/isolated access.

**Independent Test**: Job A runs on generation A, pending B is created, a new
job waits for B, and shared writes cannot alter A or a peer.

### Tests for User Story 4

- [ ] T043 [P] [US4] Add generation pin, newest-pending queue, parallel-safe sharing, and release tests in `tests/test_sync_state.py`.
- [X] T044 [P] [US4] Add job submission/acceptance generation fields and read-only source-policy tests in `tests/test_remote_job_transport.py`.
- [ ] T045 [P] [US4] Add shared-write rejection, isolated-copy output, and out-of-band divergence tests in `tests/test_sync_transport.py`.

### Implementation for User Story 4

- [X] T046 [US4] Extend durable job submission/acceptance metadata with relationship and generation identity in `sandbox/jobs/models.py` and `sandbox/jobs/registry.py`.
- [ ] T047 [US4] Add generation-aware workspace lease and newest-pending queue rules in `sandbox/jobs/scheduler.py` and `sandbox/application/job_service.py`.
- [ ] T048 [US4] Integrate generation acceptance before remote job launch in `sandbox/transports/remote_jobs.py` without changing deploy-only callers.
- [ ] T049 [US4] Add read-only managed-source projection and explicit isolated-copy policy to remote job execution preparation in `sandbox/transports/remote_jobs.py`.
- [ ] T050 [US4] Add divergence detection, explicit resolution gating, and artifact-only isolated output handling in `sandbox/sync/projection.py`.
- [ ] T051 [US4] Add generation fields and source-access policy to CLI/MCP job status and acceptance envelopes in `sandbox/sync/models.py` and `mcp/wp-server/tools/sync.py`.
- [ ] T052 [US4] Document job generation pinning, source-write rejection, and isolated output in `docs/remote-hosting.md` and the job guide.

**Checkpoint**: User Story 4 is complete only after disposable remote job
acceptance verifies generation identity, queueing, read-only source, and
isolated-output behavior.

## Phase 7: User Story 5 - Recover and Inspect Synchronization Safely (Priority: P2)

**Goal**: Reconcile interruptions, lost acknowledgments, credentials, races,
divergence, redaction, and bounded status consistently across CLI/MCP.

**Independent Test**: Interrupt a transfer, replay its request identity, and
exercise each negative outcome without false acceptance or protected output.

### Tests for User Story 5

- [ ] T053 [P] [US5] Add interruption, lost-response replay, retry-bound, and stop-during-transfer tests in `tests/test_sync_state.py`.
- [ ] T054 [P] [US5] Add credential-refusal-before-mutation and remote-divergence tests in `tests/test_sync_transport.py`.
- [X] T055 [P] [US5] Add CLI/MCP status-field equivalence and redaction tests in `tests/test_sync_mcp.py`.

### Implementation for User Story 5

- [ ] T056 [US5] Implement bounded reconciliation of accepted/pending/refused/unknown generations in `sandbox/sync/service.py` and `sandbox/sync/repository.py`.
- [X] T057 [US5] Implement explicit divergence resolution command and confirmation boundary in `sandbox/commands/sync.py` and `sandbox/application/sync_service.py`.
- [X] T058 [US5] Add typed recovery and unknown-acknowledgment envelopes to `sandbox/transports/remote_sync.py`.
- [ ] T059 [US5] Add redaction and sensitive-path/process-argument assertions at every public sync and job boundary in `sandbox/services/redaction.py` and `sandbox/sync/models.py`.
- [X] T060 [US5] Add bounded metrics for aggregate counts/timestamps/bytes without source contents or filenames in `sandbox/sync/repository.py`.
- [ ] T061 [US5] Run the full recovery, credential, divergence, parity, and cleanup acceptance in `specs/033-agent-aware-remote-sync/quickstart.md`.

**Checkpoint**: User Story 5 is complete only when every negative acceptance
result is explicit and no failed or refused generation is reported current.

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Finish compatibility, documentation, release evidence, and queue
closure without claiming unverified remote behavior.

- [X] T062 [P] Update `README.md`, `CLAUDE.md`, `AGENTS.md`, and `docs/remote-hosting.md` with the final CLI/MCP sync contract and safety boundaries.
- [ ] T063 [P] Add command/MCP manifest coverage and package import checks in `tests/test_sync_cli.py` and `tests/test_mcp_composition.py`.
- [ ] T064 [P] Add regression tests proving existing deploy, host apply, and off-mode job paths remain unchanged in `tests/test_remote.py`, `tests/test_remote_job_transport.py`, and `tests/test_cli.py`.
- [X] T065 Run focused sync tests, relevant existing remote/job tests, and `git diff --check` with bounded output.
- [ ] T066 Run the disposable remote quickstart with finite timeouts and preserve job IDs, generation IDs, request IDs, timings, and cleanup evidence in `specs/033-agent-aware-remote-sync/`.
- [ ] T067 Review the `fb17bb5c05c60ef78ce1e33e7a25685b` feedback record against the live evidence and mark it `verified` only if all stated success criteria pass; otherwise record the exact blocked condition.
- [ ] T068 Rebuild `docs/feedback-priority-queue-2026-08-25.md` from a fresh paginated ledger and update only records supported by current evidence.
- [ ] T069 Commit and push the completed non-`main` work to `latest` after required tests and remote acceptance pass.

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; creates feature seams.
- **Foundational (Phase 2)**: Depends on Setup and blocks all story work.
- **User Story 1 (Phase 3)**: Depends on Foundational; MVP.
- **User Story 2 (Phase 4)**: Depends on US1's one-time transfer service.
- **User Story 3 (Phase 5)**: Depends on Foundational and US1's relationship lookup.
- **User Story 4 (Phase 6)**: Depends on US1 and US3; integrates existing jobs.
- **User Story 5 (Phase 7)**: Depends on US1–US4 boundaries.
- **Polish (Phase 8)**: Depends on all desired stories and acceptance evidence.

### User Story Dependencies

- **US1 (P1)**: Foundational only; MVP.
- **US2 (P1)**: US1 transfer/application boundary.
- **US3 (P1)**: Foundational plus US1 identity/status boundary.
- **US4 (P1)**: US1 generation transport plus US3 relationship serialization.
- **US5 (P2)**: All preceding negative/error surfaces.

### Parallel Opportunities

- T002, T003, and T004 can run in parallel after T001.
- T006, T009, T013, T014, and T015 can run in parallel after their model seams exist.
- Within US1, T016–T018 are parallel tests; T025 is parallel with transport tests after the envelope is stable.
- Within US3, identity tests and redaction tests can run in parallel once T038 exists.
- Within US5, recovery, credential, and parity tests can run in parallel.
- Documentation and manifest regression tasks in Phase 8 can run in parallel after behavior stabilizes.

## Parallel Example: User Story 1

```text
Task T016: one-time acceptance/idempotency tests in tests/test_sync_transport.py
Task T017: remote failure/no-restart contract tests in tests/test_sync_transport.py
Task T018: CLI once/status tests in tests/test_sync_cli.py
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Setup and Foundational phases.
2. Implement one-time screened generation transfer and redacted status.
3. Validate against one disposable remote workspace with no service recreation.
4. Stop and review the evidence before enabling live/watch mode.

### Incremental Delivery

1. Add mode/stop behavior after one-time transfer is trusted.
2. Add shared identity and participant serialization.
3. Add generation-pinned jobs and source-write isolation.
4. Add recovery/divergence/parity and perform the complete quickstart.
5. Close the feedback record only from live acceptance evidence.

### Notes

- `[P]` tasks touch different files and have no incomplete dependency.
- Every task has an exact repository path and a story label when required.
- No task authorizes production deployment, broad cleanup, force-push, or secret
  access; those remain outside this feature.
