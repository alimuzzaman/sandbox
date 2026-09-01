# Tasks: Immutable Activation and Recovery

**Input**: Design documents from `/specs/051-immutable-activation-recovery/`

**Tests**: Required. All acceptance tests are authored RED before any production task.

**Organization**: Tasks are strictly dependency ordered. Do not start Phase 3 until
Phase 2 tests exist and fail for the intended missing behavior.

## Phase 1: Setup and Contract Fixtures

- [ ] T001 Create closed synthetic Feature 049 plan, Feature 050 proof, activation-policy, topology, init, runtime, edge, and rollback-grant fixtures in tests/fixtures/hosting_image_activation.py
- [ ] T002 Create forbidden trust/broker/credential/helper/pull/build/prune witness fakes in tests/fixtures/hosting_image_activation.py
- [ ] T003 Create deterministic crash-point and target-mutation race harnesses in tests/fixtures/hosting_image_activation.py
- [ ] T004 Document local-only test scope and live remote/edge/production gates in `docs/remote-hosting.md`

---

## Phase 2: Acceptance Tests First (Must Be RED)

- [ ] T005 [US1] Add caller-artifact non-authority, exact machine binding, prepared proof-custody lease/pin before validation, proof-expiry/capacity refusal, shared projection/repository representation, schema/digest/equality, and no-reinterpretation tests covering FR-001–FR-005 in `tests/test_hosting_image_activation_models.py`
- [ ] T006 [US1] Add machine policy narrowing, capability revision, exact target/topology, and fail-before-effect tests covering FR-004–FR-005 in tests/test_hosting_image_activation_policy.py
- [ ] T007 [US1] Add exact local-image preflight, tag/index/alias/build/pull/platform/orphan refusal tests covering FR-012–FR-013 in tests/test_hosting_image_activation_runtime.py
- [ ] T008 [US1] Add exact activation running/config/platform/topology/health/edge proof and incomplete non-success tests covering FR-020–FR-024 in tests/test_hosting_image_activation_service.py
- [ ] T009 [US2] Add init create-without-start and complete pre-start inspection tests covering FR-014–FR-016 in tests/test_hosting_image_activation_init.py
- [ ] T010 [US2] Add init effect-entry, exit/termination receipt, deadline, bounded-stream, cancellation, and no-secret-serialization tests covering FR-017–FR-019 in tests/test_hosting_image_activation_init.py
- [ ] T011 [US2] Add crash tests at every init create/inspect/effect/start/wait/cleanup/receipt boundary proving at-most-once and uncertainty fencing in tests/test_hosting_image_activation_init.py
- [ ] T012 [US3] Add distinct replay-safe `sb host image recover`, failed-apply isolation, two read-only Feature 048 observations, 051-owned non-authorizing provisional, exact pre/post comparison, and exhaustive activate/rollback phase-by-class matrix tests: `neither`/`ambiguous` never promote, `exact_prior` only closes proven pre-effect no-effect without generation advance, and `exact_new` promotes only with every phase-required receipt; cover changed/unavailable non-success, persistence uncertainty, crash replay, and separate atomic result/promotion for FR-028–FR-030 in `tests/test_hosting_image_activation_recovery.py`
- [ ] T013 [US3] Add Feature 048 protected-effect witness tests proving zero init/runtime/edge/trust/broker/credential/pull/build calls in tests/test_hosting_image_activation_recovery.py
- [ ] T014 [US3] Add edge proven-not-entered resume, acceptance-unknown lookup, terminal promotion, fresh-runtime proof, and uncertain-delivery fence tests covering FR-022–FR-024 in tests/test_hosting_image_activation_recovery.py
- [ ] T015 [US4] Add exact zero-init adoption success and no-effect tests covering FR-031–FR-032 in tests/test_hosting_image_activation_service.py
- [ ] T016 [US4] Add init-bearing, caller/project/external/legacy receipt, health-only, stale, and effect-required adoption refusal tests in tests/test_hosting_image_activation_service.py
- [ ] T017 [US5] Add one-generation rollback success, exact previous local image, and same-state-machine tests covering FR-011 and FR-033–FR-035 in tests/test_hosting_image_activation_service.py
- [ ] T018 [US5] Add first-deploy, older-generation, missing image/proof, post-hoc/stale/caller grant, changed config/topology, and uncertain init/data rollback refusals in tests/test_hosting_image_activation_service.py
- [ ] T019 [US5] Add rollback forbidden registry/credential/broker/helper/pull/build/tag/fallback witness tests covering FR-036 in tests/test_hosting_image_activation_service.py
- [ ] T020 Add shared-owner/generation-CAS pairwise race matrix and exact target-owner -> host-state -> stage-ledger lock-order/deadlock tests for activation/adoption/rollback/recovery/apply/sync/login/edge/all registered mutations covering FR-005–FR-007 in tests/test_hosting_image_activation_races.py
- [ ] T021 Add request acceptance-unknown, exact replay, conflict, crash, effect-uncertainty, immutable result, prepared/accepted proof-pin holder/deadline/no-auto-unpin, compaction race, host-acceptance crash promote/cancel, exact terminal release, stage-proof expiry/capacity refusal, forward authority/grant/subject persistence, and tombstone tests covering FR-005, FR-008–FR-010, and FR-025–FR-027 in `tests/test_hosting_image_activation_repository.py`
- [ ] T022 Add activation/rollback single transaction schema plus exhaustive recovery `exact_new|exact_prior|neither|ambiguous` phase-transition tests covering FR-011, FR-030, and every legal/illegal cell in tests/test_hosting_image_activation_repository.py
- [ ] T023 Add old Feature 047/048 opaque-state preservation, sole-outer-writer enforcement, non-authority, non-opt-in hosting, public-result, and CLI compatibility tests in tests/test_hosting_image_activation_cli.py plus RED narrow-export/import-boundary tests for `sandbox.hosting.images.activation` in `tests/test_architecture_boundaries.py`, covering FR-037–FR-041
- [ ] T024 Run all T005–T023 focused suites and record expected RED failures caused only by missing Feature 051 production behavior in specs/051-immutable-activation-recovery/implementation-evidence.md

