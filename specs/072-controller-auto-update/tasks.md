# Tasks: Controller Auto-Update

**Input**: Design documents from `specs/072-controller-auto-update/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Requested (owner: types first, then tests, then implementation; temp git repos only). Tests and type checks run only in Haiku helpers.

**Organization**: by user story; US1-US3 are P1 and share the engine, so they form the MVP together.

## Phase 1: Setup

- [x] T001 Create package skeleton `sandbox/controller_update/__init__.py` with a module docstring naming spec 072

## Phase 2: Foundational (blocks every story)

- [x] T002 Define types in `sandbox/controller_update/models.py`: `parse_revision`, `Hint`, `Refusal`, `CheckoutState`, `SkipReason` (with `notice` flag), the `Decision` union (`NoAction`, `Skip`, `Updated`, `RolledBack`, `Stranded`, `AlreadyMoved`), `Policy` defaults per data-model.md
- [x] T003 [P] Write state-store tests in `tests/test_controller_update_state.py`: round trip, malformed file and entries ignored, per-remote replace, refusal match/expiry, file mode 0600, best-effort writers never raise
- [x] T004 Implement `sandbox/controller_update/state.py` per contracts/state-file.md (atomic replace, sidecar flock, `$SANDBOX_HOME/runtime/controller-update/`)
- [x] T005 Implement bounded git helpers in `sandbox/controller_update/gitops.py`: clean `GIT_*` env, `GIT_TERMINAL_PROMPT=0`, stdin devnull, timeouts; `read_checkout`, `is_ancestor`, `has_commit`, `fetch_upstream`, `ff_merge`, `changed_paths`, `rollback`

## Phase 3: User Story 1 - catch up with a newer published remote (P1, MVP)

**Goal**: a behind, clean, tracking checkout fast-forwards to the hinted published revision and re-runs once.

**Independent Test**: temp origin + clone one commit behind + hint; one evaluation moves HEAD to the hint and reports `Updated`.

- [x] T006 [P] [US1] Engine tests in `tests/test_controller_update_engine.py`: fast-forward to exact hint not past upstream tip; equal/older hint is `NoAction`; local branch `installed` tracking `origin/main`; refresh runs only when a venv exists; MCP requirements change adds the notice
- [x] T007 [US1] Implement `evaluate()` and `apply()` in `sandbox/controller_update/engine.py` (candidate selection across hints, lock, re-check under lock, fetch, containment, ff, refresh via `python -m sandbox.controller_update.refresh`)
- [x] T008 [US1] Implement `sandbox/controller_update/refresh.py` calling `sandbox.core._config._ensure_cli_requirements` for `.cli-venv`
- [x] T009 [P] [US1] Hook tests in `tests/test_controller_update_hook.py`: remote-capable classification, updated notice, `execve` called with `ENTRY`, original argv and `SANDBOX_AUTO_UPDATE_REEXEC=1` (exec injected)
- [x] T010 [US1] Implement `sandbox/controller_update/hook.py` `maybe_auto_update(args, argv)` per contracts/cli-behavior.md
- [x] T011 [US1] Call the hook from `sandbox/cli.py` right after `parse_args`
- [x] T012 [P] [US1] Remote receipt tests in `tests/test_controller_update_remote.py`: install command contains the receipt write after `trap - EXIT`; probe parses `remote_source=`; status gains `installed_source_revision`; hint recorded with `_remote_name` (ssh stubbed)
- [x] T013 [US1] Implement receipt write, probe read and hint recording in `sandbox/core/_remote.py`

## Phase 4: User Story 2 - a server can never choose the code (P1)

**Independent Test**: hints for a foreign commit, a malformed value, an off-branch commit and a diverged history never move HEAD.

- [x] T014 [P] [US2] Engine tests: commit only in another repo -> `not_on_upstream`; on another origin branch -> `not_on_upstream`; diverged -> `not_descendant`; malformed -> ignored with no fetch; fetch failure -> `fetch_failed` and memo; refusal memo suppresses refetch for 6 h; non-GitHub origin refused
- [x] T015 [US2] Make the engine pass T014 in `sandbox/controller_update/engine.py`

## Phase 5: User Story 3 - pinned or unsafe checkouts left alone (P1)

**Independent Test**: detached, dirty, staged, no upstream, lock held each leave HEAD unchanged with the matching notice.

- [x] T016 [P] [US3] Engine tests: detached -> pinned notice (only when hint is a known descendant); modified tracked file and staged change -> dirty; no upstream; lock held by another process -> locked; not a git repo -> silent; another process already moved HEAD -> `AlreadyMoved`
- [x] T017 [US3] Make the engine pass T016

## Phase 6: User Story 4 - failed refresh rolls back (P2)

- [x] T018 [P] [US4] Engine tests: refresh failure -> `RolledBack`, HEAD back at old; rollback impossible -> `Stranded` with recovery notice
- [x] T019 [US4] Implement rollback (`read-tree -m -u` then `update-ref` CAS) in `sandbox/controller_update/gitops.py` and wire it in the engine

## Phase 7: User Story 5 - opt-out (P2)

- [x] T020 [P] [US5] Hook tests: `SANDBOX_AUTO_UPDATE=0/off/false/no` -> no git calls; config `defaults.controller_auto_update: false` -> no action; env `1` beats config `false`
- [x] T021 [US5] Implement policy resolution in `sandbox/controller_update/hook.py`

## Phase 8: User Story 6 - machine runs and job children (P2)

- [x] T022 [P] [US6] Hook tests: `--json` args, `SANDBOX_DURABLE_JOB_ID`, `SANDBOX_AUTO_UPDATE_REEXEC`, non-`sb` entry -> no action and nothing written to stdout or stderr
- [x] T023 [US6] Implement the gating order in `sandbox/controller_update/hook.py`

## Phase 9: Polish and cross-cutting

- [x] T024 [P] End-to-end test `tests/test_controller_update_e2e.py` per quickstart.md section 2 (real `./sb` from a temp clone; skipped when `git` is missing)
- [x] T025 [P] Write `docs/controller-auto-update.md` (trigger, trust, skip reasons, opt-outs, machine-output decision, receipt, Follow latest one-time steps)
- [x] T026 [P] Cross-link from `docs/remote-service-cli.md` and `docs/sandbox-config-reference.md` (`defaults.controller_auto_update`, `SANDBOX_AUTO_UPDATE`)
- [x] T027 [P] Add a short controller auto-update section to `skills/sandbox-cli/SKILL.md` and `.agents/skills/sandbox-cli/SKILL.md`
- [x] T028 Add gotcha 34 to `CLAUDE.md` for the hook placement and receipt contract
- [ ] T029 Haiku helper runs the new tests plus the remote/CLI suites they touch; fix failures
- [ ] T030 Luna review of the head; fix or answer each finding

## Dependencies

- T002 blocks everything. T004, T005 block the engine (T007).
- US1 (T006-T013) is the MVP; US2 and US3 extend the same engine and tests.
- US4 needs T007; US5 and US6 need T010.
- Polish after all stories.

## Parallel examples

- After T002: T003, T006, T009, T012 (test files) in parallel.
- Docs T025-T027 in parallel with T024.

## Implementation strategy

Types, then all test files, then implementation in story order, then docs and
review. Commit once the suites pass.
