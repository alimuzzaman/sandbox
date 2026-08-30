# Tasks: Bounded Host Swap Provisioning and Memory Monitoring

**Input**: Design documents from `specs/046-host-swap-monitor/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Contract, policy, ownership, privacy, interruption, and rollback tests are required. In every user-story phase, add the listed tests first, run the phase's RED gate, confirm the expected failures, and only then start the GREEN implementation tasks. A local pass never substitutes for the separate human-review or authorized live-Linux gates.

**Organization**: Tasks are grouped by user story. Controller-owned planning and the complete fail-closed preflight layer land before any protected apply action becomes reachable. Each story has an independent test criterion and can be reviewed as a bounded increment after its dependencies are complete.

## Phase 1: Setup (Shared Test and Evidence Support)

**Purpose**: Add narrow synthetic fixtures and an evidence ledger without implementing host behavior.

- [X] T001 [P] Add deterministic, secret-free host-memory fixtures with fixed `/proc`, cgroup, swap, ownership, and command-result values in `tests/host_memory_fixtures.py`
- [X] T002 [P] Add a Feature 046 acceptance ledger with separate local, synthetic-provider, human-review, live-Linux, and reboot sections in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [X] T003 [P] Add shared assertions that reject raw command output, environment dumps, host paths, process arguments, and unbounded samples in `tests/host_memory_assertions.py`

---

## Phase 2: Foundational Types, Policy, Repository, and Service Wiring

**Purpose**: Establish typed, fail-closed foundations required by every user story.

### Foundational RED tests

- [X] T004 [P] Add failing serialization and validation tests for status, plan, operation, sample, history, warning, and read-only projection models in `tests/test_host_memory_models.py`
- [X] T005 [P] Add failing boundary tests for the default 4 GiB target, valid 1 GiB and 8 GiB overrides, byte-unit arithmetic, minimum headroom, severity thresholds, and explicit unknown states in `tests/test_host_memory_policy.py`
- [X] T006 [P] Add failing repository tests for atomic writes, schema versions, bounded retention, operation identity, ownership metadata, and corrupt-state fail-closed behavior in `tests/test_host_memory_repository.py`
- [X] T007 [P] Add failing remote-result tests for typed envelopes, bounded evidence, strict underscore action allowlists, and no raw stdout or stderr projection in `tests/test_host_memory_remote.py`
- [X] T008 Run the foundational tests in `tests/test_host_memory_models.py`, `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, and `tests/test_host_memory_remote.py`; confirm they fail for missing behavior and record the RED result in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Foundational GREEN implementation

- [X] T009 Implement versioned immutable domain models, enums, validation, and bounded serialization in `sandbox/resources/host_memory/models.py` and export only public types from `sandbox/resources/host_memory/__init__.py`
- [X] T010 Implement pure byte-based planning, size-override bounds, thresholds, headroom, ownership, and fail-closed decision rules in `sandbox/resources/host_memory/policy.py`
- [X] T011 Implement atomic versioned state, sample, ownership, and operation-journal persistence behind a repository API in `sandbox/resources/host_memory/repository.py`
- [X] T012 Implement the typed remote action/result adapter with evidence limits and the explicit `host_memory_status`, `host_memory_history`, and `host_memory_apply` allowlist in `sandbox/resources/host_memory/remote.py`
- [X] T013 Implement the base application-service response envelope, dependency interfaces, and read-only status projection contract in `sandbox/resources/host_memory/service.py`
- [X] T014 Wire the private host-memory service factory and dependency adapters without a governance mutation export in `sandbox/resources/context.py`
- [X] T015 Run `tests/test_host_memory_models.py`, `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, and `tests/test_host_memory_remote.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Typed foundations are usable. No host mutation is reachable.

---

## Phase 3: User Story 1 - Inspect Current Host Memory and Swap Status (Priority: P1) - MVP

**Goal**: Return a bounded read-only status that distinguishes host memory, effective cgroup memory, swap state, ownership, freshness, warnings, and unknown evidence without mutating the host.

**Independent Test**: With only synthetic observations, `resources swap-status --json` returns the contract shape, reports unmanaged or unknown states honestly, emits no raw host data, performs no mutation, and gives Feature 047 only the immutable status projection.