**Checkpoint**: Acceptance contract is executable and RED. Production work may begin.

---

## Phase 3: Foundational Models, Policy, and Repository

- [ ] T025 Create the activation package, implement its explicit narrow public exports in `sandbox/hosting/images/activation/__init__.py`, and implement closed canonical authority/projection/policy/request/transaction/init/running/generation/forward-rollback-subject/grant/proof-pin-binding/recovery-provisional/result models in `sandbox/hosting/images/activation/models.py`
- [ ] T026 Implement exact Feature 049/050 schema/digest/equality validation without policy-service imports in sandbox/hosting/images/activation/models.py
- [ ] T027 Implement exact machine `ActivationAuthorityBinding`, caller non-authority, projection equality, policy narrowing, topology/init limits, and mutation-capability validation in `sandbox/hosting/images/activation/policy.py`
- [ ] T028 Implement legal activation/adoption/rollback transition table and effect-boundary invariants in sandbox/hosting/images/activation/models.py
- [ ] T029 Implement only the closed nested activation/recovery value codec and candidate-transition validator with authority/proof-pin/grant/subject bindings, recovery provisional, secret-field rejection, exhaustive recovery matrix, and legacy-field round-trip projection in `sandbox/hosting/images/activation/repository.py`; it MUST NOT parse, lock, replace, or fsync outer `hosts.json`
- [ ] T030 Integrate the authenticated Feature 050 proof-custody port while Feature 050 remains sole custody writer; coordinate candidate acceptance with the narrow shared-host transaction port, durable-holder deadline replay, proof refusal, generation expectations, exact replay/conflict, and nested activation tombstones in `sandbox/hosting/images/activation/repository.py` without outer-state mutation authority
- [ ] T031 Extend `sandbox/hosting/recovery/repository.py` as the sole outer `hosts.json` parser/writer/locker with a narrow activation nested read/compare/atomic-commit port that preserves legacy/unknown fields, and register activation/adoption/rollback plus existing target mutation capability names without adding state writes in `sandbox/core/_hosting.py`
- [ ] T032 Run model/policy/repository/race tests T005–T006 and T020–T022 and keep all effect adapters fake in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 4: User Story 2 — Inspectable Init

- [ ] T033 Implement create-without-start init adapter protocol with closed synthetic environment, deadlines, bounded streams, cancellation, and ownership in sandbox/transports/remote_hosting_activation.py
- [ ] T034 Implement exact pre-start image/config/command/mount/network/env-key/privilege/dependency inspection and mismatch removal in sandbox/hosting/images/activation/init_runner.py
- [ ] T035 Implement durable effect-entered handoff, start/wait/termination/cleanup, and canonical init receipt in sandbox/hosting/images/activation/init_runner.py
- [ ] T036 Implement uncertain possible-execution fencing with no automatic replay/adoption/rollback in sandbox/hosting/images/activation/service.py
- [ ] T037 Run all init acceptance/crash/leak tests T009–T011 and scan production imports for credential/broker/helper/pull reachability in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 5: User Story 1 — Exact Activation

- [ ] T038 Implement coherent local/running container observation with unchanged target/daemon/runtime epochs in sandbox/hosting/images/activation/runtime_observer.py
- [ ] T039 Implement exact rendered-topology preflight and no-build/no-pull selected-service replacement adapter in sandbox/transports/remote_hosting_activation.py
- [ ] T040 Implement activation orchestration from durable accept through ordered init, runtime replacement, running/health proof, edge sub-request, and commit in sandbox/hosting/images/activation/service.py
- [ ] T041 Implement immutable edge sub-request replay lookup/resume/promotion/fence composition through the existing edge adapter in sandbox/hosting/images/activation/service.py
- [ ] T042 Add activate command dispatch with explicit project/environment/request/generation/confirmation and bounded result mapping in sandbox/commands/hosting.py
- [ ] T043 Run activation acceptance tests T005–T008 and edge/replay tests T014/T021, recording exact focused evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 6: User Story 3 — Feature 048 Observation Recovery

