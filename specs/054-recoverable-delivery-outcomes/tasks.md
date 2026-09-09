# Tasks: Recoverable Delivery Outcomes

**Input**: Accepted spec.md and the complete plan.md, research.md, data-model.md, contracts/ and quickstart.md in specs/054-recoverable-delivery-outcomes/.

**Execution rule**: Historical core tasks and their checkboxes remain below. The current remaining sequence first completes all production/config/docs T061–T070, including US6 and the output corrections, before any further feature acceptance or test execution. Then run T071–T075 and the outstanding applicable T040–T047 plus T045A; only then author T076–T079 and outstanding core regressions, and run T080–T083 with T055–T060. This follows active AGENTS instructions over test-first template examples. No TDD or per-file test runs during coding.

**Ownership**: Packages A–G in plan.md are disjoint. One Astra integration owner owns shared files, integration decisions and final acceptance. Use Astra Low/Medium for coupled work, Luna Max for bounded simple modules and Luna XHigh for mechanical docs/evidence. No Sol. A [P] task may overlap only with a ready task in different owned files; dependencies below still apply. Do not overwrite concurrent user/root work.

## Phase 1: Setup and design gate

- [X] T001 Record the active branch, candidate revision and existing dirty/concurrent ownership in tmp/054-delivery-acceptance/source-baseline.md; confirm the exclusions in specs/054-recoverable-delivery-outcomes/plan.md without changing unrelated work.
- [X] T002 Run the root-owned managed agent-context update for AGENTS.md/CLAUDE.md and independent speckit-analyze against specs/054-recoverable-delivery-outcomes/{spec.md,plan.md,tasks.md}; resolve actual blocking contradictions before source implementation.
- [X] T003 Assign one writer per package and record exact inputs, owned paths, exclusions and handoff requirements in tmp/054-delivery-acceptance/ownership.md using specs/054-recoverable-delivery-outcomes/plan.md; prohibit test/acceptance execution until T039.

## Phase 2: Foundation

All stories depend on these shared types and the basic journal. Existing owner repositories remain authoritative.

- [X] T004 Implement closed schema-v1 target, requested outcome, evidence, operation, creation, URL result and query models with finite bounds and allowlisted serialization in sandbox/delivery/__init__.py and sandbox/delivery/models.py according to specs/054-recoverable-delivery-outcomes/data-model.md.
- [X] T005 Implement the diagnostic SQLite owner, schema creation for writers only, atomic initial record/permanent request guard binding, idempotent immutable terminal snapshots, bounded events, protected-record capacity and read-only mode=ro access in sandbox/delivery/repository.py; implement reserve_request with 4,096 non-expiring guards, expired-key no-replay and fail-closed new-key capacity under the existing database cap, never workload authority.

## Phase 3: US1 — Recovery before effects (P1, minimum useful scope)

**Goal:** Ordinary apply either retains eligible recovery admission before deployment effects or gives a typed refusal and durable preparation command.

**Independent acceptance:** T041 proves valid admission ordering and zero protected effects for every defined ineligible case; T049 adds regressions afterward. Partial optional telemetry with valid identity must be eligible while independent required resource policy can still fail.

- [X] T006 [P] [US1] Add a public authenticated identity-only accessor in sandbox/resources/context.py that performs no telemetry-repository creation, explicitly allows only known/partial/unmanaged valid identities, and preserves independent resource-policy outcomes.
- [X] T007 [P] [US1] Add bounded read_delivery_job_evidence in sandbox/jobs/registry.py using an existing SQLite database in mode=ro without constructors, migration, reconciliation, output payload or scheduling. In the same A-owned package, extend sandbox/jobs/process.py with bounded read-only genuine macOS boot-session observation alongside Linux boot IDs; unavailable/legacy hostname-derived identity never authorizes admission, signaling or record rebinding.
- [X] T008 [US1] Implement fixed-context/retained-job/application source validation and live child process identity/group matching in sandbox/delivery/admission.py, including the bounded five-second child-publication wait and typed no-context/mismatch/dirty/unknown refusals.
- [X] T009 [US1] Add the owned ordinary recovery delivery projection and exact committed-admission readback seam in sandbox/hosting/recovery/repository.py; keep target/state/remote fences and existing authority interpretation.
- [X] T010 [US1] Define plan eligibility, safe durable invocation guidance and the mandatory pre-effect integration in sandbox/commands/hosting.py; revalidate source/config/registration/identity/owner, remove the operation=None execution fallback, and use the same identity rule in later ordinary recovery.
- [X] T011 [US1] Complete admission/journal failure handling in sandbox/commands/hosting.py so committed or uncertain original authority survives, generation does not advance on refusal and no second identity starts work after a lost acknowledgment.

## Phase 4: US2 — Explain interrupted delivery (P1)

**Goal:** One read-only query explains ordinary and immutable outcomes, known effects, proof gaps and a permitted existing continuation.

