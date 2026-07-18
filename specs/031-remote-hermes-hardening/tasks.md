# Tasks: Remote and Hermes Operations Hardening

**Input**: Design documents from `/specs/031-remote-hermes-hardening/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required by the feature specification. Add focused regression fixtures
before each corresponding implementation and run the complete applicable suite at the
end.

**Organization**: Tasks are grouped by user story so each can be tested independently.

## Phase 1: Setup

**Purpose**: Freeze current unsafe and legacy behavior as sanitized fixtures.

- [x] T001 Inventory remote lifecycle parser/command contracts in `sandbox/cli.py`, `sandbox/commands/remote.py`, and `tests/test_remote.py`.
- [x] T002 [P] Inventory Hermes health, reconciliation, and verification contracts in `sandbox/core/_hermes.py` and `tests/test_hermes.py`.
- [x] T003 [P] Define sanitized service, scheduler, and terminal-result test fixtures in `tests/test_remote.py` and `tests/test_hermes.py`.

---

## Phase 2: Foundational Contracts

**Purpose**: Create shared, non-secret models and guarded command shape before any mutation logic.

- [x] T004 Add non-secret remote-service record validation and safe bind policy helpers in `sandbox/core/_remote.py`.
- [x] T005 Add explicit `remote service` parser registration and plan/confirm validation in `sandbox/cli.py`.
- [x] T006 Add remote-service command dispatch with redacted result formatting in `sandbox/commands/remote.py`.
- [x] T007 [P] Add environment-only remote-service token-source validation in `mcp/wp-server/server.py`.
- [x] T008 Add foundational redaction, invalid-bind, and no-write contract tests in `tests/test_remote.py` and `tests/test_server_transport.py`.

**Checkpoint**: Shared service contract is safe, explicit, and testable.

---

## Phase 3: User Story 1 - Safely operate a remote MCP service (Priority: P1) MVP

**Goal**: Migrate lifecycle from detached process/argv credential handling to an owned,
confirmation-gated service whose stop scope is proven.

**Independent Test**: Service fixtures prove read-only planning, owner-only credential
handling, selected-unit stop, and unrelated-process survival.

- [x] T009 [P] [US1] Write unit rendering, credential secrecy, and public-bind rejection tests in `tests/test_remote.py`.
- [x] T010 [P] [US1] Write selected-unit ownership and unrelated streamable-HTTP survival tests in `tests/test_remote.py`.
- [x] T011 [US1] Render owner-only credential and Sandbox-owned systemd user-service artifacts in `sandbox/core/_remote.py`.
- [x] T012 [US1] Implement read-only service status and migration-plan evidence in `sandbox/core/_remote.py`.
- [x] T013 [US1] Implement `--confirm` service migration/apply with bounded rollback in `sandbox/core/_remote.py`.
- [x] T014 [US1] Route existing `remote up`/`remote down` through selected-unit ownership checks and remove broad argv process termination from `sandbox/core/_remote.py`.
- [x] T015 [US1] Expose service status/migration/lifecycle results through `sandbox/commands/remote.py` and `sandbox/cli.py`.
- [x] T016 [US1] Add remote MCP environment-token behavior and compatibility tests in `tests/test_server_transport.py`.

**Checkpoint**: Remote service lifecycle works locally through fixtures without touching a registered remote.

---

## Phase 4: User Story 2 - See truthful operations health (Priority: P1)

**Goal**: Return independently observable component facts and stable degradation codes.

**Independent Test**: Each recovery, gateway, scheduler, catalog, session, and worktree
fixture produces the documented reason without concealing another component's facts.

- [x] T017 [P] [US2] Write health fixtures for disabled recovery, inactive service, scheduler unavailability, drift, stale sessions, and dirty worktrees in `tests/test_hermes.py`.
- [x] T018 [P] [US2] Write remote-service health projection tests in `tests/test_remote.py` and `tests/test_hermes_gateway.py`.
- [x] T019 [US2] Add a shared component-health fact and aggregate reason-code model in `sandbox/core/_hermes.py`.
- [x] T020 [US2] Integrate remote service/recovery, gateway ownership, linger, scheduler, cron, session, and worktree evidence into `sandbox/core/_hermes.py`.
- [x] T021 [US2] Preserve stable redacted health envelopes in `sandbox/commands/hermes.py` and relevant MCP projections.

**Checkpoint**: `hermes health` is truthful for all local fixture states.

---

## Phase 5: User Story 3 - Reconcile Hermes cron without losing state (Priority: P2)

**Goal**: Preserve fail-closed legacy detection and add verified rollback around confirmed force replacement.

**Independent Test**: A fake scheduler proves blocked planning, exact convergence, and
restoration after an injected post-removal failure.

- [x] T022 [P] [US3] Write transaction tests for preflight failure, snapshot failure, exact convergence, restore success, and rollback failure in `tests/test_hermes.py`.
- [x] T023 [US3] Add protected prior-inventory snapshot and bounded metadata helpers in `sandbox/core/_hermes.py`.
- [x] T024 [US3] Add reconciliation preflight and exact postcondition verification in `sandbox/core/_hermes.py`.
- [x] T025 [US3] Add restore-on-post-removal-failure flow and `rolled_back`/`rollback_failed` result states in `sandbox/core/_hermes.py`.
- [x] T026 [US3] Preserve plan-only and explicit-confirm behavior in `sandbox/commands/hermes.py` and `sandbox/cli.py`.

**Checkpoint**: Cron migration is safe and reversible in fixtures; it never triggers a job.

---

## Phase 6: User Story 4 - Classify terminal agent results correctly (Priority: P2)

**Goal**: Distinguish documented terminal results from genuine provider, protocol, or work failures.

**Independent Test**: All approved markers classify correctly, while provider errors,
malformed output, and missing transitions remain failures.

- [x] T027 [P] [US4] Write terminal-result, provider precedence, malformed-output, and missing-transition tests in `tests/test_hermes.py`.
- [x] T028 [US4] Add versioned documented terminal-result grammar and bounded classifier in `sandbox/hermes/scheduler.py`.
- [x] T029 [US4] Integrate classifier evidence into `cron_verify`, cron health, and CLI/MCP envelopes in `sandbox/core/_hermes.py` and `sandbox/commands/hermes.py`.
- [x] T030 [US4] Update verification output/redaction assertions in `tests/test_hermes.py`.

**Checkpoint**: False wrapper errors are distinguishable without masking genuine failures.

---

## Phase 7: Polish and Cross-Cutting Concerns

- [x] T031 [P] Update remote service migration, health, and rollback guidance in `docs/remote-hosting.md` and `docs/hermes-agent.md`.
- [x] T032 [P] Update feature artifacts and managed agent context in `specs/031-remote-hermes-hardening/` and `CLAUDE.md`.
- [x] T033 Run focused contract tests in `tests/test_remote.py`, `tests/test_hermes.py`, `tests/test_hermes_gateway.py`, and `tests/test_server_transport.py`.
- [x] T034 Run the applicable full unit suite and read-only CLI probes from `specs/031-remote-hermes-hardening/quickstart.md`.
- [ ] T035 Perform separately approved disposable-remote reboot, listener-scope, selected-unit-stop, and optionally cron-migration acceptance; record only sanitized evidence in `docs/remote-hermes-operations-prd.md` or a follow-up note.

## Dependencies & Execution Order

- Setup → Foundational → US1/US2/US3/US4 → Polish.
- US2 requires the remote-service fact model from US1.
- US3 and US4 can proceed after Foundational and share Hermes test fixtures, but their
  `_hermes.py` edits should land sequentially to avoid conflict.
- T035 requires explicit current authorization and is intentionally not a prerequisite
  for local code completion.

## Parallel Opportunities

- T002/T003 can run alongside T001.
- T004 and T007 may proceed independently after the current contract inventory.
- The test-first pairs T009/T010, T017/T018, T022, and T027 touch separate fixture
  areas and can be prepared in parallel with their corresponding implementation design.
- Documentation tasks T031/T032 can run once interfaces stabilize.

## Implementation Strategy

1. Deliver the safe remote service contract and fixtures (US1), then prove it before
   touching legacy lifecycle code.
2. Deliver health facts (US2) so operational state is observable before introducing
   destructive-recovery rollback paths.
3. Deliver cron transaction (US3) and classifier (US4), validating each with isolated
   scheduler fixtures.
4. Finish docs and local checks. Seek a separate approval only for disposable remote
   acceptance; do not infer it from implementation authorization.