### Tests for User Story 1 - RED first

- [X] T016 [P] [US1] Add failing provider observation tests for `/proc`, cgroup v1/v2, active swap, service state, ownership, effective limits, unsupported Linux evidence, and read-only execution in `tests/test_host_memory_provider.py`
- [X] T017 [P] [US1] Add failing service tests for status composition, warning derivation, freshness, unknown evidence, and bounded serialization in `tests/test_host_memory_service.py`
- [X] T018 [P] [US1] Add failing fixed-action control-contract tests for `host_memory_status` authorization, capability checks, evidence bounds, and zero mutation in `tests/test_host_memory_remote.py`
- [X] T019 [P] [US1] Add failing CLI contract tests for `resources swap-status`, `--json`, stable exit classes, and aggregate-only output in `tests/test_resource_interfaces.py`
- [X] T020 [P] [US1] Add failing architecture tests proving Feature 047 consumers receive only `HostMemoryStatusProjection` and cannot import planner, provider, repository, or apply methods in `tests/test_host_memory_interfaces.py`
- [X] T021 [US1] Run `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_interfaces.py`, and `tests/test_host_memory_interfaces.py`; confirm the User Story 1 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 1 - GREEN after T021

- [X] T022 [US1] Implement bounded read-only Linux observation, cgroup normalization, swap enumeration, ownership classification, and explicit unknown results in `sandbox/resources/host_memory/provider.py`
- [X] T023 [US1] Compose provider observations, stored samples, policy warnings, freshness, and the only immutable governance projection in `sandbox/resources/host_memory/service.py` and expose that projection without mutation methods from `sandbox/resources/context.py`
- [X] T024 [US1] Register the authorized fixed `host_memory_status` control action without shell or arbitrary-command input in `mcp/wp-server/server.py`
- [X] T025 [US1] Add the controller-side typed status request and response mapping with no SSH fallback in `sandbox/core/_remote.py`
- [X] T026 [US1] Add `resources swap-status` text and JSON presentation with stable error classes in `sandbox/commands/resources.py`
- [X] T027 [US1] Run the User Story 1 tests in `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_interfaces.py`, and `tests/test_host_memory_interfaces.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: The read-only status path is independently usable and is the MVP. Feature 047 may consume its projection but owns no Feature 046 mutation.

---

## Phase 4: User Story 2 - Build the Controller-Owned Enable Plan (Priority: P1)

**Goal**: Parse the optional valid 1-8 GiB size, obtain read-only remote status, and store one deterministic reviewable plan on the controller. No remote planning action or host mutation is reachable in this phase.

**Independent Test**: Synthetic eligible-host observations produce deterministic default, 1 GiB, 8 GiB, RAM-boundary, filesystem-boundary, and reserve-boundary plans. Invalid size and capacity inputs refuse before any remote mutation call.

### Tests for User Story 2 planning - RED first

- [ ] T028 [P] [US2] Add failing plan tests for the 4 GiB default, every valid 1-8 GiB override boundary, invalid integer/range, RAM/filesystem/free-reserve bounds, requested/effective policy, artifact inventory, confirmation binding, expiry, and already-enabled convergence in `tests/test_host_memory_policy.py`
- [ ] T029 [P] [US2] Add failing enable-transaction tests for fixed paths, restrictive modes, preallocation validation, signature writing, swap activation, service installation, atomic ordering, and idempotency in `tests/test_host_memory_provider.py`
- [ ] T030 [P] [US2] Add failing service tests for controller-owned plan identity, size propagation, request identity, confirmation binding, authorization-before-side-effect, phase journaling, and replay-safe convergence in `tests/test_host_memory_service.py`
- [ ] T031 [P] [US2] Add failing control-contract tests for strict `host_memory_apply` canonical-plan schemas, capability checks, typed results, allowed canonical `effective_policy.size_gib`, rejected top-level overrides, and bounded evidence in `tests/test_host_memory_remote.py` and `tests/test_resource_remote.py`
- [ ] T032 [P] [US2] Add failing CLI tests for `resources swap-plan --operation enable --size-gib 1..8`, invalid size/mode handling, confirmed `resources swap-apply`, JSON parity, refusal without confirmation, and replay-safe plan identities in `tests/test_resource_interfaces.py`
- [ ] T033 [US2] Run the User Story 2 planning/apply tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`; confirm the assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Controller planning implementation - GREEN after T033

