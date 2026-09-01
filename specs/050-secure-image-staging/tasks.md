# Tasks: Secure Private Image Staging

**Input**: Design documents from `/specs/050-secure-image-staging/`

**Prerequisites**: Completed Feature 049 plan contract, plan.md, spec.md, research.md,
data-model.md, contracts/

**Tests**: Required. All acceptance tests are written and observed RED before production source.

## Phase 1: Test Fixtures and Ownership

- [ ] T001 Record staging production-file ownership in `tests/hosting_image_fixtures.py`; do not create production modules before T015
- [ ] T002 [P] Add safe plan/policy/request/proof fixture builders in `tests/hosting_image_fixtures.py`
- [ ] T003 [P] Add fake broker/helper/daemon/process-tree/ledger witnesses in `tests/hosting_image_fixtures.py`
- [ ] T004 Record the pre-implementation focused selectors in `specs/050-secure-image-staging/quickstart.md`

---

## Phase 2: Acceptance Tests First (Blocking RED Gate)

**CRITICAL**: T005-T014 must run and fail for missing behavior before T015.

- [ ] T005 [P] Add exact plan/staging-policy authority and pre-credential refusal tests for FR-001-FR-007 in `tests/test_hosting_image_staging_policy.py`
- [ ] T006 [P] Add fixed recipient/helper/capability and caller-substitution tests for FR-008-FR-014 in `tests/test_hosting_image_staging_policy.py`
- [ ] T007 [P] Add exact replay/conflict/generation/acceptance-unknown, 64-total-proof/4096-tombstone/64-live-pin/16-MiB admission, unconditional tombstone-full new-unique-request `retention_full` with retained replay preserved, prepared/accepted proof custody, durable activation-owner/request holder, expired-before-acceptance refusal, post-deadline promotion of committed acceptance, no process/recovery adoption, lock order, crash reconciliation, same-owner promote/cancel/release, compaction exclusion, `proof_expired`, and unsafe-ID-reuse tests for FR-005-FR-007, FR-021-FR-027, and FR-035-FR-037 in `tests/test_hosting_image_staging_repository.py`
- [ ] T008 [P] Add credential canary and every forbidden-surface/temporary-cleanup path test for FR-009-FR-013 and FR-028 in `tests/test_hosting_image_staging_secrets.py`
- [ ] T009 [P] Add transient-unit/cgroup-v2 identity, no-delegation/escape, double-fork, timeout, cancellation, signal, crash, `populated=0`/removal, and fence tests for FR-014-FR-017 in `tests/test_hosting_image_staging_process.py`
- [ ] T010 [P] Add exact pull/no-tag/no-build/no-fallback tests for FR-018 in `tests/test_remote_hosting_images.py`
- [ ] T011 [P] Add same target/daemon epoch anonymous-denial/authenticated-pull, RepoDigest/config/platform/image-ID/topology proof and drift matrix for FR-019-FR-020 in `tests/test_remote_hosting_images.py`
- [ ] T012 [P] Add canonical full `StagedImageProof`, unchanged Feature 049 projection/repository representation, machine/target/daemon, helper/capability, topology, requested/observed identity, observation/staging generation, replay/expiry, privacy, and downstream-validation tests for FR-025-FR-027 in `tests/test_hosting_image_staging_service.py`
- [ ] T013 [P] Add zero Compose/init/runtime/edge/adoption/rollback/prune reachability and legacy compatibility tests for FR-029-FR-034 in `tests/test_hosting_image_staging_service.py`
- [ ] T014 Run T005-T013 selectors and record expected RED causes in `specs/050-secure-image-staging/quickstart.md`

**Checkpoint**: Full staging acceptance is fixed before implementation.

---

## Phase 3: User Story 1 - Stage the Exact Authorized Image (Priority: P1) MVP

**Independent Test**: One exact request pulls once, proves one coherent local identity, commits once, and reaches zero activation witnesses.

- [ ] T015 [US1] Create production staging modules and implement closed policy/request/projection/image-observation/full-proof/tombstone/proof-custody lease-pin models, durable activation-owner/request holder identity, admission-deadline rules, and exact total-capacity predicate for FR-001-FR-005, FR-019-FR-027, and FR-035-FR-037 in `sandbox/hosting/images/staging_models.py`
- [ ] T016 [US1] Implement exact plan/policy/target/helper/broker/capability admission before effects for FR-001-FR-008 in `sandbox/hosting/images/staging_policy.py`
- [ ] T017 [US1] After T015, implement its closed schemas in the authenticated stage ledger: generation CAS, 64-total-proof/4096-tombstone/64-live-pin/16-MiB admission predicate, pre-effect `retention_full`, prepared/accepted proof custody, durable-holder/deadline replay rules, cross-store lock order, crash reconciliation, idempotent same-owner promote/cancel/release, compaction exclusion, `proof_expired`, and atomic durability for FR-005-FR-007, FR-021-FR-027, and FR-035-FR-037 in `sandbox/hosting/images/staging_repository.py`
- [ ] T018 [P] [US1] Implement fixed non-secret remote stage frames and result parser for FR-014, FR-018-FR-020, and FR-032 in `sandbox/transports/remote_hosting_images.py`
- [ ] T019 [US1] Implement anonymous exact-manifest denial, authenticated exact digest pull, and coherent projection/topology/target/daemon local observation for FR-018-FR-020 in `sandbox/hosting/images/staging_worker.py`
- [ ] T020 [US1] Implement service orchestration from durable accept through proof commit for FR-001-FR-027 in `sandbox/hosting/images/staging_service.py`
- [ ] T021 [US1] Add confirmation-gated stage dispatch and safe envelope projection for FR-005-FR-007 and FR-032 in `sandbox/commands/hosting.py`
- [ ] T022 [US1] Run `tests.test_hosting_image_staging_policy`, `tests.test_hosting_image_staging_repository`, and exact success selectors in `tests/test_hosting_image_staging_service.py`

