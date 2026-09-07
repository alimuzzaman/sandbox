# Tasks: Immutable Activation and Recovery

**Input**: Design documents from `/specs/051-immutable-activation-recovery/`

**Tests**: Required. All acceptance tests are authored RED before any production task.

**Organization**: Tasks are strictly dependency ordered. Do not start Phase 3 until
Phase 2 tests exist and fail for the intended missing behavior.

## Phase 1: Setup and Contract Fixtures

- [x] T001 Create closed synthetic Feature 049 plan, Feature 050 proof, activation-policy, topology, init, runtime, edge, and rollback-grant fixtures in tests/fixtures/hosting_image_activation.py
- [x] T002 Create forbidden trust/broker/credential/helper/pull/build/prune witness fakes in tests/fixtures/hosting_image_activation.py
- [x] T003 Create deterministic crash-point and target-mutation race harnesses in tests/fixtures/hosting_image_activation.py
- [x] T004 Document local-only test scope and live remote/edge/production gates in `docs/remote-hosting.md`

---

## Phase 2: Acceptance Tests First (Must Be RED)

- [x] T005 [US1] Add caller-artifact non-authority, exact machine binding, prepared proof-custody lease/pin before validation, proof-expiry/capacity refusal, shared projection/repository representation, schema/digest/equality, and no-reinterpretation tests covering FR-001–FR-005 in `tests/test_hosting_image_activation_models.py`
- [x] T006 [US1] Add machine policy narrowing, capability revision, exact target/topology, and fail-before-effect tests covering FR-004–FR-005 in tests/test_hosting_image_activation_policy.py
- [x] T007 [US1] Add exact local-image preflight, tag/index/alias/build/pull/platform/orphan refusal tests covering FR-012–FR-013 in tests/test_hosting_image_activation_runtime.py
- [x] T008 [US1] Add exact activation running/config/platform/topology/health/edge proof and incomplete non-success tests covering FR-020–FR-024 in tests/test_hosting_image_activation_service.py
- [x] T009 [US2] Add init create-without-start and complete pre-start inspection tests covering FR-014–FR-016 in tests/test_hosting_image_activation_init.py
- [x] T010 [US2] Add init effect-entry, exit/termination receipt, deadline, bounded-stream, cancellation, and no-secret-serialization tests covering FR-017–FR-019 in tests/test_hosting_image_activation_init.py
- [x] T011 [US2] Add crash tests at every init create/inspect/effect/start/wait/cleanup/receipt boundary proving at-most-once and uncertainty fencing in tests/test_hosting_image_activation_init.py
- [x] T012 [US3] Add distinct replay-safe `sb host image recover`, failed-apply isolation, two read-only Feature 048 observations, 051-owned non-authorizing provisional, exact pre/post comparison, and exhaustive activate/rollback phase-by-class matrix tests: `neither`/`ambiguous` never promote, `exact_prior` only closes proven pre-effect no-effect without generation advance, and `exact_new` promotes only with every phase-required receipt; cover changed/unavailable non-success, persistence uncertainty, crash replay, and separate atomic result/promotion for FR-028–FR-030 in `tests/test_hosting_image_activation_recovery.py`
- [x] T013 [US3] Add Feature 048 protected-effect witness tests proving zero init/runtime/edge/trust/broker/credential/pull/build calls in tests/test_hosting_image_activation_recovery.py
- [x] T014 [US3] Add edge proven-not-entered resume, acceptance-unknown lookup, terminal promotion, fresh-runtime proof, and uncertain-delivery fence tests covering FR-022–FR-024 in tests/test_hosting_image_activation_recovery.py
- [x] T015 [US4] Add exact zero-init adoption success and no-effect tests covering FR-031–FR-032 in tests/test_hosting_image_activation_service.py
- [x] T016 [US4] Add init-bearing, caller/project/external/legacy receipt, health-only, stale, and effect-required adoption refusal tests in tests/test_hosting_image_activation_service.py
- [x] T017 [US5] Add one-generation rollback success, exact previous local image, and same-state-machine tests covering FR-011 and FR-033–FR-035 in tests/test_hosting_image_activation_service.py
- [x] T018 [US5] Add first-deploy, older-generation, missing image/proof, post-hoc/stale/caller grant, changed config/topology, and uncertain init/data rollback refusals in tests/test_hosting_image_activation_service.py
- [x] T019 [US5] Add rollback forbidden registry/credential/broker/helper/pull/build/tag/fallback witness tests covering FR-036 in tests/test_hosting_image_activation_service.py
- [x] T020 Add shared-owner/generation-CAS pairwise race matrix and exact target-owner -> host-state -> stage-ledger lock-order/deadlock tests for activation/adoption/rollback/recovery/apply/sync/login/edge/all registered mutations covering FR-005–FR-007 in tests/test_hosting_image_activation_races.py
- [x] T021 Add request acceptance-unknown, exact replay, conflict, crash, effect-uncertainty, immutable result, prepared/accepted proof-pin holder/deadline/no-auto-unpin, compaction race, host-acceptance crash promote/cancel, exact terminal release, stage-proof expiry/capacity refusal, forward authority/grant/subject persistence, and tombstone tests covering FR-005, FR-008–FR-010, and FR-025–FR-027 in `tests/test_hosting_image_activation_repository.py`
- [x] T022 Add activation/rollback single transaction schema plus exhaustive recovery `exact_new|exact_prior|neither|ambiguous` phase-transition tests covering FR-011, FR-030, and every legal/illegal cell in tests/test_hosting_image_activation_repository.py
- [x] T023 Add old Feature 047/048 opaque-state preservation, sole-outer-writer enforcement, non-authority, non-opt-in hosting, public-result, and CLI compatibility tests in tests/test_hosting_image_activation_cli.py plus RED narrow-export/import-boundary tests for `sandbox.hosting.images.activation` in `tests/test_architecture_boundaries.py`, covering FR-037–FR-041
- [x] T024 Record the explicit user waiver of RED-first execution in specs/051-immutable-activation-recovery/implementation-evidence.md; no RED suite was observed