- [X] T034 [US2] Implement deterministic controller-side enable-plan construction, 4 GiB default, valid 1-8 GiB requested/effective sizing, every capacity calculation, preconditions, and confirmation digest rules in `sandbox/resources/host_memory/policy.py`
- [ ] T035 [US2] Implement stored plan, requested/effective policy, confirmation, request, phase, artifact, and ownership records with atomic transitions in `sandbox/resources/host_memory/repository.py`
- [X] T036 [US2] Implement read-only controller plan orchestration from `host_memory_status` evidence only, with no remote plan action or provider mutation, in `sandbox/resources/host_memory/service.py`
- [X] T037 [US2] Add `resources swap-plan` parsing and text/JSON rendering, including valid `--size-gib 1..8` propagation and disable-mode rejection, in `sandbox/commands/resources.py`
- [ ] T038 [US2] Run the plan-focused tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_service.py`, and `tests/test_resource_interfaces.py`; require the read-only planning path GREEN while protected apply remains unavailable, and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: The reviewed plan is controller-owned and read-only. The remote allowlist still has no planning action, and protected apply is not registered or reachable.

---

## Phase 5: User Story 3 - Complete Fail-Closed Preconditions Before Apply Reachability (Priority: P1)

**Goal**: Refuse every unsafe, unsupported, ambiguous, insufficient-space, conflicting, or unowned condition before mutation. This phase blocks registration of protected apply.

**Independent Test**: The complete synthetic refusal matrix produces normative `refused`, `partial`, or `failed` results plus stable reason codes, with no file write, service change, swap command, arbitrary remote action, or Feature 047 mutation reachability.

### Tests for User Story 3 - RED first

- [ ] T039 [P] [US3] Add a failing policy refusal matrix for unregistered target, unknown capacity, unsupported OS/facility, insufficient disk/RAM/reserve, conflicting or multiple swap, stale/expired/drifted plan, capability or revision absence, unsafe path, concurrent operation, incomplete rollback, and ambiguous ownership in `tests/test_host_memory_policy.py`
- [ ] T040 [P] [US3] Add failing provider preflight tests proving every filesystem, ownership, capacity, service, lock, rollback-block, and capability check occurs before the first side effect and foreign artifacts remain untouched in `tests/test_host_memory_provider.py`
- [ ] T041 [P] [US3] Add failing control-boundary tests for the exact underscore action allowlist, absence of a remote plan action, unknown keys/actions, top-level sizes, arbitrary paths, shell syntax, raw SSH fallback, revision/protocol skew, malformed output, and oversized evidence in `tests/test_host_memory_remote.py` and `tests/test_resource_remote.py`
- [ ] T042 [P] [US3] Add failing composition tests proving Feature 047 governance cannot invoke Feature 046 plan, apply, disable, provider, repository, or remote mutation paths in `tests/test_host_memory_interfaces.py`
- [ ] T043 [US3] Run `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_host_memory_interfaces.py`; confirm the User Story 3 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Fail-closed implementation - GREEN after T043

- [ ] T044 [US3] Complete typed refusal precedence, unknown-evidence handling, plan invalidation, capacity checks, concurrent/rollback blocks, and zero-mutation decisions in `sandbox/resources/host_memory/policy.py`
- [ ] T045 [US3] Complete provider path, filesystem, ownership, service, capacity, operation-lock, rollback-block, capability, and foreign-artifact preflight checks before all mutation in `sandbox/resources/host_memory/provider.py`
- [ ] T046 [US3] Enforce the exact underscore action allowlist, no remote plan action, strict canonical apply schema, remote revision/protocol checks, normative outcomes, and bounded failure mapping across `sandbox/resources/host_memory/remote.py`, `mcp/wp-server/server.py`, and `sandbox/core/_remote.py`
- [ ] T047 [US3] Run the User Story 3 tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_host_memory_interfaces.py`; require GREEN before any apply registration task starts and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Every approved precondition and refusal boundary is GREEN. Protected apply remains unreachable until Phase 6.