**Independent acceptance:** T042 queries success/failure/active/uncertain ordinary and image fixtures through CLI/MCP with zero authority or journal writes. Healthy runtime plus initializer refusal must remain unsuccessful. T050 and T054 add regressions afterward.

- [X] T012 [P] [US2] Add an owner-provided pure read-only immutable status projection in sandbox/hosting/images/activation/status.py, preserving existing schema-specific initializer, artifact, generation, runtime and edge decisions without mutation-lock snapshots.
- [X] T013 [US2] Implement DeliveryService exact evidence joins, distinct application/control revisions, declared-requirement success evaluation, safe next-action descriptors and legacy partial-history diagnosis in sandbox/delivery/service.py using injected owner interfaces only.
- [X] T014 [US2] Implement delivery inspect selectors, recorded-only default, optional current read-only observation, bounded JSON/human output and separate query-versus-delivery success in sandbox/commands/delivery.py.
- [X] T015 [P] [US2] Implement the delivery_inspect MCP group in mcp/wp-server/tools/delivery.py using an injected delivery_service_factory and the same schema/serializer; do not add an app-helper or shell-built adapter.
- [X] T016 [US2] Add ordinary/immutable command writer hooks and linked recovery-result capture in sandbox/commands/hosting.py; retain initial records before covered effects and terminal snapshots only from authoritative results, with visible delivery_record_incomplete on post-effect storage failure.
- [X] T017 [US2] Complete disagreement, missing/unsupported detail, unsafe-reference and bounded-redaction handling in sandbox/delivery/service.py; never reconcile jobs, archive on read, backfill historical success or obtain new replay/cleanup authority.

## Phase 5: US3 — Verify exposed sites (P1)

**Goal:** Deploy/preview distinguishes route configuration from verified application availability or exact-release scope on every requested hostname.

**Independent acceptance:** T044 runs public positive and DNS/TLS/redirect/query/backend/alias/edge/deadline controls. Every negative stays non-success; T051 adds regressions afterward. Shared command invocation is wired in T033–T035 after US5 ownership support.

- [X] T018 [P] [US3] Implement the closed delivery.routes config provider in sandbox/config/delivery.py, including WordPress REST metadata default, required custom application marker, optional/required release identity, applicable edge contract and all declared bounds from specs/054-recoverable-delivery-outcomes/contracts/routes.md.
- [X] T019 [US3] Implement finite public DNS/HTTP/TLS/redirect/query/application/release checks in sandbox/delivery/route_worker.py with explicit public inputs, limited response reads, safe result fields and no secret environment forwarding.
- [X] T020 [US3] Implement parent deadline enforcement, bounded attempts, worker termination/reaping and verified/failed/incomplete aggregation in sandbox/delivery/routes.py; every hostname/check shares one 10–300 second budget.
- [X] T021 [US3] Integrate route/application/release/edge evidence into frozen requested outcomes and DeliveryService evaluation in sandbox/delivery/service.py, preserving existing stronger immutable authority and visible unsupported optional identity.
- [X] T022 [US3] Add per-host effects/results for partial primary/alias configuration and exact already-authorized cleanup in sandbox/commands/deploy.py and sandbox/commands/preview.py; do not claim verification from a route write or reload.
- [X] T023 [US3] Preserve safe known exposure effects and original operation references on timeout, transport loss, wrong backend or partial cleanup in sandbox/commands/deploy.py and sandbox/commands/preview.py; retain instances when cleanup ownership is not proved.

## Phase 6: US4 — Latest attempt and retained success (P2)

**Goal:** Success A remains identifiable after failed attempt B, with explicit historical bounds and a separate current observation.

**Independent acceptance:** T043 proves A/B ordering, immutable repeated writes, app/control distinction, pinned retention, incomplete persistence and cursor limits. T052 adds regressions afterward.

- [X] T024 [US4] Complete 30-day/64-per-target/512-global terminal detail retention, 128 protected-record cap, 1,024-target metadata limit, 96 MiB database cap and explicit expired/unknown metadata in sandbox/delivery/repository.py; preserve all permanent request guards through detail/target eviction and never delete owner fences or reopen expired IDs.
- [X] T025 [US4] Implement separate latest-attempt/latest-retained-complete-success selection and stable bounded pagination/cursor validation in sandbox/delivery/repository.py and sandbox/delivery/service.py; current observation cannot manufacture old success.
- [X] T026 [US4] Guard superseding covered mutations in sandbox/commands/hosting.py, sandbox/commands/deploy.py and sandbox/commands/preview.py so a prior terminal authority snapshot is retained before overwrite or the new mutation refuses; never claim cross-store atomicity.

## Phase 7: US5 — Exact creation and URL ownership (P2)

**Goal:** Creation/reuse and partial URL changes remain tied to one original request and exact instance incarnation.

**Independent acceptance:** T045 uses two labelled instances, created/reused/lost-response/nonterminal/incarnation-drift/partial-URL cases; no unrelated change or new deletion right. T053 adds regressions afterward.