**Checkpoint**: Acceptance contract is executable and RED. Production work may begin.

---

## Phase 3: Foundational Models, Policy, and Repository

- [x] T025 Create the activation package, implement its explicit narrow public exports in `sandbox/hosting/images/activation/__init__.py`, and implement closed canonical authority/projection/policy/request/transaction/init/running/generation/forward-rollback-subject/grant/proof-pin-binding/recovery-provisional/result models in `sandbox/hosting/images/activation/models.py`
- [x] T026 Implement exact Feature 049/050 schema/digest/equality validation without policy-service imports in sandbox/hosting/images/activation/models.py
- [x] T027 Implement exact machine `ActivationAuthorityBinding`, caller non-authority, projection equality, policy narrowing, topology/init limits, and mutation-capability validation in `sandbox/hosting/images/activation/policy.py`
- [x] T028 Implement legal activation/adoption/rollback transition table and effect-boundary invariants in sandbox/hosting/images/activation/models.py
- [x] T029 Implement only the closed nested activation/recovery value codec and candidate-transition validator with authority/proof-pin/grant/subject bindings, recovery provisional, secret-field rejection, exhaustive recovery matrix, and legacy-field round-trip projection in `sandbox/hosting/images/activation/repository.py`; it MUST NOT parse, lock, replace, or fsync outer `hosts.json`
- [x] T030 Integrate the authenticated Feature 050 proof-custody port while Feature 050 remains sole custody writer; coordinate candidate acceptance with the narrow shared-host transaction port, durable-holder deadline replay, proof refusal, generation expectations, exact replay/conflict, and nested activation tombstones in `sandbox/hosting/images/activation/repository.py` without outer-state mutation authority
- [x] T031 Extend `sandbox/hosting/recovery/repository.py` as the sole outer `hosts.json` parser/writer/locker with a narrow activation nested read/compare/atomic-commit port that preserves legacy/unknown fields, and register activation/adoption/rollback plus existing target mutation capability names without adding state writes in `sandbox/core/_hosting.py`
- [x] T032 Run model/policy/repository/race tests T005–T006 and T020–T022 and keep all effect adapters fake in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 4: User Story 2 — Inspectable Init

