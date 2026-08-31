# Tasks: Observation-Only Hosting Recovery

## Phase 1: Setup

- [X] T001 Create `sandbox/hosting/recovery/__init__.py`, `models.py`, `policy.py`, `repository.py`, and `service.py` with explicit public exports
- [X] T002 [P] Add focused empty test modules `tests/test_host_recovery_models.py`, `tests/test_host_recovery_policy.py`, `tests/test_host_recovery_repository.py`, `tests/test_host_recovery_service.py`, and `tests/test_host_recovery_cli.py`

## Phase 2: Foundational

- [X] T003 Add strict bounded recovery identities, phases, evidence, attempts, result classes, and canonical digests in `sandbox/hosting/recovery/models.py`
- [X] T004 [P] Add model validation, size, duplicate, redaction, and canonical-digest tests in `tests/test_host_recovery_models.py`
- [X] T005 Add authoritative durable job/request/source descriptor fields in `sandbox/application/job_service.py`
- [X] T006 Add fixed child context injection without environment enumeration/copying in `sandbox/jobs/supervisor.py`
- [X] T007 [P] Prove descriptor/context behavior and no raw environment persistence in `tests/test_job_service.py` and `tests/test_job_supervisor.py`
- [X] T008 Add per-target bounded lock, reload-under-lock generation CAS, atomic attempt/receipt commit, compaction, and tombstones in `sandbox/hosting/recovery/repository.py`
- [X] T009 [P] Add crash, race, CAS, replay, compaction, tombstone, permission, and legacy-state tests in `tests/test_host_recovery_repository.py`
- [X] T010 Add machine-local keyed opaque secret-binding identities without value output in `sandbox/hosting/recovery/models.py` and `sandbox/core/_secrets.py`
- [X] T011 [P] Add secret binding key loss/change, weak-value privacy, and no-value persistence tests in `tests/test_host_recovery_models.py`

## Phase 3: User Story 1 - Exact Receipt-Only Reconciliation (P1)

**Goal**: Reconcile one eligible exact failed apply with zero protected effects.

**Independent Test**: Exact current-contract failure commits one receipt-only result and advances once; replay returns it unchanged.

- [X] T012 [US1] Add pre-effect hosting operation acceptance and bounded phase persistence to `sandbox/commands/hosting.py`
- [X] T013 [US1] Extend the single bounded host observer with stable epoch markers, config-file digests, and exact persistent/one-shot image IDs in `sandbox/commands/hosting.py`
- [X] T014 [P] [US1] Add pure failed-job/binding/source/config/image/topology/service/phase eligibility classification in `sandbox/hosting/recovery/policy.py`
- [X] T015 [P] [US1] Add the complete exact and legacy eligibility matrix in `tests/test_host_recovery_policy.py`
- [X] T016 [US1] Implement job-ledger binding, host identity projection, observation, atomic receipt reconciliation, and exact replay in `sandbox/hosting/recovery/service.py`
- [X] T017 [P] [US1] Add receipt-only mutation witnesses, generation-once, disconnect replay, and historical Lenzora legacy refusal tests in `tests/test_host_recovery_service.py`
- [X] T018 [US1] Register `host recover` arguments, expose status generation/latest recovery, and add versioned text/JSON rendering in `sandbox/cli.py` and `sandbox/commands/hosting.py`
- [X] T019 [P] [US1] Add CLI required-field, exit-code, schema, stable-class, and privacy contract tests in `tests/test_host_recovery_cli.py`

## Phase 4: User Story 2 - Fail-Closed Drift and Partial Evidence (P1)

**Goal**: Refuse every changed, partial, torn, or mutation-requiring target before effects.

**Independent Test**: All negative cells leave source/Compose/image/init/migration/DNS/Caddy witnesses untouched.

- [X] T020 [US2] Add changed host/runtime/config/secret/image/topology/service/one-shot and torn-epoch classifiers in `sandbox/hosting/recovery/policy.py`
- [X] T021 [P] [US2] Add same-tag/different-image, repointed host, changed runtime path, secret version, duplicate, truncated, timeout, and external-change cases in `tests/test_host_recovery_policy.py`
- [X] T022 [US2] Enforce clean recovery source regardless of ordinary dirty-apply policy and normal-apply handoff for mutation-required outcomes in `sandbox/hosting/recovery/service.py`
- [X] T023 [P] [US2] Add all protected-effect refusal witnesses and bounded public error evidence in `tests/test_host_recovery_service.py`

