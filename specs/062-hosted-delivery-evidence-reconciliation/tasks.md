# Tasks: Hosted Delivery Evidence Reconciliation

**Input**: plan.md, spec.md, research.md, data-model.md, contracts/reconcile.md, quickstart.md
**Tests**: required (tests first within each story).
**Gate**: start after 054 T040-T047 and 060 T027/T028; 064 journal results needed for edge-rollback release facts (until then those cases keep the fence).

## Phase 1: Setup

- [ ] T001 Create `sandbox/delivery/reconcile.py`, `sandbox/delivery/phase_evidence.py`, `sandbox/delivery/fence_release.py`; register contracts in `tests/test_architecture_boundaries.py`
- [ ] T002 [P] Fixtures: scripted observation port, scripted image record port, two-clone project, 054 attempt fixtures in `tests/delivery_reconcile_support.py`

## Phase 2: Foundational

- [ ] T003 [P] Schema tests for new tables, single-writer constraint, bounded evidence in `tests/test_delivery_reconcile.py`
- [ ] T004 Add tables and repository methods in `sandbox/delivery/repository.py`
- [ ] T005 [P] Phase evidence tests: pre-attempt state at admission, started/finished per phase, rollback facts from 064 result in `tests/test_delivery_fence_release.py`
- [ ] T006 Record phase evidence from `sandbox/commands/hosting.py` apply/edge/compose/initializer steps and image activation

## Phase 3: US1 Lost client, landed deploy converges (P1)

- [ ] T007 [P] [US1] Classification tests for every row of research R3, zero remote writes in every class, concurrent reconciles adopt once in `tests/test_delivery_reconcile.py`
- [ ] T008 [US1] Implement `reconcile.run`/`classify`; hosting observation port wrapping `_observe_host_runtime`; writes under 060 TargetLease through 054/051 paths
- [ ] T009 [US1] `host reconcile` CLI and MCP `host_reconcile`; pre-admission reconcile in `sandbox/delivery/admission.py` with `--no-reconcile`
- [ ] T010 [P] [US1] Job tests: supervisor_lost transport event, delivery_outcome on interrupted job, waiter reconciles once and exits by outcome in `tests/test_delivery_job_outcome.py`
- [ ] T011 [US1] Job registry field and waiter behavior in `sandbox/jobs/registry.py` and the job-start wait path

## Phase 4: US2 Fences release themselves (P1)

- [ ] T012 [P] [US2] Release rule tests (FR-023..FR-028), pre-feature records keep fence, refusal names missing item and exits, notice line in `tests/test_delivery_fence_release.py`
- [ ] T013 [US2] Implement `fence_release.evaluate` and wire into admission

## Phase 5: US3 Any clean checkout (P2)

- [ ] T014 [P] [US3] Scope tests: hosted scope by declared project; lookup from second clone; collision warning; refusals name callable commands in `tests/test_delivery_identity_scope.py`
- [ ] T015 [US3] Hosted `request_scope` change in `sandbox/delivery/models.py`; evidence fields; inspect/retire lookups
- [ ] T016 [US3] Scope-key step inside 060 `sandbox/hosting/state_partition/conversion.py` (alias bindings, batches, resumable); older-reader `converted` marking

## Phase 6: US4 Image request-bound record (P2)

- [ ] T017 [P] [US4] Tests: committed adopts / recovery_no_effect proves with corroboration; contradiction → diverged; incomplete → insufficient/unavailable in `tests/test_delivery_reconcile.py`
- [ ] T018 [US4] Image record port over `sandbox/hosting/images`/`recovery` request records

## Phase 7: US5 Retry same revision (P3)

- [ ] T019 [P] [US5] Tests: new request id linked to failed one; history keeps both; replay unchanged in `tests/test_delivery_retry_link.py`
- [ ] T020 [US5] Retry linking in `sandbox/commands/hosting.py` apply admission

## Phase 8: Polish

- [ ] T021 Docs: `docs/remote-hosting.md` (reconcile, release rules, identity), `docs/remote-job-runtime.md` (waiter), CLAUDE.md gotcha, CHANGELOG.md
- [ ] T022 Run `./sb selftest` and the architecture test
- [ ] T023 Live proof per quickstart on a disposable remote

## Dependencies

Phase 2 before stories. US2 needs US1's classify. US3's T016 needs 060 conversion. US4 needs T008.

## MVP

US1 + US2 (T001-T013).