- [x] T033 Implement create-without-start init adapter protocol with closed synthetic environment, deadlines, bounded streams, cancellation, and ownership in sandbox/transports/remote_hosting_activation.py
- [x] T034 Implement exact pre-start image/config/command/mount/network/env-key/privilege/dependency inspection and mismatch removal in sandbox/hosting/images/activation/init_runner.py
- [x] T035 Implement durable effect-entered handoff, start/wait/termination/cleanup, and canonical init receipt in sandbox/hosting/images/activation/init_runner.py
- [x] T036 Implement uncertain possible-execution fencing with no automatic replay/adoption/rollback in sandbox/hosting/images/activation/service.py
- [x] T037 Run all init acceptance/crash/leak tests T009–T011 and scan production imports for credential/broker/helper/pull reachability in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 5: User Story 1 — Exact Activation

- [x] T038 Implement coherent local/running container observation with unchanged target/daemon/runtime epochs in sandbox/hosting/images/activation/runtime_observer.py
- [x] T039 Implement exact rendered-topology preflight and no-build/no-pull selected-service replacement adapter in sandbox/transports/remote_hosting_activation.py
- [x] T040 Implement activation orchestration from durable accept through ordered init, runtime replacement, running/health proof, edge sub-request, and commit in sandbox/hosting/images/activation/service.py
- [x] T041 Implement immutable edge sub-request replay lookup/resume/promotion/fence composition through the existing edge adapter in sandbox/hosting/images/activation/service.py
- [x] T042 Add activate command dispatch with explicit project/environment/request/generation/confirmation and bounded result mapping in sandbox/commands/hosting.py
- [x] T043 Run activation acceptance tests T005–T008 and edge/replay tests T014/T021, recording exact focused evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 6: User Story 3 — Feature 048 Observation Recovery

- [x] T044 Add bounded Feature 051 pending-transition projection and exact-new/prior/neither/ambiguous read-only observation models with canonical evidence identity and target/runtime epoch boundaries in `sandbox/hosting/recovery/models.py`
- [x] T045 Add exact pending-transition/generation/fresh-epoch observer eligibility and deterministic evidence identity without failed-apply or Feature 051 effect authority in `sandbox/hosting/recovery/policy.py`
- [x] T046 Implement the Feature 048 read-only activation observer returning a closed value for both pre/post calls with zero repository/state write in `sandbox/hosting/recovery/service.py`
- [x] T047 Add distinct replay-safe `sb host image recover` dispatch without changing existing failed-apply recovery arguments/results in `sandbox/commands/hosting.py`
- [x] T048 Implement the shared-owner/CAS 051 recovery protocol through the sole shared-host transaction port: durable non-authorizing provisional, exact crash resume, immediate second observation, pre/post comparison, exhaustive operation/phase/class matrix, and separate atomic result plus only matrix-legal promotion/clear candidate in `sandbox/hosting/images/activation/repository.py`
- [x] T049 Run recovery/provisional/crash/changed-evidence and protected-effect tests T012–T014 plus the full existing Feature 048 suite and record evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 7: User Story 4 — Zero-Init Adoption

- [x] T050 Implement zero-init-only adoption validation, exact no-effect proof, and atomic generation commit in sandbox/hosting/images/activation/service.py
- [x] T051 Add adopt command dispatch with explicit confirmation and bounded refusal classes in sandbox/commands/hosting.py
- [x] T052 Run adoption acceptance/refusal/no-effect tests T015–T016 and record evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 8: User Story 5 — One-Generation Rollback

- [x] T053 Implement deterministic pre-forward `ForwardRollbackSubject`, machine grant validation, acceptance persistence, and terminal generation references without a future-generation subject in `sandbox/hosting/images/activation/policy.py`
- [x] T054 Implement previous-generation-only local proof selection and rollback through the common transaction/runtime/edge/commit path in sandbox/hosting/images/activation/service.py
- [x] T055 Add rollback command dispatch with explicit confirmation and bounded refusal classes in sandbox/commands/hosting.py
- [x] T056 Run rollback acceptance/refusal/forbidden-capability tests T017–T019 and record evidence in specs/051-immutable-activation-recovery/implementation-evidence.md

---

## Phase 9: Compatibility, Documentation, and Final Gates

