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

## Phase 8: Convergence — 2026-08-13 (27-feedback CLI/config/output)

These tasks are open follow-up work and intentionally leave all prior checkboxes
unchanged.

- [x] T017 [US1] Add config-home/label matrix regressions for `e11914b5`: root vs
  `.config/sandbox` selection follows Spec 042, explicit missing labels fail
  nonzero without fallback, and omitted labels preserve existing defaults.
- [x] T018 [US1] Add parser-position regressions for `64811859` proving global
  `--label` before and after a subcommand survives normalization unchanged.
- [x] T019 [US2] Add wrapper-less guide discovery and registry-convergence tests
  for `15d1625b` and `b6905052`; fail when the checkout-local `./sb` is assumed
  or a public command is manually documented without registry evidence.
- [x] T020 [US1] Add captured stdout/stderr/exit-status negative and positive
  WordPress assertion tests for `0e2d74b6`, and one-document canonical
  `status --json` tests for `b0d1a1e5` with diagnostics forced to stderr.
- [x] T021 [US1/US2] Add CLI/MCP shared-identity parity coverage for `2b080bf5`
  and assert both adapters use the same canonical project identity service.
- [x] T022 [US1] Extend feedback contract tests for `ad190c71` (detail, filters,
  export, and non-destructive retention planning) and `f90c6712` (omitted vs
  explicit invalid limits) without deleting or rewriting original records.

## Phase 9: Convergence — 2026-08-13 (PHP extension CLI/reporting)

These tasks remain unchecked until implementation and live proof are complete.

- [x] T023 [US1] Add `sb init` regressions proving new WordPress projects emit a
  reviewable `wordpress@1`/no-profile choice, while existing projects with omitted
  `phpExtensions` retain exact legacy configuration and output.
- [x] T024 [US1/US2] Add status/doctor text and JSON fixtures for canonical extension
  state, profile/catalog revision, digest, safe provenance, and web/WP-CLI/exec/PHPUnit
  observations, including each structured failure class and one-document stdout.
  DONE 2026-08-14: constructor, status/doctor text+JSON, stable exits, remote nonzero
  forwarding, safe-output fixtures, and documentation are implemented. A supported
  local WordPress instance then reported ready web/WP-CLI/exec/PHPUnit observations
  with no extension issues through both `sb status --json` and `sb doctor --json`;
  doctor emitted one parseable document and truthfully retained its unrelated
  overall nonzero result.
- [x] T025 [US1] Add generic Compose refusal and secret-safe stdout/stderr tests for a
  present `phpExtensions` field before any image/package/runtime side effect.