- [X] T027 [P] [US5] Add closed bounded CreationContext/CreationReceipt validation with frozen intent_digest, canonical nonsecret intent_fields and per-instance receipt/guard limits to sandbox/server_config/models.py; recompute project/root/label/resolved-config/create-policy bindings and reject changed intent before effects without changing unrelated ensure callers.
- [X] T028 [US5] Implement the 4,096-record/8 MiB permanent creation guard owner and reserve_creation_request/read_creation_request in sandbox/server_config/creation_requests.py; integrate sandbox/core/_instances.py and sandbox/runtimes/compose.py to reserve before an atomic pending/selected incarnation-and-receipt registry commit. Add Compose's missing project/allocation serialization and pending commit before overlay/start, preserving identity and receipt through success/failure/later writes. An existing generic row without incarnation may gain only current opaque identity in the guarded selection commit with relation=reused, preserving WordPress W1 legacy behavior and granting no historical creation/cleanup claim. Return unknown without replay after a between-store crash, retain guards after receipt eviction/instance deletion, and keep existing snapshots in sandbox/commands/data.py. Persist frozen intent_digest, actual completion and exact read-only lookup; changed intent conflicts before effects.
- [X] T029 [US5] Add structured intent_fields/intent_digest receipt transport and exact read_remote_creation_receipt plus guard lookup in sandbox/core/_remote.py. Carry optional creation_context/expected_incarnation through sandbox/commands/instances_cmd.py OperationRequest.arguments and sandbox/application/context.py closures; sandbox/application/runtime_service.py checks selected-runtime capability before effects. Add pure capability/receipt access through explicit owner reads, never ensure or a mutating repository constructor. F owns bounded CLI input/query parsing in sandbox/cli.py. Changed intent conflicts before effects, expired/unknown requests cannot replay, and lost/malformed/unsupported responses or inventory never supply ownership; no new facade consumer or generic exposed-deploy downgrade.
- [X] T030 [US5] Add expected-incarnation/context revalidation to remote reconcile and each URL write/readback in sandbox/core/_remote.py, preserving the fields through the existing application/context.py apply closure and runtime-service checks; return typed partial home/siteurl or incarnation-changed evidence and no cross-instance rollback.
- [X] T031 [US5] Join creation and URL results to original deploy/preview operation/request/job in sandbox/commands/deploy.py and sandbox/commands/preview.py, keeping reused/nonterminal/unproved instances outside cleanup authority.

## Phase 8: Complete integration and docs before any runs

The integration owner is the only writer for shared CLI/manifests/command files. This phase depends on all story production tasks.

- [X] T032 Register the owned delivery CommandSpec, delivery config provider and predispatch read-only/admission paths in sandbox/commands/manifest.py, sandbox/config/manifest.py and sandbox/cli.py; stop compatibility writers from preceding host plan/apply admission or delivery inspect. F also owns bounded structured creation context and pure capability/receipt query parsing for the existing ensure transport, with read-only modes dispatched before compatibility writers and E-owned handlers.
- [X] T033 Finish stable operation/request intent binding and duplicate/conflicting request behavior for synchronous and durable deploy/preview in sandbox/commands/deploy.py and sandbox/commands/preview.py; persist their initial diagnostic record before effects and never replay uncertain requests.
- [X] T034 Wire the bounded public observer and exact creation/URL evidence into final deploy/preview success evaluation in sandbox/commands/deploy.py and sandbox/commands/preview.py after all capability, source and application-contract preflight checks.
- [X] T035 Add optional --request-id/--verify-timeout public arguments and equivalent existing remote_deploy MCP inputs in sandbox/cli.py and mcp/wp-server/tools/remote.py, preserving unrelated result meanings and explicit preview/deployment authority.
- [X] T036 Register delivery_inspect and its service dependency in mcp/wp-server/tools/manifest.py and mcp/wp-server/server.py; expose versioned capabilities through the existing supported controller/instance contract in sandbox/core/_remote.py and sandbox/server_config/models.py before dependent effects.
- [X] T037 [P] Document exact CLI/MCP schemas, mandatory ordinary admission, safe reconnect, history bounds, application/control revisions, creation limits and exposure scope in docs/delivery-outcomes.md, docs/remote-hosting.md and docs/remote-job-runtime.md.
- [X] T038 [P] Update README.md, docs/sandbox-config-reference.md and skills/sandbox-cli/SKILL.md with the implemented route contract, supported invocation, capability/revision compatibility and explicit proof limits; no unsolicited VERSION bump or release.
- [X] T039 Inspect the complete production/config/docs diff against specs/054-recoverable-delivery-outcomes/plan.md and contracts/; resolve missing call sites, unsafe serializers, authority/order/callback failures and concurrent-edit conflicts, then record coding complete in tmp/054-delivery-acceptance/implementation-complete.md before running any tests or acceptance commands.

## Phase 8A: Bounded nested source binding correction before any runs