- [x] T057 Add Feature 051 state/result fields and distinguish 049/050/051/048/local/remote/production evidence in `docs/remote-hosting-implementation.md`
- [x] T058 Update operator commands and stale Feature 047 image-authority wording while preserving Feature 048 failed-apply recovery contracts in `docs/remote-hosting.md`
- [x] T059 Run the full T005–T023 acceptance set, all existing hosting/Feature 048 suites, subprocess environment guards, and architecture import scans in specs/051-immutable-activation-recovery/implementation-evidence.md
- [ ] T060 Perform human review of trust/credential unreachability, proof-custody TOCTOU/lock/crash safety, recovery two-observation promotion, init effect fencing, edge uncertainty, shared-owner races, rollback grants, state secrecy, and legacy compatibility in specs/051-immutable-activation-recovery/implementation-evidence.md
- [x] T061 Record remaining live registered-host, edge, rollback, deployment, and production validation as explicit open gates without claiming readiness in specs/051-immutable-activation-recovery/implementation-evidence.md

## Phase 10: Independent Review Repairs (Authored, Not Run)

- [x] T062 Reconcile exact host acceptance before custody prepare/replay; cancel only expired prepared plus durable absence in sandbox/hosting/images/activation/repository.py
- [x] T063 Make accepted custody replay phase-aware and release every durable non-uncertain terminal pin while retaining uncertain/incomplete pins in sandbox/hosting/images/activation/service.py
- [x] T064 Persist and independently validate complete exact service projections and required edge state for recovery in sandbox/hosting/recovery/models.py and sandbox/hosting/recovery/policy.py
- [x] T065 Normalize initializer proof from actual Docker inspect output and persist an independent ordered state/effect/receipt slot per initializer in sandbox/transports/remote_hosting_activation.py and sandbox/hosting/images/activation/service.py
- [x] T066 Replay exact recovery results before active/live checks and resume a durable provisional with only the post-write observation in sandbox/hosting/images/activation/repository.py
- [x] T067 Reserve bounded terminal storage during admission and recursively validate every persisted activation authority collection in sandbox/hosting/images/activation/repository.py
- [x] T068 Wire the shared target mutation port into real apply/sync/login/failed-recovery/edge/stage paths in sandbox/commands/hosting.py
- [x] T069 Add meaningful custody, capacity, closed-state, exact-projection, ordered-init, and real-port pairwise loser/no-effect tests without executing them
- [x] T070 Update Feature 051 operator/implementation docs and implementation evidence for independent review repairs
- [x] T071 Execute and independently review T062-T070 repairs; record focused, compatibility, automated-review, and still-open live evidence without conflating proof levels

## Phase 11: Second Independent Review Repairs (Authored, Not Run)

- [x] T072 Seed missing activation state from locked outer generation and reconcile exact Feature 050 custody lookup/replay/release
- [x] T073 Replay exact image-recovery terminals before active/live prerequisites and remove failed-recovery double ownership
- [x] T074 Reserve a byte-sized terminal candidate before custody and bind real Compose/container topology, configuration, health, and orphans
- [x] T075 Bind forward rollback, edge-required/routes, zero-effect adoption observation, and authenticated init inspection authority
- [x] T076 Cap recovery results and validate global generation/request collision invariants plus private retained terminal proof pins
- [x] T077 Align Feature 051 fixtures, focused tests, operator docs, tasks, and implementation evidence with the second review repairs
- [x] T078 Execute and independently review T072-T077 focused/compatibility/security behavior and retain live validation as a separate gate

## Phase 12: Third Independent Review Repairs (Authored, Not Run)

- [x] T079 Close retained terminal proof-pin decoding with the shared recursive safe-mapping and secret-field validator
- [x] T080 Represent first-generation recovery without prior authority while preserving exact-new-only safety
- [x] T081 Derive edge evidence from current manifest routes and bind adoption to authenticated Compose context with zero effects
- [x] T082 Replace admitted target/daemon echoes with independent registered-target and Docker epoch observations across local/running/recovery/init paths
- [x] T083 Align fresh and replayed recovery `ok` results and reserve result capacity before provisional observation
- [x] T084 Close recovery-result version/digest/generation/collision validation and bound retention
- [x] T085 Derive initializer platform architecture from independent image inspection and normalize exact declared environment/mount evidence
- [x] T086 Add focused mechanism assertions for third-review repairs without executing them
- [x] T087 Align operator docs and implementation evidence with the third-review repair truth
- [x] T088 Execute and independently review T079-T087, preserving separate live registered-host/edge/rollback/deployment/production gates

## Phase 13: Fourth Independent Review Repairs (Authored, Not Run)