## Phase 5: User Story 3 - Confirmed Edge-Only Continuation (P2)

**Goal**: Continue only a proven sole pending edge through a distinct confirmed request.

**Independent Test**: Eligible confirmation reaches only the existing edge adapter; every other case reaches no effect.

- [X] T024 [US3] Add separate observation-reference, evidence, resulting-generation, confirmation, and governance checks in `sandbox/hosting/recovery/policy.py`
- [X] T025 [US3] Extract a narrow existing-edge continuation adapter from `_apply_host` in `sandbox/commands/hosting.py` without changing edge behavior
- [X] T026 [US3] Implement immediate epoch revalidation, edge-only dispatch, terminal observation, and effect-unknown fencing in `sandbox/hosting/recovery/service.py`
- [X] T027 [P] [US3] Add confirmation, stale reference, broader incomplete phase, governance, edge success/failure/unknown, and zero-runtime-effect tests in `tests/test_host_recovery_service.py` and `tests/test_host_recovery_cli.py`

## Phase 6: User Story 4 - Concurrency and Uncertainty Recovery (P2)

**Goal**: Preserve one owner/generation and prevent repeated uncertain effects across crashes and retention.

**Independent Test**: Races, owner death, partial commits, expired evidence, and stale-looking locks never create second authority.

- [X] T028 [US4] Persist active-operation and uncertainty phase boundaries around observation, commit, and edge dispatch in `sandbox/hosting/recovery/repository.py` and `service.py`
- [X] T029 [P] [US4] Add apply/recovery races, process death, persistence fault, lock release, partial commit, expiry, and aged uncertain-edge replay tests in `tests/test_host_recovery_repository.py` and `tests/test_host_recovery_service.py`
- [X] T030 [US4] Make ordinary `host apply` acquire the shared target lock and generation fence before its first hosting effect in `sandbox/commands/hosting.py`
- [X] T031 [P] [US4] Add apply/recovery single-flight and generation-conflict integration tests in `tests/test_hosting.py`

## Phase 7: Polish and Cross-Cutting Validation

- [X] T032 Update public workflow, contract, refusal, activation, and proof boundaries in `docs/remote-hosting.md` and `docs/remote-hosting-implementation.md`
- [X] T033 Update CLI-first recovery guidance in `skills/sandbox-cli/SKILL.md` and command help in `sandbox/cli.py`
- [X] T034 Run direct security review for auth/binding, HMAC key lifecycle, redaction, path/lock ownership, command construction, CAS, and edge effect uncertainty across `sandbox/hosting/recovery/`, `sandbox/commands/hosting.py`, and durable supervisor changes
- [X] T035 Run the focused test list in `specs/048-host-observation-recovery/quickstart.md`, compile changed Python, and run `git diff --check`
- [X] T036 Record exact local evidence and remaining disposable/live/runtime/Lenzora activation gates in `specs/048-host-observation-recovery/implementation-evidence.md`

## Dependencies

- Setup precedes Foundational.
- Foundational blocks every user story.
- US1 is the MVP and blocks US3.
- US2 may proceed after Foundational alongside US1 policy tests, then integrates with US1 service.
- US4 repository work may proceed after Foundational; shared apply integration follows US1.
- Polish follows all selected stories.

## Parallel Examples

- After T003, run T004, T007, T009, and T011 in separate files.
- During US1, T014/T015 can proceed while T012/T013 prepare apply/observer evidence.
- During US2, T021 can proceed from policy contracts while T022 changes service orchestration.
- During US3, CLI tests in T027 can be drafted while T025 extracts the edge adapter.
- During US4, repository fault tests in T029 can proceed while T030 integrates apply locking.

## Implementation Strategy

1. Land strict models, durable context, repository/CAS, and security primitives.
2. Deliver US1 receipt-only recovery as the MVP; legacy jobs refuse.
3. Complete the negative matrix before any edge path.
4. Add separately confirmed edge-only continuation.
5. Harden crash/race/retention behavior, docs, review, and focused evidence.

All 36 tasks use the required checkbox/ID/label/path format.