This additive correction preserves the original source/job commit `C` and binds it to a deterministically derived deployed artifact commit `T`. These tasks are intentionally unchecked: existing Feature 054 production completion does not establish the nested-source contract.

- [X] T039A Add the closed `SourceArtifact` codec and `source_schema=2` owner projection fields in sandbox/hosting/recovery/models.py, sandbox/hosting/recovery/repository.py and sandbox/delivery/models.py; preserve legacy `source_schema=1`, absent optional fields and existing OCI `artifact_digest` semantics without digest rewrites or historical inference.
- [X] T039B Integrate pre-effect C/T preparation, selected-tree equality, T-derived configuration, literal-T publication and returned-SHA assertion through sandbox/commands/hosting.py; update recovery policy/observer and delivery joins so job/admission use C while runtime/edge use independently observed T. Register ordinary_source_artifact_v1 and refuse unsupported extended writes before effects. Keep post-dispatch publication anomalies truthful and do not add cleanup or transport authority.

## Phase 9: Actual supported workflow exercises

This phase begins only after T039A–T039B. Use quickstart.md. Protected cases require existing concrete authorization and exact candidate/controller capability; execute all available safe cases and record unavailable protected/optional-runtime proof explicitly. Missing authority is not permission and is not a pass. Do not hold useful local verification hostage to unavailable remote cases.

- [ ] T040 Record actual candidate/application/control/installed-controller revision and capabilities through supported read-only commands in tmp/054-delivery-acceptance/revisions.md; exercise delivery help, missing-store diagnosis, config validation and unsupported capability refusal using specs/054-recoverable-delivery-outcomes/quickstart.md.
- [ ] T041 [US1] Exercise plan, direct refusal, valid durable child, partial telemetry and all defined ineligible/interruption/owner cases through supported CLI on owned fixtures; observe genuine Linux/macOS boot identity and unavailable/legacy-identity refusal without signaling or rebinding old jobs. Retain admission-before-effects and zero-effect evidence in tmp/054-delivery-acceptance/admission.md, with any unavailable real-host cases named.
- [ ] T042 [US2] Exercise recorded ordinary/immutable and current-read-only diagnosis through CLI and MCP, including active/lost response/initializer refusal/edge conflict; retain exact joins, permitted continuation and unchanged owner/journal evidence in tmp/054-delivery-acceptance/diagnosis.md.
- [ ] T043 [US4] Exercise success A then failure B, different app/control sources, journal failure, retention/capacity and pagination through supported diagnosis/owned fixtures; replay the original key with same/changed intent after detail/target eviction and exhaust permanent guard capacity, asserting expired/conflict/new-key refusal with zero replay in tmp/054-delivery-acceptance/history.md.
- [ ] T044 [US3] Exercise authorized deploy/preview public-route positives and DNS/TLS/redirect/query/marker/backend/release/alias/edge/deadline controls from quickstart.md; retain each host's actual result, configured effects and worker deadline proof in tmp/054-delivery-acceptance/routes.md.
- [ ] T045 [US5] Exercise the supported CLI/runtime chain for both WordPress and generic Compose using two owned labelled instances with created/reused/lost response/nonterminal/incarnation drift/partial WordPress URL cases. Observe retained generic pending ownership after failed startup, unchanged structured context, pure lookup/capability queries, pre-effect capability refusal and positive generic exposed success. Add changed-intent conflict, receipt-eviction replay refusal, guard-capacity refusal and guard-before-registry crash/unknown cases, with guard survival independent of a current instance row, in tmp/054-delivery-acceptance/creation.md. Require zero replay/unrelated changes/new cleanup rights.
- [ ] T045A Exercise the nested-source correction through the actual supported isolated hosting owner workflow: prove a clean outer/nested fixture yields `C != T`, selected-tree equality excludes the outer sentinel, recovery/delivery retain the identical artifact and frozen T-derived config, literal `T` is published, and C-bound job/admission joins remain distinct from independently observed T runtime/edge evidence. Cover pre-effect source/prefix/tree/config conflicts, missing capability, unexpected post-dispatch SHA, legacy identity records and missing/pruned source proof; retain exact limits in tmp/054-delivery-acceptance/nested-source-binding.md.
- [ ] T046 Exercise default, failure, retained, detail and cursor output with synthetic sensitive-value controls through actual CLI/MCP adapters; retain bounded valid JSON, redaction and old-controller refusal results in tmp/054-delivery-acceptance/compatibility.md.
- [ ] T047 Resolve defects observed in T040–T046 across their complete affected production/config/docs paths, rerun only affected actual commands and record remaining unavailable proof in tmp/054-delivery-acceptance/observed-defects.md before authoring regressions.

## Phase 10: Focused regressions after real runs

Use observations from Phase 9, not implementation-mirroring assertions. All new/changed captured subprocesses use tests.subprocess_support.run_test_process or synthetic_environment. Never copy, unpack, enumerate or pass through the parent os.environ.