- [x] T089 Atomically terminalize recovered original activation authority and replay-safe exact Feature 050 pin release
- [x] T090 Return exact immutable activation terminals before current admission/custody validation
- [x] T091 Supply declared init values from bound Compose or a narrow opaque provider without persistence, argv disclosure, or output
- [x] T092 Make `recovery_no_effect` stable non-success while retaining exact-new promotion success
- [x] T093 Derive running OS/architecture from independent exact local-image inspection
- [x] T094 Enforce the closed canonical retained proof-pin codec and malformed-state refusal
- [x] T095 Align focused mechanism tests, operator docs, and implementation evidence without executing checks
- [x] T096 Execute and independently review T089-T095 while preserving separate live gates

## Phase 14: Fifth Independent Review Repairs (Authored, Not Run)

- [x] T097 Reconcile exact retained terminal custody before public replay without recreating an absent released lease
- [x] T098 Move declared init values from SSH command serialization to bounded private stdin with remote output redaction
- [x] T099 Permit only exact same-request uncertain active/result fencing and replay without new effects
- [x] T100 Align focused crash/private-input/uncertainty tests, operator docs, and implementation evidence
- [x] T101 Execute and independently review T097-T100 while preserving separate live gates

## Phase 15: Sixth Independent Review Repairs (Authored, Not Run)

- [x] T102 Keep raw Compose and container init values inside the remote private-input comparison protocol
- [x] T103 Fence every post-create initializer failure with stopped-container cleanup and private-cache erasure
- [x] T104 Require exact uncertain phase plus active/retained result equality for the narrow collision exception
- [x] T105 Align focused raw-output/cleanup/negative-invariant tests, docs, and implementation evidence
- [x] T106 Execute and independently review T102-T105 while preserving separate live gates

## Phase 16: Seventh Independent Review Repair (Authored, Not Run)

- [x] T107 Bind the private Compose value selector to exact admitted render inputs and the opaque protected render identity
- [x] T108 Add default-source success, source-failure, and divergent-render assertions without value exposure
- [x] T109 Align operator docs and implementation evidence with the bound private source
- [x] T110 Execute and independently review T107-T109 while preserving separate live gates

## Phase 17: Eighth Review Test Harness (Authored, Not Run)

- [x] T111 Add a real private Compose helper synthetic-process matrix for refusal, divergence, success, and value non-disclosure
- [x] T112 Execute and independently review T111 while preserving separate live gates

## Phase 18: Ninth Review Test Refinement (Authored, Not Run)

- [x] T113 Assert real-helper stdout/stderr private-value redaction while preserving public success identity
- [x] T114 Execute and independently review T113 while preserving separate live gates

## Phase 19: T060 Source-Security Review Repairs

- [x] T115 Bind local and running proof to the guarded registered target identity and exact Compose project
- [x] T116 Execute replacement from the exact remote private render and prove post-effect Compose configuration hashes
- [x] T117 Clean or prove absent every possible post-create initializer container before returning
- [x] T118 Authenticate rollback grants with a machine-bound Ed25519 public verifier and owner-safe bundle reads
- [x] T119 Require generation-bound edge receipts and keep reachability-only verification fail-closed
- [x] T120 Add focused negative tests and synchronize the Feature 051 contracts and operator guidance
- [x] T121 Run the complete local gates and obtain a new independent T060 source-security review

## Phase 20: Post-Review Authority and Early-Recovery Repairs

- [x] T122 Bind admission and running proof to the complete rendered Compose configuration digest and refuse unsnapshotted file-backed inputs
- [x] T123 Bind initializer names and labels to target/image/declaration ownership and never remove an unproved collision
- [x] T124 Refuse a changed prior Compose project before the first rollback effect
- [x] T125 Persist a closed recovery context and support no-candidate recovery from early phases without promotion
- [x] T126 Close persisted target and tombstone schemas and add direct command-path recovery coverage
- [x] T127 Synchronize Feature 051 contracts, implementation guidance, and focused negative tests
- [x] T128 Rerun complete local gates and obtain a new independent post-repair source-security review

## Phase 21: Compose Resource and Private-Digest Repairs

- [x] T129 Refuse top-level Compose configs/secrets and external networks without exact byte/object snapshots
- [x] T130 Keep the raw private render and its raw hash remote-only and persist only an opaque machine-keyed configuration identity
- [x] T131 Redact inline content and every duplicate-name/overlapping private value from remote helper output
- [x] T132 Add external-resource, low-entropy oracle, inline-content, and duplicate-key regressions and synchronize docs
- [x] T133 Rerun complete local gates and obtain a new independent post-repair source-security review