---

## Phase 6: User Story 2 - Enable Bounded Swap with Monitoring (Priority: P1)

**Goal**: Only after T047, explicitly confirm the same controller-owned plan and create the one fixed-path, policy-sized owned swap configuration plus bounded monitoring service.

### Protected apply implementation - GREEN only after T047

- [ ] T048 [US2] Implement the fixed-path swap and monitor enable transaction behind the completed preflight gate, with restrictive permissions, policy-selected size, read-back verification, idempotent convergence, and owned rollback hooks in `sandbox/resources/host_memory/provider.py`
- [ ] T049 [US2] Implement apply orchestration with authorization, exact confirmation, current plan/request identity, per-phase revalidation, journaling, reconciliation, normative outcomes, and bounded evidence in `sandbox/resources/host_memory/service.py`
- [ ] T050 [US2] Register only the strict authorized `host_memory_apply` control action, accepting canonical effective policy but no top-level size, path, unit, or shell override, in `mcp/wp-server/server.py`
- [ ] T051 [US2] Add typed apply transport, replay-safe operation identity, and ambiguous-output mapping to `partial` plus stable reason codes without direct-host fallback in `sandbox/core/_remote.py`
- [ ] T052 [US2] Add confirmed `resources swap-apply` text/JSON flows with stable normative outcomes and reason-code exit classes in `sandbox/commands/resources.py`
- [ ] T053 [US2] Run the complete User Story 2 tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Eligible plans can be applied only through the completed safety layer; mutation is fixed-path, policy-sized, authorized, confirmed, owned, and replay-safe.

---

## Phase 7: User Story 4 - Review Bounded Aggregate History (Priority: P2)

**Goal**: Collect and return bounded aggregate memory/swap samples and warnings with rotation, freshness, privacy limits, a hard five-second deadline, and no overlapping monitor run.

**Independent Test**: Synthetic timer samples finish or terminate within five seconds, never overlap, record a durable valid/partial/failed result, rotate at the configured bound, and return only allowed aggregate fields.

### Tests for User Story 4 - RED first

- [ ] T054 [P] [US4] Add failing sample, freshness, warning, serialization, ordering, and maximum-count tests in `tests/test_host_memory_models.py`
- [ ] T055 [P] [US4] Add failing monitor collection tests for host/effective memory, swap totals, bounded warning transitions, unsupported evidence, and aggregate-only capture in `tests/test_host_memory_provider.py`
- [ ] T056 [P] [US4] Add failing provider/service tests for the hard five-second sample deadline, timed-out collector termination, durable partial/failed samples, lock/timer no-overlap behavior, and recovery by the next scheduled run in `tests/test_host_memory_provider.py` and `tests/test_host_memory_service.py`
- [ ] T057 [P] [US4] Add failing repository tests for atomic append, deterministic rotation, corrupt-tail handling, retention bounds, and no sensitive fields in `tests/test_host_memory_repository.py`
- [ ] T058 [P] [US4] Add failing service, `host_memory_history`, transport, and `resources swap-history` tests for range, count, 1,000-sample, 1 MiB, normative outcome, and reason-code limits in `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`
- [ ] T059 [US4] Run `tests/test_host_memory_models.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`; confirm the User Story 4 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 4 - GREEN after T059