- [ ] T048 [P] Add closed-schema, count/byte bounds, unsafe-field and application/control distinction regressions to tests/test_delivery_models.py from observed output/validation cases.
- [ ] T049 [P] [US1] Add durable-record/process/source/identity/refusal/admission-persistence regressions in tests/test_delivery_admission.py and extend tests/test_job_registry.py plus the applicable existing process-identity and tests/test_hosting_recovery*.py cases. Cover genuine Linux/macOS boot IDs, bounded native observation failure, reboot mismatch and legacy guessed-ID refusal with no signaling/rebinding; assert zero protected effects and preservation of original owners.
- [ ] T050 [P] [US2] Add exact ordinary/immutable joins, initializer/edge refusal, readonly absence of migration/reconciliation, recovery relation and post-effect recording-gap regressions in tests/test_delivery_service.py.
- [ ] T051 [P] [US3] Add controlled HTTP/TLS/DNS-worker/redirect/query/application/release/alias/edge/deadline regressions in tests/test_delivery_routes.py, including one failing alias and worker reaping; use synthetic subprocess environments.
- [ ] T052 [P] [US4] Add immutable terminal/duplicate/conflict, success-A/failure-B, cursor invalidation, finite retention/capacity and pinned-record preservation regressions in tests/test_delivery_repository.py, including permanent guards after detail/target eviction, same/changed expired-key no-replay and full-capacity new-key refusal with existing lookup still available.
- [ ] T053 [P] [US5] Extend relevant existing tests/test_remote_deploy*.py, tests/test_preview*.py and instance-owner/Compose/runtime-service/ensure-CLI tests for frozen intent/context transport through both adapters, pure capability/receipt queries, generic pending ownership on failed startup, positive generic exposed success, changed-intent pre-effect conflict, receipt eviction/instance deletion guard survival, expired-key no-replay, guard-capacity refusal and crash between guard/registry commits, alongside creation/reuse/nonterminal/incarnation/partial URL behavior. Assert additive unrelated ensure behavior, no replay, unrelated mutation or new cleanup authority.
- [ ] T053A [P] Add focused nested-source regressions in tests/test_hosting.py, tests/test_delivery_service.py, tests/test_delivery_admission.py, tests/test_host_recovery_repository.py, tests/test_host_recovery_policy.py and existing source transport fixtures after T045A. Cover real C/T derivation and tree equality, closed artifact/legacy byte stability, pre-effect zero-effect conflicts, literal-T/pushed-SHA mismatch handling, C-vs-T joins, null-until-observed source evidence, missing/pruned proof and ordinary_source_artifact_v1 refusal. Use synthetic_environment/run_test_process for captured subprocesses; do not replace owner/admission workflow tests with pure same-SHA mocks.
- [ ] T054 [P] Add CLI/MCP selector/schema/meaning/byte-limit/redaction/capability/legacy-caller parity regressions in tests/test_delivery_cli_mcp.py without introducing an app-helper consumer or environment-dependent capture.
- [ ] T055 Run the focused new/affected unittest and existing hosting/activation/job/deploy/preview/manifest checks through finite durable jobs and retain terminal job IDs/results in tmp/054-delivery-acceptance/focused-gates.md; distinguish unchanged baseline failures from feature regressions.

## Phase 11: Required gates, independent review and completion

- [ ] T056 Run the repository-required full unittest and current documented CLI/MCP/manifest/runtime-revision gates through finite durable jobs after focused fixes; record exact commands, environment, terminal jobs, results and baseline comparison in tmp/054-delivery-acceptance/full-gates.md.
- [ ] T057 Obtain independent read-only correctness review of the stable diff and evidence, tracing admission ordering, genuine job/identity authority, exact joins, persistence/retention, routes and incarnation semantics; save actionable file-referenced findings in tmp/054-delivery-acceptance/correctness-review.md.
- [ ] T058 [P] Obtain independent product/output review of actual success/failed/unknown/partial CLI/MCP examples, next-action wording, latest-versus-success and exposure scope; save findings in tmp/054-delivery-acceptance/product-review.md.
- [ ] T059 Resolve corroborated review findings in the affected owned source/docs/tests, rerun only justified affected real commands/gates and record final SC-001–SC-009 evidence/limits plus capability/source/installed revision distinction in docs/delivery-outcomes.md and tmp/054-delivery-acceptance/final.md.
- [ ] T060 Review the final scoped diff and required gate evidence, then stage/commit/push completed work to the active non-main branch under repository policy; record commit/push evidence and explicitly unverified protected/optional-runtime acceptance in tmp/054-delivery-acceptance/final.md without tagging, releasing, deploying or opening/merging a PR.

## Dependencies and execution graph

~~~text
T001–T003 design/ownership
    -> T004 models -> T005 journal
    -> US1 T006–T011
    -> US2 T012–T017 (uses US1 owner/job projections)
    -> US3 T018–T023 (uses shared types; final invocation waits for US5)
    -> US4 T024–T026 (uses journal and query/writer seams)
    -> US5 T027–T031 (uses shared types; joins in existing commands)