## Phase 22: Closed Projection and Full-Render HMAC Repair

- [x] T134 Replace the open-ended scrubbed Compose document with a closed allowlisted public projection
- [x] T135 Derive a target-scoped HMAC key from the owner-only machine master and exact machine/target identities, then bind the complete private render through private stdin
- [x] T136 Keep the machine master local, remove the derived key before Docker execution, and keep both keys/raw render/raw hash out of state, argv, and output
- [x] T137 Redact every rendered scalar key and value and add command/entrypoint/label-key/label/annotation/health/URL/logging/extension canaries
- [x] T138 Preserve managed network name/driver/IPAM/alias identity changes through the opaque full-render binding
- [x] T139 Rerun complete local gates and obtain a new independent post-repair source-security review

## Phase 23: Fresh Runtime Configuration and Retained-Proof Custody Repairs

- [x] T140 Bind each private Compose service hash to a target-scoped HMAC identity and retain it in closed compose/running/generation projections
- [x] T141 Move running `ps`/inspect/image observation into a closed remote projection so raw environment, arbitrary labels, and raw config hashes never cross SSH
- [x] T142 Require exact protected service-hash equality in fresh running, post-edge, rollback, and two-observation recovery proof
- [x] T143 Decode and canonical-byte compare the complete retained staged proof plus exact ledger authority/revision under target -> host -> stage custody before policy admission
- [x] T144 Enforce persisted record/proof/tombstone/pin maxima, nested proof authority, and disjoint tombstone/retained authority at load time
- [x] T145 Add stable-drift, remote non-disclosure, corrupt/partial/mismatched proof, wrong authority/revision, over-limit, overlap, and in-lock admission regressions and synchronize docs
- [x] T146 Rerun complete local gates and obtain a new independent post-repair source-security review

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

- [X] T062 Add protected post-stage v2 activation-bundle preparation, target-scoped
  Compose identity, ssh-agent public-key signing, immediate verification, owner-only
  replay/conflict handling, and focused refusal tests.

## Phase 24: Lenzora edge-cache purge extension (Astra Medium plan)

- [X] T147 Add RED policy/value/provider tests for explicit route-covered `zone_all` policies,
  exact zone responses, all-zone preflight, provider refusal classes, bounded receipts, and
  secret non-disclosure in `tests/test_hosting_edge_cache.py` and `tests/test_hosting.py`.
- [X] T148 Extend the Feature 051 contracts/data model and validate `cloudflare.cache_purge`
  in `sandbox/core/_hosting.py`; implement a pure policy plus Cloudflare purge adapter in
  `sandbox/hosting/edge_cache.py` and `sandbox/core/_cloudflare.py` without credential or
  arbitrary-zone escapes.
- [X] T149 Add durable per-zone purge operation/receipt validation and exact replay,
  conflict, acceptance-unknown, and retention bounds through the existing outer host-state
  owner and v2 activation repository/models.
- [X] T150 Integrate admitted purge order and read-only observation into v2 activation,
  rollback, and `_HostImageEdgeAdapter`; preserve old receipt schemas and zero-write
  recovery/adoption behavior.
- [X] T151 Integrate the same purge operation into ordinary `host apply`, edge continuation,
  plan/status/diagnose output, and add an explicit confirmation-gated CLI path only if the
  shared owner contract requires it.
- [X] T152 Update Lenzora's production/development hosting manifests and protected deployment
  classifiers while preserving existing v2 production and ordinary development flows.
- [X] T153 Run focused edge-cache, hosting, activation, recovery, and CLI gates plus
  architecture scans; record exact evidence and open live validation in implementation docs.
- [ ] T154 Commit and push verified Sandbox work on `latest`; separately preserve and report
  the Lenzora checkout's existing dirty files and branch state.

## Phase 25: Convergence

