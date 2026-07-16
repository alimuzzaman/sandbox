# Tasks: Test Execution Modes

**Input**: Design documents from `specs/028-test-execution-modes/`

**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/cli-mcp.md`, `quickstart.md`

**Tests**: Required by FR-013. Add focused tests before implementation and keep the
existing full suite as the final regression gate.

## Phase 1: Setup and contract tests

- [x] T001 Add a disposable pure-unit fixture and test doubles for harness/process calls in `tests/fixtures/pure-unit/` and `tests/test_runtime_test_modes.py`.
- [x] T002 [P] Add configuration validation tests for `tests.suite` values and malformed test configuration in `tests/test_project_config.py`.
- [x] T003 [P] Add CLI parser and invalid-combination tests for positional mode and `--provision-only` in `tests/test_cli.py`.
- [x] T004 [P] Add MCP mode schema/forwarding and additive result-field tests in `tests/test_mcp.py`.

## Phase 2: Foundational mode policy

- [x] T005 Implement shared mode constants, configuration normalization, and bounded read-only mode detection in `sandbox/config/wordpress.py` and `sandbox/core/_tests.py`.
- [x] T006 Add path containment and marker-priority tests proving unsafe, mixed, unknown, and WordPress-marked projects resolve to integration in `tests/test_runtime_test_modes.py`.

## Phase 3: User Story 1 — Pure unit execution

- [x] T007 [US1] Factor project Composer/PHPUnit dependency preparation from the WordPress harness runner in `sandbox/core/_tests.py` and `sandbox/core/_herd.py`.
- [x] T008 [US1] Implement Docker and Herd pure-unit runners without suite, polyfill, test database, or `WP_TESTS_*` setup in `sandbox/core/_tests.py` and `sandbox/core/_herd.py`.
- [x] T009 [US1] Route CLI mode resolution before harness provisioning and reject unit plus `--provision-only` in `sandbox/commands/debug.py` and `sandbox/cli.py`.
- [x] T010 [US1] Prove unit runner isolation, Composer passthrough, and failure propagation with the disposable fixture in `tests/test_runtime_test_modes.py`.

## Phase 4: User Story 2 — Integration compatibility

- [x] T011 [US2] Preserve and explicitly route the existing WordPress harness path for integration mode in `sandbox/commands/debug.py` and `sandbox/core/_tests.py`.
- [x] T012 [US2] Add regression tests for default/auto/explicit integration behavior, labels, passthrough arguments, and provision-only in `tests/test_runtime_test_modes.py` and `tests/test_cli.py`.

## Phase 5: User Story 3 — Interface observability

- [x] T013 [US3] Extend MCP `run_tests` with optional mode forwarding, early validation, and additive resolved-mode output in `mcp/wp-server/tools/wp.py`.
- [x] T014 [US3] Update user-facing test-mode documentation and configuration references in `README.md`, `CLAUDE.md`, `docs/sandbox-config-reference.md`, and `docs/sandbox-mcp-tasks.md`.

## Phase 6: Polish and verification

- [x] T015 Run focused mode/config/CLI/MCP tests, the full `./.cli-venv/bin/python -m unittest discover -s tests -q` suite, `git diff --check`, and update `specs/028-test-execution-modes/implementation-evidence.md` with exact results.

## Dependencies

- T001–T004 establish contract coverage and can run in parallel.
- T005–T006 depend on the contract fixtures and block runner work.
- T007–T010 implement and verify the unit path.
- T011–T012 preserve the existing integration path after the shared resolver exists.
- T013–T014 can proceed after the mode contract is stable.
- T015 is the final gate after all implementation and documentation tasks.

## MVP scope

T001–T012 deliver explicit mode selection and the pure-unit/integration runners.
T013–T014 complete the MCP and documentation surfaces; T015 is mandatory before
handoff.
