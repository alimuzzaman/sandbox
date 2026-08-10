# Tasks: Hermes State Sync

**Input**: Design documents from `specs/017-hermes-state-sync/`

## Phase 1: Setup

- [X] T001 Add state repository options and help text in `sandbox/cli.py`
- [X] T002 Add state command dispatch and result envelopes in `sandbox/commands/hermes.py`

## Phase 2: Foundational

- [X] T003 [P] Add state repository validation, manifest schema, and safe path allowlist in `sandbox/core/_hermes.py`
- [X] T004 [P] Add secret-like content and symlink rejection tests in `tests/test_hermes.py`

## Phase 3: User Story 1 - Rebuildable State (Priority: P1)

**Independent Test**: Restore a fixture repository into a clean remote and verify the
non-secret profile and memory state while provider authentication remains absent.

- [X] T005 [P] [US1] Add restore command contract tests in `tests/test_hermes.py`
- [X] T006 [US1] Implement staged manifest fetch and atomic restore in `sandbox/core/_hermes.py`
- [X] T007 [US1] Invoke state restore from `sandbox/core/_hermes.py::setup` when configured
- [X] T008 [US1] Document setup restore and operator authentication in `docs/hermes-agent.md`

## Phase 4: User Story 2 - Publish State Changes (Priority: P2)

**Independent Test**: Change an allowlisted file, run sync, and verify exactly one
sanitized commit; no-change sync creates no commit.

- [X] T009 [P] [US2] Add setup/sync CLI contract tests in `tests/test_hermes.py`
- [X] T010 [US2] Implement local staged export, secret scan, commit, and push in `sandbox/core/_hermes.py`
- [X] T011 [US2] Persist per-remote state repository configuration without tokens in `sandbox/core/_hermes.py`
- [X] T012 [US2] Document sync command, branch behavior, and no-change behavior in `docs/hermes-agent.md`

## Phase 5: User Story 3 - Secret-Safe Boundaries (Priority: P3)

**Independent Test**: Seed forbidden paths and token-shaped fixtures and verify no
commit or remote mutation occurs.

- [X] T013 [P] [US3] Add forbidden path/content regression tests in `tests/test_hermes.py`
- [X] T014 [US3] Add bounded redaction/error reporting without secret output in `sandbox/core/_hermes.py`
- [X] T015 [US3] Add security exclusions and rebuild verification to `specs/017-hermes-state-sync/quickstart.md`

## Phase 6: Polish

- [X] T016 Run the Hermes unit suite and full test suite; record evidence in `specs/017-hermes-state-sync/quickstart.md`
- [X] T017 Review the final diff for unrelated changes and update `docs/hermes-agent.md`

## Dependencies

- T001-T004 precede all user stories.
- US1 precedes setup-triggered restore validation in US2.
- US3 hardens both US1 and US2 and must complete before release.
- T016-T017 follow all implementation tasks.

## Phase 7: Convergence

- [X] T018 Validate state manifests and revisions before restore; reject symlinks, unexpected paths, and secret-like content; and roll back the staged state swap atomically per US1/AC and US3/AC (partial)
- [X] T019 Serialize state repository mutations, export only owned paths, and persist a stable manifest schema/revision per US2/AC and US3/AC (partial)