- [ ] T155 Prepare candidate-specific private Compose/environment inputs under the existing target owner in `sandbox/commands/hosting.py` and `sandbox/transports/remote_hosting_activation.py`; bind manifest values, registered secrets and verified source revision through private stdin, owner-only atomic installation and exact replay/conflict checks; preserve active/legacy inputs and prove non-disclosure with synthetic canaries in `tests/test_hosting_image_provisioning.py` and `tests/test_hosting_image_activation_private_source.py` per FR-013 and FR-051 (partial).
- [ ] T156 Bind an authenticated ordered initializer contract into new v2 snapshots/requests in `sandbox/hosting/images/activation/v2_models.py` and `sandbox/hosting/images/provisioning.py`; retain legacy decoding/digests, refuse legacy init-bearing forward execution, and implement inspect-before-start plus bounded owned execution/cleanup through `init_runner.py` and the private remote adapter; prove exact service/image/order/configuration matching and terminal receipts in the existing v2/init/private-source suites per FR-014 through FR-019 (missing).
- [ ] T157 Persist ordered initializer steps/receipts and separate runtime effect entry in `sandbox/hosting/images/activation/v2_repository.py`, `repository.py` and `v2_service.py`; require complete receipts before replacement/commit and fence uncertain init in recovery, including empty-genesis cases; add crash/replay/receipt-substitution regressions, synchronize contract/runbook documentation and run required compatibility gates per FR-017, FR-018, SC-002, SC-008 and plan: architecture and effect order (partial).

## Phase 26: Convergence

This phase completes T155–157 against the whole-deployment audit. Execute in order
with one current-agent owner and no further delegates. Preserve historical receipts
and dirty work. No intermediate task authorizes deployment or incident settlement.

- [x] T158 Complete read-only consistency analysis of `spec.md`, `plan.md`, `data-model.md`, `contracts/execution-v2.md` and this phase; resolve blocking contract gaps before further controller implementation per FR-052–065 and plan: deployment repair amendment (partial).
- [ ] T159 Add integrated RED Lenzora-shaped fixtures through actual codecs/helpers/CLI in the existing activation v2, private-source, provisioning, init, recovery, races, edge and hosting suites; include 17 services, three init jobs, queue prerequisite, secret mounts, delayed health and dev duplicate-init cases per SC-010–013 (missing).
- [ ] T160 Rework `sandbox/commands/hosting.py` and a focused private-input module/transport to capture exact local application bytes under source/broker guards, materialize supported secret files, publish complete candidates with replay/rotation/retention identities, and propagate selectors through every parser; prove private non-disclosure and failure preservation per FR-052–054 (partial).
- [ ] T161 Extend `activation/v2_models.py`, `v2_repository.py`, `repository.py`, `v2_runtime.py` and `images/provisioning.py` with exact dependency conditions/prerequisite graph, full receipt chain, retained selectors and pre-forward authority across every request/intent/generation/recovery codec; preserve legacy digest bytes per FR-055, FR-056 and FR-060 (partial).
- [ ] T162 Implement prerequisite and init execution in `activation/v2_service.py`, the v2 init lifecycle and private runtime transport; persist terminal exit before cleanup, disable implicit dependencies, enforce ownership and crash/no-replay boundaries, then enable supported graph activation per FR-014–019 and FR-055–056 (missing).
- [ ] T163 Implement bounded read-only readiness and shared first-target registration/edge setup in the activation runtime/service and hosting/edge mechanisms; preflight provider refusals before runtime/data effects and reconstruct acknowledged purge aggregates without POST replay per FR-057–058 (missing).
- [ ] T164 Repair graph-phase two-observation recovery and retained pre-forward rollback across activation/recovery modules and command dispatch; remove effect-entered empty-genesis authority, preserve detailed fixed refusal reasons and legacy terminal records per FR-059–060 and FR-065 (partial).
- [ ] T165 Add the separate closed operator settlement plan/apply service, target mutation registration and CLI contract; require exact quiescence/preservation/data/approval evidence, preserve uncertainty/current/generation, and gate subsequent forward admission on predecessor/data authority; test custody crash boundaries per FR-061–062 and SC-012 (missing).
- [ ] T166 Repair opt-in development dependency/source ownership in hosting and implement exact A/D/S handling in Lenzora's deployment wrapper worktree with existing unit/contract tests and runbooks; preserve generic hosting and development mode per FR-063–064 and SC-011–013 (partial).
- [ ] T167 Run final focused/full Sandbox and required Lenzora local gates, then separately authorized real disposable topology/CI and installed-tool/provider/incident/production gates; record exact identities and unresolved owner approvals without substituting narrow checks for deployment proof per FR-065, SC-010–013 and plan: acceptance tiers (missing).