- [ ] T060 [US4] Implement bounded sample, history, freshness, and warning serialization in `sandbox/resources/host_memory/models.py`
- [ ] T061 [US4] Implement aggregate-only monitor sampling and warning derivation inputs in `sandbox/resources/host_memory/provider.py`
- [ ] T062 [US4] Implement the hard five-second collector deadline, timed-out child termination, durable partial/failed result, fixed non-overlapping monitor lock/timer behavior, and next-run recovery in `sandbox/resources/host_memory/provider.py` and `sandbox/resources/host_memory/service.py`
- [ ] T063 [US4] Implement atomic bounded sample append, rotation, ordering, and corrupt-data isolation in `sandbox/resources/host_memory/repository.py`
- [X] T064 [US4] Implement bounded history orchestration and register the strict authorized `host_memory_history` action in `sandbox/resources/host_memory/service.py` and `mcp/wp-server/server.py`
- [X] T065 [US4] Add typed history transport and `resources swap-history` text/JSON presentation with enforced limits in `sandbox/core/_remote.py` and `sandbox/commands/resources.py`
- [ ] T066 [US4] Run the User Story 4 tests in `tests/test_host_memory_models.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Operators can inspect privacy-bounded aggregate history, and monitor overruns cannot overlap or fabricate samples.

---

## Phase 8: User Story 5 - Disable Only Fully Owned Configuration (Priority: P2)

**Goal**: Plan and disable active configuration only when every affected artifact is fully owned and the host remains safe. Stop future samples while preserving prior bounded aggregate history under a minimal disabled-state receipt.

**Independent Test**: A fully owned synthetic configuration disables in journaled reverse order, preserves bounded history, and removes only active owned artifacts; altered, foreign, ambiguous, insufficient-headroom, or stale-plan fixtures refuse before mutation.

### Tests for User Story 5 - RED first

- [ ] T067 [P] [US5] Add failing disable-plan tests for ownership signatures, active-use checks, strict-greater-than post-disable headroom, fixed active artifact inventory, preserved history, disabled-state receipt, stale confirmation, and foreign-state refusal in `tests/test_host_memory_policy.py`
- [ ] T068 [P] [US5] Add failing disable-transaction tests for monitor stop, timer/service removal, swap deactivation, owned-file removal, reverse ordering, read-back verification, no new sample, preserved bounded history, and foreign-artifact preservation in `tests/test_host_memory_provider.py`
- [ ] T069 [P] [US5] Add failing service, remote-contract, and CLI tests for separately confirmed disable planning/apply, authorization, replay, normative outcomes, and stable refusal codes in `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`
- [ ] T070 [P] [US5] Add failing repository tests for disable phase journaling, ownership snapshot binding, terminal convergence, bounded-history preservation, and atomic conversion to the minimal disabled-state receipt in `tests/test_host_memory_repository.py`
- [ ] T071 [US5] Run `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, `tests/test_resource_interfaces.py`, and `tests/test_host_memory_repository.py`; confirm the User Story 5 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 5 - GREEN after T071

