# Tasks: Host Exec

**Input**: Design documents from `specs/072-host-exec/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/host-exec-cli.md

**Tests**: requested (the brief requires types first, then tests, then
implementation, and a stdin canary test). Tests run only in Haiku helpers on
the `xspeed-dev` remote.

## Phase 1: Setup

- [x] T001 Create empty modules with docstrings: `sandbox/hosting/target_resolution.py`, `sandbox/hosting/host_exec.py`, `sandbox/commands/host_exec.py`

## Phase 2: Foundational (types)

- [x] T002 Define `ResolvedTarget` and refusal code constants in `sandbox/hosting/target_resolution.py` per data-model.md
- [x] T003 [P] Define `ExecRequest`, `ExecOutcome`, timeout bounds and outcome codes in `sandbox/hosting/host_exec.py` per data-model.md

## Phase 3: User Story 3 - target resolution for stacks and read-only commands (P2, but a prerequisite of US1)

**Goal**: `--target`/`--name` resolve a recorded stack or plain target.

**Independent Test**: `host logs --remote R --target P/E` from a template checkout returns logs.

- [x] T004 [P] [US3] Write resolution tests in `tests/test_host_target_resolution.py`: stack by target, stack by name, plain via project dir, template-without-stack refusal, unrecorded target refusal, environment mismatch, stack dir outside `stacks/`
- [x] T005 [US3] Implement `resolve()` in `sandbox/hosting/target_resolution.py`
- [x] T006 [US3] Write CLI tests in `tests/test_host_target_resolution.py` that `cmd_host` logs/status/diagnose with `--target` use the stack's project dir
- [x] T007 [US3] Wire resolution into `cmd_host` for logs/status/diagnose in `sandbox/commands/hosting.py`; update `--target`/`--name` help in `sandbox/cli.py`

## Phase 4: User Story 1 - exec with stdin (P1)

**Goal**: `host exec` runs argv in the target's service with stdin passed through.

**Independent Test**: canary on stdin reaches the child only; exit status and `--json` match the child.

- [x] T008 [P] [US1] Write tests in `tests/test_host_exec.py`: remote command shape (quoting, no `-u`, `--profile '*'`, `timeout`), argv split, timeout bounds, busy refusals, unknown service, exit propagation (0, 3, timeout, 255), `--json` envelope and truncation, audit fields, and the stdin canary test that scans argv, environment, audit and the whole runtime tree
- [x] T009 [US1] Implement `split_passthrough`, `remote_command`, `busy_reason`, `run`, `append_audit` in `sandbox/hosting/host_exec.py`
- [x] T010 [US1] Implement `cmd_host_exec` in `sandbox/commands/host_exec.py`
- [x] T011 [US1] Dispatch `exec` from `cmd_host` in `sandbox/commands/hosting.py`; add `exec` to the host action choices and call `split_passthrough` in `main()` in `sandbox/cli.py`

## Phase 5: User Story 2 - WP-CLI on a hosted WordPress target (P1)

- [x] T012 [US2] Add a test in `tests/test_host_exec.py` that `--service wordpress -- wp option get siteurl` builds the expected remote command with no user flag

## Phase 6: Polish

- [x] T013 [P] Document `host exec` and `--target`/`--name` resolution in `docs/remote-hosting.md`; update the stack note in `docs/sandbox-config-reference.md`
- [x] T014 [P] Update `skills/sandbox-cli/SKILL.md` and `.agents/skills/sandbox-cli/SKILL.md`
- [x] T015 Run the new and touched test modules plus baseline comparison through Haiku helpers on `xspeed-dev`
- [x] T016 Luna review of the head; fix and re-review
- [x] T017 Live check on `xspeed-dev` target `xspeed-hub-test-stacks/sandbox` service `hub` with a stdin canary; grep job records and audit

## Dependencies

T001 → T002/T003 → US3 (T004-T007) → US1 (T008-T011) → US2 (T012) → T013-T017.
US1 depends on US3's resolver.

## Parallel examples

- T002 and T003 (different files).
- T004 and T008 tests can be written together once types exist.
- T013 and T014 docs.

## Implementation strategy

MVP is US3 + US1 together: exec needs the resolver, and the resolver alone
fixes feedback `da5d522e`.