- [ ] T044 Add bounded Feature 051 pending-transition projection and exact-new/prior/neither/ambiguous read-only observation models with canonical evidence identity and target/runtime epoch boundaries in `sandbox/hosting/recovery/models.py`
- [ ] T045 Add exact pending-transition/generation/fresh-epoch observer eligibility and deterministic evidence identity without failed-apply or Feature 051 effect authority in `sandbox/hosting/recovery/policy.py`
- [ ] T046 Implement the Feature 048 read-only activation observer returning a closed value for both pre/post calls with zero repository/state write in `sandbox/hosting/recovery/service.py`
- [ ] T047 Add distinct replay-safe `sb host image recover` dispatch without changing existing failed-apply recovery arguments/results in `sandbox/commands/hosting.py`
- [ ] T048 Implement the shared-owner/CAS 051 recovery protocol through the sole shared-host transaction port: durable non-authorizing provisional, exact crash resume, immediate second observation, pre/post comparison, exhaustive operation/phase/class matrix, and separate atomic result plus only matrix-legal promotion/clear candidate in `sandbox/hosting/images/activation/repository.py`
- [ ] T049 Run recovery/provisional/crash/changed-evidence and protected-effect tests T012–T014 plus the full existing Feature 048 suite and record evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 7: User Story 4 — Zero-Init Adoption

- [ ] T050 Implement zero-init-only adoption validation, exact no-effect proof, and atomic generation commit in sandbox/hosting/images/activation/service.py
- [ ] T051 Add adopt command dispatch with explicit confirmation and bounded refusal classes in sandbox/commands/hosting.py
- [ ] T052 Run adoption acceptance/refusal/no-effect tests T015–T016 and record evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 8: User Story 5 — One-Generation Rollback

- [ ] T053 Implement deterministic pre-forward `ForwardRollbackSubject`, machine grant validation, acceptance persistence, and terminal generation references without a future-generation subject in `sandbox/hosting/images/activation/policy.py`
- [ ] T054 Implement previous-generation-only local proof selection and rollback through the common transaction/runtime/edge/commit path in sandbox/hosting/images/activation/service.py
- [ ] T055 Add rollback command dispatch with explicit confirmation and bounded refusal classes in sandbox/commands/hosting.py
- [ ] T056 Run rollback acceptance/refusal/forbidden-capability tests T017–T019 and record evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 9: Compatibility, Documentation, and Final Gates

- [ ] T057 Add Feature 051 state/result fields and distinguish 049/050/051/048/local/remote/production evidence in `docs/remote-hosting-implementation.md`
- [ ] T058 Update operator commands and stale Feature 047 image-authority wording while preserving Feature 048 failed-apply recovery contracts in `docs/remote-hosting.md`
- [ ] T059 Run the full T005–T023 acceptance set, all existing hosting/Feature 048 suites, subprocess environment guards, and architecture import scans in specs/051-immutable-activation-recovery/implementation-evidence.md
- [ ] T060 Perform human review of trust/credential unreachability, proof-custody TOCTOU/lock/crash safety, recovery two-observation promotion, init effect fencing, edge uncertainty, shared-owner races, rollback grants, state secrecy, and legacy compatibility in specs/051-immutable-activation-recovery/implementation-evidence.md
- [ ] T061 Record remaining live registered-host, edge, rollback, deployment, and production validation as explicit open gates without claiming readiness in specs/051-immutable-activation-recovery/implementation-evidence.md

## Dependencies and Execution Order

- Feature 049 implementation and its closed `VerifiedImagePlan` contract precede Feature 050.
- Feature 050 implementation and its closed `StagedImageProof` contract precede Feature 051.
- T001–T004 precede all tests; T005–T023 precede every production task T025 onward.
- T025–T032 precede effect adapters and all user-story implementation.
- Init T033–T037 precedes activation T038–T043.
- Recovery T044–T049 depends on the activation transaction/proof implemented in T038–T043.
- Adoption T050–T052 and rollback T053–T056 depend on the common service/state machine.
- Final compatibility and evidence T057–T061 follow all focused implementation phases.

## Parallel Execution

No production implementation should be parallelized across the shared repository/state
machine boundary. After Phase 2, independent test-file refinements may run in parallel only
when one owner integrates the fixtures and verifies the complete ordered gate.

## Implementation Strategy

The MVP is exact activation plus inspectable init and truthful recovery (Phases 1–6).
Zero-init adoption and one-generation rollback remain in the same state machine and follow
only after the core invariants pass. No task authorizes live credentials, registry access,
remote mutation, edge change, deployment, or production use.