- [ ] T072 [US5] Implement owned-disable planning, strict-greater-than headroom, active-use, preserved-history, disabled-state receipt, confirmation, and fail-closed foreign-state rules in `sandbox/resources/host_memory/policy.py`
- [ ] T073 [US5] Implement journaled reverse-order disable and owned-only active cleanup, stop future samples, preserve bounded history, atomically minimize the receipt, and verify final state in `sandbox/resources/host_memory/provider.py`
- [ ] T074 [US5] Implement disable orchestration and strict authorized disable-plan/application handling through controller planning and `host_memory_apply` in `sandbox/resources/host_memory/service.py` and `mcp/wp-server/server.py`
- [ ] T075 [US5] Add typed disable transport and confirmed CLI flow without arbitrary artifact selection in `sandbox/core/_remote.py` and `sandbox/commands/resources.py`
- [ ] T076 [US5] Run the User Story 5 tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, `tests/test_resource_interfaces.py`, and `tests/test_host_memory_repository.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Disable removes only fully owned active state, stops sampling, and preserves bounded recovery history without touching foreign configuration.

---

## Phase 9: User Story 6 - Recover Safely from Interruptions and Replays (Priority: P2)

**Goal**: Reconcile every interrupted enable or disable phase from durable evidence, replay the same request safely, and roll back only proven owned partial state.

**Independent Test**: Fault injection at every phase boundary, duplicate operation identity, lost response, malformed response, and partial rollback converges or returns a normative outcome with a stable reason code; no second operation identity launches and no unowned artifact changes.

### Tests for User Story 6 - RED first

- [ ] T077 [P] [US6] Add failing journal recovery tests for every enable/disable phase, duplicate operation IDs, stale in-progress records, terminal replay, and conflicting operation identity in `tests/test_host_memory_repository.py`
- [ ] T078 [P] [US6] Add failing provider fault-injection tests for interruption before and after every side effect, read-back reconciliation, owned rollback, rollback failure, ambiguous ownership, and preserved disable history in `tests/test_host_memory_provider.py`
- [ ] T079 [P] [US6] Add failing service tests for same-identity replay, ledger lookup before retry, reconcile-versus-rollback decisions, `refused` plus `operation_in_progress`, and `partial` plus `response_invalid` in `tests/test_host_memory_service.py`
- [ ] T080 [P] [US6] Add failing transport tests for timeout, empty output, malformed output, lost response, bounded ledger lookup, normative outcomes/reason codes, and prohibition on a second operation identity in `tests/test_host_memory_remote.py` and `tests/test_resource_remote.py`
- [ ] T081 [P] [US6] Add failing CLI tests for replay guidance, stable normative-outcome exit classes, no automatic retry under a new identity, and bounded JSON errors in `tests/test_resource_interfaces.py`
- [ ] T082 [US6] Run `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`; confirm the User Story 6 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 6 - GREEN after T082

- [ ] T083 [US6] Implement durable operation lookup, phase reconciliation, terminal replay, conflict detection, and bounded failure retention in `sandbox/resources/host_memory/repository.py`
- [ ] T084 [US6] Implement phase-aware read-back reconciliation and owned-only rollback with incomplete-block and preserved-history evidence in `sandbox/resources/host_memory/provider.py`
- [ ] T085 [US6] Implement replay orchestration, ambiguous-acceptance mapping to normative outcomes/reason codes, reconcile/rollback selection, and stable terminal results in `sandbox/resources/host_memory/service.py`
- [ ] T086 [US6] Implement bounded ledger lookup and same-identity replay across `mcp/wp-server/server.py`, `sandbox/core/_remote.py`, and `sandbox/commands/resources.py`
- [ ] T087 [US6] Run the User Story 6 tests in `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resource_remote.py`, and `tests/test_resource_interfaces.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Interruptions and uncertain transport outcomes reconcile from durable evidence without duplicate mutation, non-normative statuses, unsafe cleanup, or history loss.

---

## Phase 10: Documentation, Regression Proof, and External Acceptance Gates

**Purpose**: Synchronize operator guidance, prove adjacent boundaries, and retain non-local release gates as explicit unchecked work.

