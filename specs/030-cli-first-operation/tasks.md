# Tasks: CLI-first Sandbox operation

**Input**: [spec.md](spec.md), [plan.md](plan.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts](contracts/cli-first-operation.md)

## Phase 1: Setup

- [x] T001 Create feature artifacts in `specs/030-cli-first-operation/`

## Phase 2: Foundational

- [x] T002 Register feature-owned CLI commands in `sandbox/commands/runtime.py`
- [x] T003 Declare Compose execution capability in `sandbox/application/context.py`
- [x] T004 Add command ownership in `sandbox/commands/manifest.py`

## Phase 3: User Story 1 - Generic CLI operation (P1)

**Independent Test**: Generic CLI guide and execution route only through the
declared Compose runtime.

- [x] T005 [US1] Implement `sb exec` argv validation and runtime invocation in `sandbox/commands/runtime.py`
- [x] T006 [US1] Mark `exec` instance-scoped in `sandbox/cli.py`
- [x] T007 [US1] Add public command inventory coverage in `tests/test_cli.py`

## Phase 4: User Story 2 - CLI-first skill (P2)

**Independent Test**: `sb skill show sandbox-cli` gives usable runtime-aware
guidance without MCP setup.

- [x] T008 [US2] Add runtime-aware `sb guide` in `sandbox/commands/runtime.py`
- [x] T009 [US2] Add shipped skill in `skills/sandbox-cli/SKILL.md`
- [x] T010 [US2] Document workflow in `docs/cli-first-operation.md` and `README.md`

## Phase 5: User Story 3 - Automatic delivery policy (P3)

**Independent Test**: Agent guides and constitution agree on automatic
commit/push plus protected actions.

- [x] T011 [US3] Update policy in `AGENTS.md`, `CLAUDE.md`, and `.specify/memory/constitution.md`

## Phase 6: Verification

- [x] T012 Run focused command/composition tests in `tests/test_cli.py` and `tests/test_command_composition.py`
- [x] T013 Run generic Compose live execution from `tests/fixtures/generic-compose`
- [x] T014 Run full test suite and documentation diff checks
- [x] T015 Commit and push verified completed work on the active branch

## Phase 7: Convergence

- [x] T016 Align active fix and MCP task guidance with the automatic commit/push policy while preserving the fix workflow's live-proof boundary per FR-007 and FR-008 (contradicts)
