# Tasks: OCI Trust and Verification

**Input**: Design documents from `/specs/049-oci-trust-verification/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required. All acceptance/contract tests are authored and observed RED
before any production source task begins.

## Phase 1: Test Fixtures and Contract Ownership

**Purpose**: Establish test/evidence ownership without creating production packages.

- [ ] T001 Create the test ownership map only in `tests/hosting_image_fixtures.py`; production package creation is deferred to T013
- [ ] T002 [P] Add contract fixture builders matching `specs/049-oci-trust-verification/contracts/verification.md` in `tests/hosting_image_fixtures.py`
- [ ] T003 [P] Add canonical plan fixture builders matching `specs/049-oci-trust-verification/contracts/verified-image-plan.md` in `tests/hosting_image_fixtures.py`
- [ ] T004 Record the pre-implementation test command and expected RED selectors in `specs/049-oci-trust-verification/quickstart.md`

---

## Phase 2: Acceptance Tests First (Blocking RED Gate)

**Purpose**: Define every acceptance boundary before production implementation.

**CRITICAL**: T005-T012 must run and fail for the intended missing behavior before T013.

- [ ] T005 [P] Add canonical valid-plan, shared `DeliveryIdentityProjection`, input-order invariance, visibility-non-claim, and authority-field mutation tests for FR-001-FR-020 in `tests/test_hosting_image_trust.py`
- [ ] T006 [P] Add machine/project/receipt channel-substitution and policy/receipt/provenance mismatch tests for FR-001-FR-010 in `tests/test_hosting_image_trust.py`
- [ ] T007 [P] Add tag/index/registry/platform/configuration/topology negative matrix for FR-004-FR-016 in `tests/test_hosting_image_contracts.py`
- [ ] T008 [P] Add closed-schema, unknown-version, duplicate, nesting, string, collection, byte, and diagnostic bound tests for FR-015-FR-016 in `tests/test_hosting_image_boundaries.py`
- [ ] T009 [P] Add zero credential/network/Docker/process/remote/time/random/persistence effect-witness tests for FR-022-FR-023 in `tests/test_hosting_image_boundaries.py`
- [ ] T010 [P] Add complete `VerifiedImagePlan` consumer mutation/reinterpretation refusal tests for FR-017-FR-024 in `tests/test_hosting_image_contracts.py`
- [ ] T011 [P] Add legacy Feature 047/048 byte-preservation and non-authority tests for FR-025-FR-026 in `tests/test_hosting_image_boundaries.py`
- [ ] T012 Run T005-T011 selectors and record the expected RED causes in `specs/049-oci-trust-verification/quickstart.md`

**Checkpoint**: Acceptance contract is fixed before source implementation.

---

## Phase 3: User Story 1 - Produce One Verified Image Plan (Priority: P1) MVP

**Goal**: Return one canonical immutable plan from matching trusted/untrusted inputs.

**Independent Test**: Equivalent valid inputs return byte-identical plans/digests and zero effects.

- [ ] T013 [P] [US1] Create the production images package as needed and implement bounded digest/platform/repository/service plus canonical `DeliveryIdentityProjection` value types for FR-004-FR-018 in `sandbox/hosting/images/models.py`
- [ ] T014 [P] [US1] Implement closed machine policy, receipt payload, project intent, topology, and plan value types for FR-001-FR-019 in `sandbox/hosting/images/models.py`
- [ ] T015 [US1] Implement domain-separated canonical receipt and plan serialization/digests for FR-007-FR-020 in `sandbox/hosting/images/models.py`
- [ ] T016 [P] [US1] Implement explicit project/machine hosting-image config normalizers for FR-001-FR-016 in `sandbox/config/hosting_images.py`
- [ ] T017 [US1] Register project and machine hosting-image providers through `sandbox/config/manifest.py`
- [ ] T018 [US1] Implement pure successful verification and `VerifiedImagePlan` construction for FR-001-FR-020 in `sandbox/hosting/images/trust.py`
- [ ] T019 [US1] Export only bounded value/verifier interfaces from `sandbox/hosting/images/__init__.py`
- [ ] T020 [US1] Run the US1 selectors in `tests/test_hosting_image_trust.py`

---

## Phase 4: User Story 2 - Refuse Ambiguous or Untrusted Evidence (Priority: P1)

**Goal**: Fail closed with safe bounded results and no partial plan/effect.

**Independent Test**: Every invalid/substitution/bound case refuses and all effect witnesses stay zero.

- [ ] T021 [US2] Implement stable safe refusal projection and allowlisted locations for FR-015-FR-023 in `sandbox/hosting/images/trust.py`
- [ ] T022 [US2] Enforce channel separation, signature non-claim, and impossible relationship refusal for FR-001-FR-016 in `sandbox/hosting/images/trust.py`
- [ ] T023 [US2] Enforce construction-time effect-capability denial for FR-022-FR-023 in `sandbox/hosting/images/trust.py`
- [ ] T024 [US2] Run the US2 selectors in `tests/test_hosting_image_contracts.py` and `tests/test_hosting_image_boundaries.py`

---

## Phase 5: User Story 3 - Hand Off Trust Without Reinterpretation (Priority: P2)

**Goal**: Give Features 050/051 one closed validated plan dependency.

**Independent Test**: Exact plans validate; any mutated, partial, unknown, or legacy envelope refuses without re-verification or effects.

- [ ] T025 [US3] Implement closed plan-envelope validation without trust reinterpretation for FR-024 in `sandbox/hosting/images/models.py`
- [ ] T026 [US3] Add explicit legacy-state rejection adapters with no state reads/writes for FR-025 in `sandbox/hosting/images/trust.py`
- [ ] T027 [US3] Document the Feature 049/050/051 authority boundary and proof non-claims for FR-027-FR-028 in `docs/remote-hosting-implementation.md`
- [ ] T028 [US3] Document operator-facing trust versus staging/runtime/production evidence for FR-026-FR-028 in `docs/remote-hosting.md`
- [ ] T029 [US3] Run the complete Feature 049 focused suite and record US3 evidence in `specs/049-oci-trust-verification/quickstart.md`

---

## Phase 6: Cross-Cutting Validation

- [ ] T030 [P] Run non-opt-in hosting/config regression selectors in `tests/test_hosting.py` and `tests/test_config.py`
- [ ] T031 Run `python3 -m compileall -q sandbox/hosting/images sandbox/config/hosting_images.py` and `git diff --check`, recording results in `specs/049-oci-trust-verification/quickstart.md`
- [ ] T032 Perform human security review of authority channels, canonical digest domains, signature non-claims, effect reachability, and safe result projection against `specs/049-oci-trust-verification/spec.md`
- [ ] T033 Capture source/local results and open live/artifact-proof gates in `specs/049-oci-trust-verification/quickstart.md`

---

## Dependencies & Execution Order

- Phase 1 precedes the RED gate.
- Phase 2 defines all acceptance tests and must complete before any source task T013+.
- US1 provides the plan model/verifier needed by US2 and US3.
- US2 hardens refusal without changing US1 success semantics.
- US3 depends on the closed plan from US1 and refusal contract from US2.
- Cross-cutting validation follows all stories.

## Parallel Opportunities

- T002-T003 can run together.
- T005-T011 are independent test files/fixtures and can run together.
- T013-T014 and T016 can run together after the RED gate.
- Documentation T027-T028 can run together after consumer semantics stabilize.

## Implementation Strategy

The MVP is Phase 1, Phase 2, and US1. Do not proceed to Feature 050 staging until
the complete Feature 049 suite, security review, and plan contract pass. No task in
this feature accesses a live credential, registry, Docker daemon, remote, or production.
