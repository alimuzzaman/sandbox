# Tasks: Lenzora TODO Worker

**Input**: [spec.md](spec.md), [plan.md](plan.md)

## Phase 1: Catalog and Worktree Foundation

- [X] T001 Add failing enabled/disabled catalog and Lenzora managed-worktree tests in `tests/test_hermes.py` and `tests/test_hermes_catalog_integrity.py`.
- [X] T002 Generalize managed agent worktree preparation in `sandbox/core/_hermes.py` for the Lenzora TODO worker without weakening clean-tree or lock checks.

## Phase 2: User Story 1 — Progress TODO Work (Priority: P1)

**Goal**: Run one bounded Lenzora root-TODO task per scheduled execution.

**Independent Test**: A catalog preview renders only the TODO worker with its dedicated Lenzora worktree and Terra/Medium route.

- [X] T003 [US1] Replace active Lenzora dispatch with the guarded `lenzora-todo-task` catalog entry in `sandbox/hermes/cron-catalog.json`.
- [X] T004 [US1] Add prompt, route, rendering, and reconciliation tests for `lenzora-todo-task` in `tests/test_hermes.py` and `tests/test_hermes_catalog_integrity.py`.

## Phase 3: User Story 2 — Safe No-Work State (Priority: P2)

**Goal**: Missing or empty TODO state does not create failure or mutation.

**Independent Test**: A live verified run either advances one actionable task or reports the explicitly blocking prerequisite with no mutation.

- [X] T005 [US2] Document enabled-job policy, root TODO format, activation boundaries, and recovery in `docs/hermes-agent.md`.
- [ ] T006 [US2] Run focused tests, full self-test, synchronized live reconciliation, verified no-work execution, and record sanitized evidence in `specs/026-lenzora-todo-worker/`.

## Dependencies and Execution Order

- T001 before T002.
- T002 before T003 because reconciliation must prepare the new worktree.
- T003 before T004–T006.
- T006 is the final live acceptance gate.