---

## Phase 4: User Story 2 - Keep Credentials Inside the Fixed Boundary (Priority: P1)

**Independent Test**: Canary reaches only broker/helper private channel/volatile workspace and every terminal-safe path leaves zero artifact/descendant.

- [ ] T023 [US2] Add the fixed GHCR repository-read staging adapter without generic secret exposure for FR-008-FR-011 in `sandbox/secrets/service.py`
- [ ] T024 [US2] Implement measured fixed helper entry, closed synthetic environment, bounded credential frame, and volatile owner-only workspace validation for FR-010-FR-015 in `sandbox/hosting/images/staging_helper.py`
- [ ] T025 [US2] Implement mandatory credential cleanup and secret-free result framing on every terminal/signal path for FR-011-FR-013 in `sandbox/hosting/images/staging_helper.py`
- [ ] T026 [US2] Provision and report the exact helper artifact/runtime revision through the supported installer for FR-014 in `scripts/install-remote.sh`
- [ ] T027 [US2] Integrate broker lease lifetime strictly around helper pull/cleanup for FR-008-FR-014 and FR-028 in `sandbox/hosting/images/staging_service.py`
- [ ] T028 [US2] Run all credential canary/cleanup selectors in `tests/test_hosting_image_staging_secrets.py`

---

## Phase 5: User Story 3 - Replay and Reconcile Without Duplicate Helpers (Priority: P2)

**Independent Test**: Every interruption yields immutable replay, safe exact resume, or durable fence; never a duplicate uncontrolled helper.

- [ ] T029 [US3] Implement unique transient systemd service/cgroup-v2 ownership, no-delegation capability gate, finite deadlines, whole-unit cancellation/kill, and inactive-plus-empty/removal proof for FR-015-FR-017 in `sandbox/hosting/images/staging_worker.py`
- [ ] T030 [US3] Persist pre-effect/effect-entered/process/cleanup boundaries and exact resume rules for FR-021-FR-024 in `sandbox/hosting/images/staging_repository.py`
- [ ] T031 [US3] Implement fresh local reconciliation before any possible-effect replay for FR-023-FR-024 in `sandbox/hosting/images/staging_service.py`
- [ ] T032 [US3] Run `tests.test_hosting_image_staging_process` and repository crash/replay selectors in `tests/test_hosting_image_staging_repository.py`

---

## Phase 6: User Story 4 - Hand Off a Closed Staged Proof (Priority: P2)

**Independent Test**: Exact proof validates byte-for-byte; every mutation/legacy/stale proof refuses with zero broker/helper calls.

- [ ] T033 [US4] Implement the downstream validator/export over the T015 proof and custody value models, including canonical proof digest, unchanged shared projection, closed serialization, and finite saturation/expiry result validation for FR-025-FR-027 and FR-035-FR-037 in `sandbox/hosting/images/staging_models.py`
- [ ] T034 [US4] Export only plan-validation, stage-request, stage-result, and proof-validation interfaces from `sandbox/hosting/images/__init__.py`
- [ ] T035 [US4] Document the 049/050/051 and credential/non-credential boundaries for FR-031-FR-034 in `docs/remote-hosting-implementation.md`
- [ ] T036 [US4] Document stage operation, results, evidence limits, and no-activation claim for FR-031-FR-034 in `docs/remote-hosting.md`
- [ ] T037 [US4] Run proof mutation and zero-activation selectors in `tests/test_hosting_image_staging_service.py`

---

## Phase 7: Cross-Cutting Validation

- [ ] T038 [P] Run existing secret-broker and durable-job regression selectors in `tests/test_secret_service.py` and `tests/test_job_service.py`
- [ ] T039 [P] Run existing hosting/recovery compatibility selectors in `tests/test_hosting.py` and `tests/test_host_recovery_service.py`
- [ ] T040 Run the complete Feature 050 suite listed in `specs/050-secure-image-staging/quickstart.md`
- [ ] T041 Run `python3 -m compileall -q sandbox/hosting/images sandbox/transports/remote_hosting_images.py` and `git diff --check`, recording results in `specs/050-secure-image-staging/quickstart.md`
- [ ] T042 Perform human credential, helper supply-chain, process-tree, replay, local-proof, lease/pin handoff, lock-order, finite-retention, and no-activation security review against `specs/050-secure-image-staging/spec.md`
- [ ] T043 Record source/local evidence and leave live secret/GHCR/remote/deployment gates explicit in `specs/050-secure-image-staging/quickstart.md`

## Dependencies & Execution Order

- Feature 049 must be complete before Feature 050 implementation.
- Phase 2 acceptance tests precede every production task T015+.
- US1 establishes policy/ledger/pull/proof core.
- US2 hardens credentials/helper and integrates into US1.
- US3 depends on US1 ledger and US2 process boundary.
- US4 depends on stable proof semantics from US1-US3.
- Cross-cutting validation follows all stories.

## Parallel Opportunities

- T002-T003, T005-T013, T015/T018, and T038-T039 are independent groups; T017 depends on T015.
- Documentation T035-T036 can proceed together after proof semantics stabilize.

## Implementation Strategy

MVP is Setup + all RED acceptance + US1. Do not begin Feature 051 activation until
US2-US4, complete local checks, and human credential/process review pass. Live GHCR or
remote acceptance remains separately authorized.