- [X] T088 Document status, controller-owned planning, valid size overrides, apply, history, disable-history preservation, ownership, fixed paths, privacy, replay, rollback, normative outcomes/reason codes, and Feature 047 read-only composition in `docs/resource-monitoring.md`
- [ ] T089 [P] Update the resource command overview, JSON examples, size/mode validation, refusal semantics, and external acceptance caveats in `README.md`
- [X] T090 [P] Update CLI-first operator guidance, confirmation rules, replay rules, fixed underscore wire actions, controller-owned planning, and no-SSH-fallback constraints in `skills/sandbox-cli/SKILL.md`
- [ ] T091 [P] Add command registration, help, JSON schema, error-class, documentation-link, and remote-action regression coverage in `tests/test_resource_interfaces.py`, `tests/test_resource_remote.py`, and `tests/test_remote_service_help.py`
- [ ] T092 [P] Add regression tests proving Spec 043 disk monitoring/scheduling, resource/MCP behavior, and workspace/remote contracts remain separate and gain no direct Feature 046 state access in `tests/test_storage_monitor_policy.py`, `tests/test_storage_monitor_schedule.py`, `tests/test_storage_monitor_runner.py`, `tests/test_mcp_resource_tier.py`, `tests/test_resource_service.py`, `tests/test_workspace_contracts.py`, and `tests/test_remote.py`
- [ ] T093 Run all Feature 046 and adjacent regression tests in `tests/test_host_memory_models.py`, `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_host_memory_interfaces.py`, `tests/test_resource_interfaces.py`, `tests/test_resource_remote.py`, `tests/test_remote_service_help.py`, `tests/test_storage_monitor_policy.py`, `tests/test_storage_monitor_schedule.py`, `tests/test_storage_monitor_runner.py`, `tests/test_mcp_resource_tier.py`, `tests/test_resource_service.py`, `tests/test_workspace_contracts.py`, and `tests/test_remote.py`; record exact bounded results in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T094 Run the repository-supported full test and static-check gates, verify links and task/spec/source synchronization, and record exact commands plus bounded results in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T095 Obtain and record explicit human review of authentication, authorization, privileged fixed paths, ownership proof, rollback, privacy, cryptographic identities, dependency trust, and production-path risk in `specs/046-host-swap-monitor/acceptance-evidence.md`; do not treat local tests as this approval
- [ ] T096 Execute the complete fixed authenticated transport/provider refusal matrix from `specs/046-host-swap-monitor/quickstart.md`, including target/platform/facility/service/protocol/revision, size/capacity, ownership/path, plan/confirmation, concurrency/rollback, and ambiguous-response classes; record bounded synthetic-provider evidence separately and do not call it live-host proof
- [ ] T097 After separate deployment authorization and T095 approval, update an approved disposable Linux host only through the supported Sandbox lifecycle, independently verify the installed revision matches the accepted SHA, and record redacted evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T098 After T097 and separate live-test authorization, execute the safe live-Linux eligible enable, immediate replay, status, monitoring/history, owned-disable, preserved-history, privacy, and rollback matrix plus only those refusal cases approved and safe to create on that host; record aggregate redacted results separately from T096 synthetic evidence
- [ ] T099 Keep reboot persistence explicitly unverified in `specs/046-host-swap-monitor/acceptance-evidence.md` unless separately authorized; if authorized, verify status, monitoring, ownership, replay, and bounded history after reboot without changing the accepted revision
- [ ] T100 Reconcile local, synthetic-provider, human-review, live-Linux, and reboot evidence against `specs/046-host-swap-monitor/spec.md`, leaving every unperformed external gate open and making no release-readiness claim in `specs/046-host-swap-monitor/acceptance-evidence.md`

---

## Dependencies and Execution Order

### Phase dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on Phase 1. Blocks every user story.
- **US1 (Phase 3)**: Depends on Phase 2. This is the read-only MVP and creates the only Feature 046 surface Feature 047 may consume.
- **US2 planning (Phase 4)**: Depends on US1 status evidence and Phase 2 planning foundations. It remains controller-only and read-only.
- **US3 safety gate (Phase 5)**: Depends on the US2 plan/apply contract shapes and blocks all protected apply registration or reachability.
- **US2 apply (Phase 6)**: Depends on T047 GREEN. T048-T053 must not start while any required refusal or preflight behavior is missing.
- **US4 (Phase 7)**: Depends on US1 observation and Phase 2 repository foundations. It can proceed in parallel with US2/US3 once shared interfaces are stable.
- **US5 (Phase 8)**: Depends on US2 ownership/enable behavior and the US3 safety gate.
- **US6 (Phase 9)**: Depends on US2 and US5 phase journals and transaction boundaries.
- **Documentation and gates (Phase 10)**: T088-T092 follow implemented stories. T093-T094 follow all local implementation/docs. T095 is a distinct human gate. T096 is fixed synthetic-provider acceptance and never live proof. T097-T098 require separate authorization and follow T095. T099 requires separate reboot authorization. T100 reports only evidence that exists.

### User-story dependency graph

```text
Setup -> Foundational -> US1 (MVP)
                          |-> US2 plan -> US3 safety gate -> US2 apply -> US5 -> US6
                          |-> US4 -------------------------------------------|
                                                                                v
                                                                         Docs/local gates
                                                                                v
                                                                     Human review + synthetic matrix
                                                                                v
                                                                    Authorized live-Linux gate
```

### Within each user story

1. Add all listed tests without production behavior.
2. Run the story RED task and confirm failures are caused by missing behavior.
3. Implement models/policy/repository/provider before service orchestration.
4. Keep planning controller-owned; no remote planning action exists.
5. Complete T047 before registering or exposing `host_memory_apply`.
6. Implement only the fixed underscore remote actions before CLI presentation.
7. Run the story GREEN task and retain bounded evidence.
8. Do not move an external gate to complete based on local, mocked, or synthetic-provider evidence.

### Feature 047 composition boundary