all production stories -> T032–T039 complete integration/docs
    -> T039A–T039B nested source binding correction
    -> T040–T047 actual supported workflows
    -> T048–T055 regression authoring/focused gates
    -> T056–T060 full gates/reviews/accepted scope commit
~~~

Within a story, follow listed dependencies. T006/T007 can run together; T008 waits for both. T012 can run while independent US1 helpers finish, but T013 needs their interfaces. T015 needs the agreed T013 service interface. T018 can run after foundation alongside A/E; T019–T020 remain sequential. T027 can run alongside other disjoint story helpers, but T028–T030 remain one sequential owner. Shared hosting/deploy/preview/service files are never written concurrently even when story labels differ. T024–T025 follow T005; T026 waits for all affected writer seams. T039A–T039B must finish before T040–T047; T045A waits for the nested correction and all prior production tasks; T053A waits for T045A and precedes T055. Every acceptance task waits for all of T001–T039 plus T039A–T039B, even if its story code finished earlier.

## Parallel examples by story

- US1: one writer owns resources/context.py (T006), another jobs/registry.py (T007); the admission integrator waits for both.
- US2: immutable status projection T012 can be prepared independently; CLI/MCP presentation work uses the agreed service interface, with manifest writes reserved for root.
- US3: config provider T018 can overlap creation/schema work; the route worker and parent deadline controller stay one owner to avoid mismatched bounds.
- US4: no simultaneous writers to repository.py/service.py. Bounded history design can be implemented after their earlier tasks; its independent regression T052 can run alongside other regression files only after actual workflows.
- US5: schema T027 can overlap disjoint A/D work; instance commit, remote transport and URL writes are coupled and sequential. Its later regression files have one owner. Nested source correction T039A–T039B is one coupled hosting/recovery/delivery owner; T045A observes it before T053A authors regressions.
- Cross-cutting: documentation T037/T038 is disjoint. Correctness and product reviews T057/T058 may run together on the same stable read-only diff. Parallel regression authoring begins only in Phase 10.

## Traceability and delivery strategy

| Requirement / success criterion | Implementation | Observed proof / regressions |
|---|---|---|
| FR-001–007, SC-001–002 | T006–T011, T032 | T041, T049 |
| FR-008–014, FR-017, SC-003 | T009, T012–T017, T021, T036 | T042, T050, T054 |
| FR-015–016, SC-005 | T005, T024–T026 | T043, T052 |
| FR-018–021, SC-004 | T018–T023, T033–T035 | T044, T051 |
| FR-022–024, SC-006 | T027–T031, T033–T034 | T045, T053 |
| Nested source binding (`C` vs `T`) | T039A–T039B | T045A, T053A |
| FR-025–027, SC-007–008 | T004, T014–T017, T032–T038 | T040, T046, T048, T054 |
| FR-028, SC-009 | T037–T039 | T040–T047, T055–T060 |

US1 is the minimum useful product scope; the original core included five stories and the current accepted scope includes US6 below. Do not stop after US1 or deploy intermediate unchecked code. Independent story acceptance remains useful for diagnosis of failures; the execution sequence completes the full agreed implementation before any runs. Retain unverified remote/optional cases honestly and continue useful authorized source/gate work.

**Historical task count:** 64, comprising the original 60 plus T039A, T039B, T045A and T053A. Their current individual checkboxes are preserved. The US6/remaining-work addition below adds 23 tasks, for 87 total; none of the new work is marked complete by planning.

**Extension hooks:** No before_tasks or after_tasks hooks are registered in .specify/extensions.yml. The optional after_plan agent-context update remains assigned to root in T002.

## Phase 12: Remaining production and US6 — Follow the complete command (P1)

Use packages H–L and the exact W11 checkout/SHA in plan.md. W11 paths below are relative to that checkout; Sandbox paths are relative to this repository. No source tests or acceptance commands run in this phase. The new deployment-trace.md contract is canonical; do not duplicate its codecs in consumers.

