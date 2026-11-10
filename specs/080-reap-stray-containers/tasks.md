# Tasks: Reclaim Stray Unlabeled Containers Owned by a Workspace

**Input**: Design documents from `specs/080-reap-stray-containers/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/stray-containers.md

**Tests**: Requested by the owner (types first, then tests, then code). Tests run only
on xspeed-dev through `sb job-start --remote xspeed-dev`, inside Haiku helpers, with an
`origin/latest` baseline for comparison.

## Phase 1: Setup

- [x] T001 Push `fix/reap-stray-containers` with `git push -u origin fix/reap-stray-containers` and check `git rev-parse --abbrev-ref @{upstream}` prints `origin/fix/reap-stray-containers`
- [x] T002 Capture an `origin/latest` baseline run of `tests.test_reclaim_project_teardown`, `tests.test_resource_reclaim_service` and `tests.test_resource_remote` on xspeed-dev (Haiku helper; report under `/Users/alim/Sites/git/.scratch/sandbox/reap-stray/`)

## Phase 2: Foundational (types and the one ownership rule)

- [x] T003 Add `assess_stray_container(container, checkout)` and `MAX_STRAYS_PER_CHECKOUT` signatures with docstrings (StrayAssessment shape from data-model.md) in `sandbox/resources/workspace_teardown.py`
- [x] T004 Add `remove_containers: tuple[dict, ...] = ()` to `ReclaimCandidate`, its `to_dict`, and the stray fingerprints in `evidence_digest` in `sandbox/resources/reclaim.py`

## Phase 3: User Story 2 - protected containers stay protected (P1)

**Goal**: A container that might belong to someone else is never offered or touched.

**Independent Test**: one assessment per protection rule; planner keeps the workspace with `bound_container_protected`.

- [x] T005 [P] [US2] Tests for every protecting rule of `assess_stray_container` (bind outside, named and anonymous volume, tmpfs, user network, host network, restart policy, compose/stack/sandbox label, `sandbox-` name, sibling-prefix path, symlink escaping the checkout, malformed record) in `tests/test_reclaim_stray_containers.py`
- [x] T006 [P] [US2] Planner tests: protected stray, foreign Compose project, missing `stray` evidence, too many strays each keep the workspace in `skipped` with `protected_containers` in `tests/test_reclaim_stray_containers.py`
- [x] T007 [US2] Implement the rules in `assess_stray_container` in `sandbox/resources/workspace_teardown.py`
- [x] T008 [US2] Planner classification of bound containers in `tier_candidates` in `sandbox/resources/reclaim.py`

## Phase 4: User Story 1 - a finished workspace with a leftover stray is reclaimed (P1)

**Goal**: plan lists the stray with evidence; cleanup records, stops, removes it, then removes the checkout.

**Independent Test**: teardown in strays mode with a fake engine removes the stray after its intent record and returns ok.

- [x] T009 [P] [US1] Tests: owned stray assessment and fingerprint; planner emits `remove_containers`; teardown strays mode records intent before stop, stops then removes, keeps project teardown; reclaim request carries `remove_containers` in `tests/test_reclaim_stray_containers.py`
- [x] T010 [US1] Probe attaches de-duplicated container records with `stray` assessments for workspace entries in `reclaim_inventory` in `sandbox/resources/remote.py`
- [x] T011 [US1] Inject `assess_stray_container` into `_program` in `sandbox/resources/remote.py`
- [x] T012 [US1] Strays mode in `teardown_workspace_stack(..., strays=None, checkout=None)` in `sandbox/resources/workspace_teardown.py`
- [x] T013 [US1] `reclaim_run` passes `remove_containers` and the checkout to teardown and records stray intents with fingerprints in `sandbox/resources/remote.py`
- [x] T014 [US1] `ReclaimService.cleanup` forwards `remove_containers` in `sandbox/resources/reclaim_service.py`

## Phase 5: User Story 3 - only planned containers, changed ones refused (P1)

- [x] T015 [P] [US3] Tests: restarted, new mount, new label, replaced id, unplanned bound unlabeled container, planned stray no longer bound, compose-labelled bound container untouched, inspect failure; each refuses with no stop/rm in `tests/test_reclaim_stray_containers.py`
- [x] T016 [US3] Fresh enumeration and evidence comparison before any mutation in `teardown_workspace_stack` in `sandbox/resources/workspace_teardown.py`

## Phase 6: User Story 4 - crash rerun is idempotent (P2)

- [x] T017 [P] [US4] Tests: rerun after intent-only crash removes once; stopped-but-not-removed stray still matches; absent planned stray counts as removed; stop/rm failure keeps checkout with the failing id in `tests/test_reclaim_stray_containers.py`
- [x] T018 [US4] Absent and already-stopped handling in strays mode in `sandbox/resources/workspace_teardown.py`

## Phase 7: User Story 5 - plan-id forms (P3)

- [x] T019 [P] [US5] Test: `--tier safe` with a `tmp` plan refuses `plan_kind_mismatch` and leaves the plan `planned` in `tests/test_reclaim_stray_containers.py`
- [x] T020 [US5] Refuse tier/plan mismatch before `begin` in `ReclaimService.cleanup` in `sandbox/resources/reclaim_service.py`
- [x] T021 [US5] Document both plan-id forms in `docs/resource-monitoring.md` and `skills/sandbox-cli/SKILL.md`

## Phase 8: Polish

- [x] T022 Document stray reclaim, its protections and the migrate requirement in `docs/resource-monitoring.md` and CLAUDE.md gotcha 23
- [x] T023 Mark spec 077 R10 as followed up by spec 080 in `specs/077-stack-lifetime/research.md`
- [ ] T024 Run the touched suites on xspeed-dev (Haiku helper) and compare with the T002 baseline
- [x] T025 Dry `sb resources plan --remote xspeed-dev --tier safe --json` for evidence (read-only, no cleanup)
- [ ] T026 Luna review of the head; fix or answer every finding; second fresh Luna review of the final head
- [ ] T027 Commit, push `fix/reap-stray-containers`, hand back to the orchestrator

## Dependencies

- T003-T004 before every story. US2 (T005-T008) before US1 (the planner must protect before it offers). US3 and US4 build on T012. US5 is independent.
- Parallel: T005/T006/T009/T015/T017/T019 are in one test file but separate classes; written together before code.

## Implementation Strategy

MVP is US2 + US1 + US3 together: the reclaim must not ship without its protections and
the changed-since-plan refusal. US4 and US5 follow in the same change.
