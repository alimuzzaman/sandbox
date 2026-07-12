# Tasks: Reproducible Hermes Worker Routing

**Input**: Design documents in `specs/020-hermes-worker-routing/`
**Tests**: Required; provisioning is remote configuration and must remain idempotent, non-secret, and non-activating.

## Phase 1: Setup

- [x] T001 Add the routing model, worker profile, role-policy, and owned-marker constants in `sandbox/core/_hermes.py`.

## Phase 2: Foundational Configuration Rendering

- [x] T002 Add helpers that render idempotent, non-secret root and worker profile convergence in `sandbox/core/_hermes.py`.

## Phase 3: User Story 1 - Provision a Routed Hermes Profile (Priority: P1)

**Goal**: Fresh setup creates the coordinator and worker profiles without provider authentication.

**Independent Test**: Mocked setup command converges every model/profile and excludes credential handling.

- [x] T003 [P] [US1] Add fresh-setup and repeat-setup command tests in `tests/test_hermes.py`.
- [x] T004 [US1] Extend `setup()` to configure Spark, Terra direct delegation, and named Luna/Terra/Sol profiles in `sandbox/core/_hermes.py`.

## Phase 4: User Story 2 - Route Work Without Broadening Access (Priority: P2)

**Goal**: Configure dispatch policy but leave gateway activation to the established allowlisted workflow.

**Independent Test**: Rendered setup config enables task routing and contains no gateway install/start or provider-auth command.

- [x] T005 [P] [US2] Add task-board, direct-delegation, and no-activation assertions in `tests/test_hermes.py`.
- [x] T006 [US2] Add coordinator policy-marker replacement and task-board configuration to `sandbox/core/_hermes.py`.

## Phase 5: User Story 3 - Preserve Evidence-Worker Boundaries (Priority: P3)

**Goal**: Luna can read and search local files while role policy prohibits mutation.

**Independent Test**: Luna's generated configuration includes `safe` and `file`, and its policy blocks write/patch/command behavior.

- [x] T007 [P] [US3] Add Luna toolset, no-write policy, and no-secret assertions in `tests/test_hermes.py`.
- [x] T008 [US3] Render Luna's toolset and role policy in `sandbox/core/_hermes.py`.

## Phase 6: Polish and Validation

- [x] T009 [P] Document the worker model map, Luna limitation, provider-auth prerequisite, and explicit gateway activation in `docs/hermes-agent.md`.
- [x] T010 Run `python3 -m unittest tests.test_hermes -v` and `git diff --check`; record results in `specs/020-hermes-worker-routing/quickstart.md`.
- [ ] T011 Perform a separately approved fresh-remote acceptance after provider authentication; do not activate messaging platforms as part of this feature.

## Dependencies and Implementation Strategy

- T001 and T002 block all user stories.
- T003/T004 deliver the MVP.
- T005/T006 and T007/T008 extend the same renderer sequentially after T004.
- T009 can proceed after the routing decisions are represented in code.
- T010 follows all local work; T011 requires separate current approval.

Implement the MVP first, then task-board and Luna policy behavior, then documentation and validation.