- [X] T061 [US6] Refresh Sandbox/W11 clean/dirty ownership and exact revisions, run independent speckit-analyze on specs/054-recoverable-delivery-outcomes/{spec.md,plan.md,tasks.md}, resolve blocking contradictions and assign H–L writers in tmp/054-delivery-acceptance/ownership.md. Root alone performs the optional managed AGENTS.md update.
- [X] T062 [US6] Implement closed trace input/storage/query-detail/result codecs and fixed compiled producer decoder/manifest in sandbox/delivery/trace_models.py and sandbox/delivery/producers/{__init__.py,manifest.py,lenzora.py}; preserve distinct parent/stage/prepare/activation role identities and legacy mode, use exact W11 canonical request bytes, and reject unknown/oversized/unsafe fields.
- [X] T063 [US6] Implement atomic trace start/guard, stable ProducerRunRecord registration/linking, role-stage publications, mutation/publication-ID-before-CAS replay, original snapshot receipts, terminal immutability, finite protected/detail/guard/byte retention and read-only queries in sandbox/delivery/trace_repository.py. Expired/uncertain original keys never reopen workload or publisher authority.
- [X] T064 [P] [US6] Add bounded exact trace job and activation ports in sandbox/jobs/registry.py, sandbox/hosting/images/activation/status.py and sandbox/hosting/recovery/repository.py. Include owner-verified control submission, target/application/artifact/config/plan/proof/runtime/edge/times and schema-v2 image decoding; pass one shared deadline through capped owner reads without reconciliation or generic unbounded JSON load.
- [X] T065 [US2] Complete future failed-reason mapping and actual bounded event writes in sandbox/delivery/hosting.py, sandbox/commands/hosting.py and sandbox/delivery/service.py; add scoped bounded read_related_recoveries in sandbox/delivery/repository.py, safe next-action reasons/rendering in sandbox/commands/delivery.py, and original-metadata labels. Preserve every existing terminal byte/digest and distinguish complete failure, authority_pending, safe owner reads and >32-image partial proof.
- [X] T066 [US6] Implement injected exact trace joins, original versus recovered results, parent role-stage display, separate query-detail codec, shared deadline, output elision and same-controller guidance in sandbox/delivery/trace_service.py, sandbox/delivery/trace_context.py and sandbox/commands/delivery.py, with only required shared factory wiring in sandbox/delivery/context.py; keep legacy operation query fields and behavior unchanged.
- [X] T067 [US6] Register trace capability/producer versions and trace-capabilities/start/record/owner-status/owner-record plus inspect union in sandbox/delivery/manifest.py, sandbox/commands/manifest.py, sandbox/cli.py, mcp/wp-server/tools/delivery.py, mcp/wp-server/tools/manifest.py and mcp/wp-server/server.py; validate trace selectors before compatibility writers and emit one JSON document with equivalent CLI/MCP meaning.
- [X] T068 [P] [US6] Implement W11 early read-only bootstrap, immutable invocation ticket, strict trace start/record/readback client and preflight/normal JSON result in scripts/hosted-deployment/{trace.ts,preflight.ts,defaults.ts,files.ts,command.ts,cli.ts,release.ts}, src/types/hosted-deployment.ts and the deploy launcher. Trace dirty/missing-source preflight as partial where owner is reachable; unavailable trace blocks protected work and launcher failure never claims a retained ID.
- [X] T069 [US6] Implement W11 fixed validated owner projection and main/worker stage hooks in scripts/hosted-deployment/{trace-projection.ts,state.ts,job.ts,native.ts,worker.ts,cli.ts}. Register stable parent before original jobs; worker publishes parent-keyed role-stage enter/readback before native effects, using unchanged phase/directory argv and original request/submission bytes. Preserve main-only command finish, lost-ack IDs, immutable old run data and diagnostic legacy export.
- [X] T070 [US6] Finish matching public docs in Sandbox README.md, docs/{delivery-outcomes.md,remote-hosting.md,remote-job-runtime.md}, skills/sandbox-cli/SKILL.md and W11 README.md (existing docs/deployment.md only if present), then inspect the complete H–L production diff and record coding complete in tmp/054-delivery-acceptance/implementation-complete.md. Resolve all cross-language, private-state, authority, callback and manifest omissions before runs.

## Phase 13: Actual remaining command acceptance before regressions

- [ ] T071 [US6] Run the literal W11 ./deploy dev --preflight --json success/refusal/unavailable paths and the returned Sandbox trace inspect through actual CLI/MCP on owned fixtures; record one-JSON stdout, early ID, distinct preflight/command/deployment results, old-schema compatibility and no protected effects in tmp/054-delivery-acceptance/trace-preflight.md.
- [ ] T072 [US6] Exercise the existing W11 retained-image owner pipeline and supported Sandbox job/native adapters with synthetic external ports under an isolated home; record exact parent/stage/prepare/activation roles, source/artifact/proof/runtime/edge fields, worker stage-enter readback, unchanged workload IDs/argv, interrupted main with completed child and resumed second invocation in tmp/054-delivery-acceptance/trace-pipeline.md. Separately authorized real-host proof is recorded separately; do not build/stage/deploy to satisfy this fixture.
- [ ] T073 [US6] Exercise unknown start/record/publication responses, same-ID readback/replay, stale CAS, source/runtime/role/target/proof mismatches, pre-/post-effect store failures and legacy export through actual producer/CLI adapters; require zero duplicate jobs/initializers/builds, preserved authority and explicit gaps in tmp/054-delivery-acceptance/trace-failures.md.
- [ ] T074 [US2] Exercise complete failed/no-action, complete nonterminal authority_pending, active job/immutable owner reads, populated bounded events, original failure plus recovery, legacy terminal byte stability and image-overflow partial output through CLI/MCP; retain results and zero owner/journal mutations in tmp/054-delivery-acceptance/diagnosis.md.
- [ ] T075 [US6] Exercise trace/parent detail expiry, permanent guard/new-key capacity, publication limits, query deadlines/byte elision and redaction through owned CLI/MCP fixtures; retain actual maximum elapsed/decode overrun and explicit omissions in tmp/054-delivery-acceptance/trace-bounds.md. Finish applicable outstanding T040–T047/T045A, resolve all observed production defects together, rerun affected commands and record unavailable proof before regression authoring.