- Feature 046 owns swap observation, planning, apply, monitoring, history, disable, operation journaling, reconciliation, and rollback.
- Feature 047 may consume only the immutable `HostMemoryStatusProjection` produced by Feature 046.
- Feature 047 must not import or call Feature 046 policy, repository, provider, remote adapter, apply, disable, reconciliation, or rollback surfaces.

---

## Parallel Examples

### Foundational work

```text
T004 models tests | T005 policy tests | T006 repository tests | T007 remote tests
After T008: T009 models -> T010 policy; T011 repository and T012 remote may proceed in parallel once T009 public types stabilize
```

### User Story 1

```text
T016 provider tests | T017 service tests | T018 remote tests | T019 CLI tests | T020 interface tests
After T021: T022 provider -> T023 service; T024 server and T025 controller transport can then split before T026 CLI
```

### User Story 2 planning

```text
T028 plan tests | T029 provider tests | T030 service tests | T031 remote tests | T032 CLI tests
After T033: T034 policy and T035 repository in parallel -> T036 controller service and T037 CLI plan -> T038
```

### User Story 3 safety gate

```text
T039 policy matrix | T040 provider preflight | T041 remote boundary | T042 Feature 047 boundary
After T043: T044 policy -> T045 provider -> T046 protocol mapping -> T047 GREEN gate
```

### User Story 2 protected apply

```text
Only after T047: T048 provider -> T049 service -> T050 server/T051 transport -> T052 CLI -> T053
```

### User Story 4

```text
T054 models | T055 provider capture | T056 timeout/no-overlap | T057 repository | T058 interfaces
After T059: T060 models; T061 sampling and T063 repository in parallel -> T062 deadline/no-overlap -> T064 service/server -> T065 transport/CLI
```

### User Story 5

```text
T067 policy | T068 provider | T069 interfaces | T070 repository
After T071: T072 policy -> T073 provider; T074 service/server -> T075 transport/CLI
```

### User Story 6

```text
T077 repository | T078 provider | T079 service | T080 transport | T081 CLI
After T082: T083 repository -> T084 provider -> T085 service -> T086 remote/CLI
```

---

## MVP and Incremental Delivery

### MVP: Read-only status only

1. Complete Phase 1 and Phase 2.
2. Complete US1 through T027.
3. Verify `resources swap-status` is read-only, privacy-bounded, fail-closed, and independently testable.
4. Expose only `HostMemoryStatusProjection` to Feature 047.
5. Do not imply mutation, live-host, reboot, or release acceptance from the MVP.

### Later increments

1. Complete controller-owned US2 planning through T038.
2. Complete the US3 refusal/preflight gate through T047 before any apply path is reachable.
3. Add protected US2 apply through T053.
4. Add US4 bounded history with explicit deadline and no-overlap behavior.
5. Add US5 owned-only disable with preserved bounded recovery history.
6. Add US6 interruption and replay convergence using normative outcomes and reason codes.
7. Complete local docs/regressions and synthetic-provider refusal acceptance, then retain human and live-Linux gates as separate authorization-bound work.

---

## Notes

- Every task is unchecked by design. Implementation evidence determines completion later.
- `[P]` means different files or isolated test surfaces with no unmet dependency.
- Story labels appear only on user-story work; setup, foundational, documentation, and acceptance-gate tasks remain shared.
- Exact fixed paths, sizes, action names, schemas, identities, ownership signatures, retention bounds, and warning thresholds must come from the approved Feature 046 design artifacts, not ad hoc implementation choices.
- The only wire actions are `host_memory_status`, `host_memory_history`, and `host_memory_apply`; controller-owned planning never creates a remote plan action.
- Public outcomes remain `planned`, `applied`, `already_current`, `refused`, `partial`, `failed`, `rollback_complete`, and `rollback_incomplete`; blocking or ambiguous conditions use stable reason codes, not new outcomes.
- Never expose secrets, inherited environment values, raw process details, raw command output, arbitrary host paths, or unbounded telemetry in tests, logs, contracts, evidence, or user output.
- Never deploy, access a live host, reboot, or mutate remote state while generating or analyzing these tasks.