## Phase 14: Focused trace regressions, full gates and review

- [ ] T076 [P] [US6] After T071–T075, add cross-language golden byte/closed schema/role/source/legacy/elision regressions in tests/test_delivery_trace_models.py and W11 scripts/hosted-deployment/trace.test.ts, using observed fixtures and synthetic captured subprocess environments.
- [ ] T077 [P] [US6] Add observed atomic guard/CAS/publication/role-stage/terminal/retention/deadline/expired-key/no-replay regressions in tests/test_delivery_trace_repository.py; add bounded activation/job owner-read cases to tests/test_job_registry.py and tests/test_delivery_service.py under the assigned sole writer.
- [ ] T078 [P] [US6] Add exact owner joins, main-versus-child outcome, missing/contradictory/legacy/recovery proof, CLI/MCP selectors/parity/redaction/output bounds in tests/test_delivery_trace_service.py and tests/test_delivery_cli_mcp.py; extend original output/event/recovery tests in tests/test_delivery_service.py and tests/test_delivery_repository.py sequentially after their other owner finishes.
- [ ] T079 [US6] Add observed W11 bootstrap/lost-ack/unchanged-worker-submission/pre-effect-parent-stage/post-effect-gap and old preflight schema regressions in scripts/hosted-deployment/trace.test.ts and its existing deployment test files. Use fixed synthetic subprocess inputs; finish outstanding core T048–T054/T053A without duplicating pure implementation-mirroring checks.
- [ ] T080 Run focused and repository-required full Sandbox gates with documented PYTHONPATH, finite durable jobs and exact terminal evidence; run W11 Node 24.18.0 typecheck with explicit heap limit, lint, Prisma manifest, feature registry, architecture, API contracts and grouped targeted deployment gates from current package.json. Record commands, revisions, job IDs and baseline comparison in tmp/054-delivery-acceptance/{focused-gates.md,full-gates.md}; complete T055–T056 evidence only where actually observed.
- [ ] T081 Obtain independent correctness and product/output reviews of the stable Sandbox/W11 diff and actual CLI/MCP samples, covering contracts/deployment-trace.md plus all prior US1–US5 invariants; retain actionable references and proof limits in tmp/054-delivery-acceptance/{correctness-review.md,product-review.md}. Reviews may run in parallel; implementation fixes keep one owner.
- [ ] T082 Resolve corroborated review findings across owned source/docs/tests, rerun justified affected workflows/gates, and record FR-029–034/SC-010–011 plus outstanding SC-001–009 proof and explicit unverified remote/runtime cases in tmp/054-delivery-acceptance/final.md and docs/delivery-outcomes.md.
- [ ] T083 Review exact scoped diffs/gates in both checkouts, then perform repository-required commit/push only of completed authorized work to each active non-main branch; record SHAs/push results in tmp/054-delivery-acceptance/final.md. No merge, PR, release, credentials, runtime migration, build or deployment follows from task completion.

### Remaining dependency graph and traceability

T061 -> T062 -> T063; T064 can overlap H after ports are fixed. T065 is owned by the shared integrator and finishes before T066. T066 requires T063/T064; T067 requires T066. T068 may overlap disjoint Sandbox modules after T062's contract; T069 requires T068 and agreed T063/T067 interfaces. T070 requires all production T061–T069. Then T071–T075 plus outstanding historical acceptance -> T076–T079 plus outstanding core regressions -> T080 -> T081 -> T082 -> T083. T076 and T079 share W11 trace.test.ts and must be sequential; T077/T078 never write tests/test_delivery_service.py concurrently. Old T060 is subsumed by T083 for the combined final scope.

| Requirement / criterion | Production | Actual acceptance | Regression / completion |
|---|---|---|---|
| FR-029, SC-010 | T062–T063, T067–T068 | T071, T073 | T076–T079, T080–T083 |
| FR-030, SC-010–011 | T062–T064, T066, T069 | T072–T074 | T076–T079 |
| FR-031, SC-010 | T064, T066–T067 | T071–T075 | T078, T080–T083 |
| FR-032, SC-011 | T063, T065–T066 | T074–T075 | T077–T078 |
| FR-033, SC-011 | T063, T068–T069 | T072–T073 | T077, T079 |
| FR-034, SC-010–011 | T062, T064–T066, T069 | T072–T074 | T076, T078–T079 |
| Remaining US2/US4 output precision | T065 | T074 plus T042–T043 | T078 plus T050/T052 |
