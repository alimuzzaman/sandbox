# Tasks: Bounded Host Swap Provisioning and Memory Monitoring

**Input**: Design documents from `specs/046-host-swap-monitor/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Contract, policy, ownership, privacy, interruption, and rollback tests are required. In every user-story phase, add the listed tests first, run the phase's RED gate, confirm the expected failures, and only then start the GREEN implementation tasks. A local pass never substitutes for the separate human-review or authorized live-Linux gates.

**Organization**: Tasks are grouped by user story. Each story has an independent test criterion and can be reviewed as a bounded increment after its dependencies are complete.

## Phase 1: Setup (Shared Test and Evidence Support)

**Purpose**: Add narrow synthetic fixtures and an evidence ledger without implementing host behavior.

- [ ] T001 [P] Add deterministic, secret-free host-memory fixtures with fixed `/proc`, cgroup, swap, ownership, and command-result values in `tests/host_memory_fixtures.py`
- [ ] T002 [P] Add a Feature 046 acceptance ledger with separate local, human-review, live-Linux, and reboot sections in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T003 [P] Add shared assertions that reject raw command output, environment dumps, host paths, process arguments, and unbounded samples in `tests/host_memory_assertions.py`

---

## Phase 2: Foundational Types, Policy, Repository, and Service Wiring

**Purpose**: Establish typed, fail-closed foundations required by every user story.

### Foundational RED tests

- [ ] T004 [P] Add failing serialization and validation tests for status, plan, operation, sample, history, warning, and read-only projection models in `tests/test_host_memory_models.py`
- [ ] T005 [P] Add failing boundary tests for the fixed 4 GiB target, byte-unit arithmetic, minimum headroom, severity thresholds, and explicit unknown states in `tests/test_host_memory_policy.py`
- [ ] T006 [P] Add failing repository tests for atomic writes, schema versions, bounded retention, operation identity, ownership metadata, and corrupt-state fail-closed behavior in `tests/test_host_memory_repository.py`
- [ ] T007 [P] Add failing remote-result tests for typed envelopes, bounded evidence, strict action allowlists, and no raw stdout or stderr projection in `tests/test_host_memory_remote.py`
- [ ] T008 Run the foundational tests in `tests/test_host_memory_models.py`, `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, and `tests/test_host_memory_remote.py`; confirm they fail for missing behavior and record the RED result in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Foundational GREEN implementation

- [ ] T009 Implement versioned immutable domain models, enums, validation, and bounded serialization in `sandbox/resources/host_memory/models.py` and export only public types from `sandbox/resources/host_memory/__init__.py`
- [ ] T010 Implement pure byte-based planning, thresholds, headroom, ownership, and fail-closed decision rules in `sandbox/resources/host_memory/policy.py`
- [ ] T011 Implement atomic versioned state, sample, ownership, and operation-journal persistence behind a repository API in `sandbox/resources/host_memory/repository.py`
- [ ] T012 Implement the typed remote action/result adapter with evidence limits and an explicit action allowlist in `sandbox/resources/host_memory/remote.py`
- [ ] T013 Implement the base application-service response envelope, dependency interfaces, and read-only status projection contract in `sandbox/resources/host_memory/service.py`
- [ ] T014 Wire the private host-memory service factory and dependency adapters without a governance mutation export in `sandbox/resources/context.py`
- [ ] T015 Run `tests/test_host_memory_models.py`, `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, and `tests/test_host_memory_remote.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Typed foundations are usable. No host mutation is reachable yet.

---

## Phase 3: User Story 1 - Inspect Current Host Memory and Swap Status (Priority: P1) - MVP

**Goal**: Return a bounded read-only status that distinguishes host memory, effective cgroup memory, swap state, ownership, freshness, warnings, and unknown evidence without mutating the host.

**Independent Test**: With only synthetic observations, `resources swap-status --json` returns the contract shape, reports unmanaged or unknown states honestly, emits no raw host data, performs no mutation, and gives Feature 047 only the immutable status projection.

### Tests for User Story 1 - RED first

- [ ] T016 [P] [US1] Add failing provider observation tests for `/proc`, cgroup v1/v2, active swap, service state, ownership, effective limits, unsupported Linux evidence, and read-only execution in `tests/test_host_memory_provider.py`
- [ ] T017 [P] [US1] Add failing service tests for status composition, warning derivation, freshness, unknown evidence, and bounded serialization in `tests/test_host_memory_service.py`
- [ ] T018 [P] [US1] Add failing fixed-action control-contract tests for `host_memory.status` authorization, capability checks, evidence bounds, and zero mutation in `tests/test_host_memory_remote.py`
- [ ] T019 [P] [US1] Add failing CLI contract tests for `resources swap-status`, `--json`, stable exit classes, and aggregate-only output in `tests/test_resources_cli.py`
- [ ] T020 [P] [US1] Add failing architecture tests proving Feature 047 consumers receive only `HostMemoryStatusProjection` and cannot import planner, provider, repository, or apply methods in `tests/test_host_memory_interfaces.py`
- [ ] T021 [US1] Run `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resources_cli.py`, and `tests/test_host_memory_interfaces.py`; confirm the User Story 1 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 1 - GREEN after T021

- [ ] T022 [US1] Implement bounded read-only Linux observation, cgroup normalization, swap enumeration, ownership classification, and explicit unknown results in `sandbox/resources/host_memory/provider.py`
- [ ] T023 [US1] Compose provider observations, stored samples, policy warnings, freshness, and the only immutable governance projection in `sandbox/resources/host_memory/service.py` and expose that projection without mutation methods from `sandbox/resources/context.py`
- [ ] T024 [US1] Register the authorized fixed `host_memory.status` control action without shell or arbitrary-command input in `mcp/wp-server/server.py`
- [ ] T025 [US1] Add the controller-side typed status request and response mapping with no SSH fallback in `sandbox/core/_remote.py`
- [ ] T026 [US1] Add `resources swap-status` text and JSON presentation with stable error classes in `sandbox/commands/resources.py`
- [ ] T027 [US1] Run the User Story 1 tests in `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resources_cli.py`, and `tests/test_host_memory_interfaces.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: The read-only status path is independently usable and is the MVP. Feature 047 may consume its projection but owns no Feature 046 mutation.

---

## Phase 4: User Story 2 - Plan and Enable Bounded Swap with Monitoring (Priority: P1)

**Goal**: Produce a reviewable plan and, only after explicit confirmation and authorization, create the one fixed 4 GiB owned swap configuration plus the bounded monitoring service.

**Independent Test**: Synthetic eligible-host fixtures yield one deterministic plan; a confirmed apply creates only fixed owned artifacts, records every phase, enables swap and monitoring idempotently, and returns bounded evidence. Missing confirmation, authorization, space, capability, or ownership yields no mutation.

### Tests for User Story 2 - RED first

- [ ] T028 [P] [US2] Add failing plan tests for fixed target size, preconditions, disk headroom, artifact inventory, confirmation token binding, expiry, and already-enabled convergence in `tests/test_host_memory_policy.py`
- [ ] T029 [P] [US2] Add failing enable-transaction tests for fixed paths, restrictive modes, preallocation validation, signature writing, swap activation, service installation, atomic ordering, and idempotency in `tests/test_host_memory_provider.py`
- [ ] T030 [P] [US2] Add failing service tests for plan identity, request identity, confirmation binding, authorization-before-side-effect, phase journaling, and replay-safe convergence in `tests/test_host_memory_service.py`
- [ ] T031 [P] [US2] Add failing control-contract tests for strict `host_memory.plan` and `host_memory.apply` schemas, capability checks, typed results, and bounded evidence in `tests/test_host_memory_remote.py`
- [ ] T032 [P] [US2] Add failing CLI tests for `resources swap-plan`, confirmed `resources swap-apply`, JSON parity, refusal without confirmation, and replay-safe request IDs in `tests/test_resources_cli.py`
- [ ] T033 [US2] Run `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`; confirm the User Story 2 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 2 - GREEN after T033

- [ ] T034 [US2] Implement deterministic enable-plan construction, fixed 4 GiB sizing, headroom checks, preconditions, and confirmation digest rules in `sandbox/resources/host_memory/policy.py`
- [ ] T035 [US2] Implement stored plan, confirmation, request, phase, artifact, and ownership records with atomic transitions in `sandbox/resources/host_memory/repository.py`
- [ ] T036 [US2] Implement the fixed-path swap and monitor enable transaction, restrictive permissions, read-back verification, idempotent convergence, and owned rollback hooks in `sandbox/resources/host_memory/provider.py`
- [ ] T037 [US2] Implement plan and apply orchestration with authorization, confirmation, request identity, journaling, reconciliation, and bounded evidence in `sandbox/resources/host_memory/service.py`
- [ ] T038 [US2] Register strict authorized `host_memory.plan` and `host_memory.apply` control actions without arbitrary paths, sizes, units, or shell fragments in `mcp/wp-server/server.py`
- [ ] T039 [US2] Add typed plan/apply transport, replay-safe request identity, and ambiguous-output handling without direct-host fallback in `sandbox/core/_remote.py`
- [ ] T040 [US2] Add `resources swap-plan` and confirmed `resources swap-apply` text/JSON flows with stable refusal and replay exit classes in `sandbox/commands/resources.py`
- [ ] T041 [US2] Run the User Story 2 tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Eligible hosts can be planned and converged through the controlled path; mutation remains fixed, authorized, confirmed, owned, and replay-safe.

---

## Phase 5: User Story 3 - Fail Closed on Unsafe, Unsupported, or Unowned Hosts (Priority: P1)

**Goal**: Refuse every unsafe, unsupported, ambiguous, insufficient-space, conflicting, or unowned condition before mutation and explain the bounded reason.

**Independent Test**: A table of unsafe synthetic fixtures produces typed refusals with no file write, service change, swap command, arbitrary remote action, or Feature 047 mutation reachability.

### Tests for User Story 3 - RED first

- [ ] T042 [P] [US3] Add a failing policy refusal matrix for unknown capacity, unsupported OS, insufficient headroom, conflicting swap, stale plan, capability absence, unsafe path, and ambiguous ownership in `tests/test_host_memory_policy.py`
- [ ] T043 [P] [US3] Add failing provider preflight tests proving all safety and ownership checks occur before the first side effect and foreign artifacts remain untouched in `tests/test_host_memory_provider.py`
- [ ] T044 [P] [US3] Add failing control-boundary tests for unknown keys, unknown actions, arbitrary paths, arbitrary sizes, shell syntax, raw SSH fallback, and oversized evidence in `tests/test_host_memory_remote.py`
- [ ] T045 [P] [US3] Add failing composition tests proving Feature 047 governance cannot invoke Feature 046 plan, apply, disable, provider, repository, or remote mutation paths in `tests/test_host_memory_interfaces.py`
- [ ] T046 [US3] Run `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_remote.py`, and `tests/test_host_memory_interfaces.py`; confirm the User Story 3 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 3 - GREEN after T046

- [ ] T047 [US3] Complete typed refusal precedence, unknown-evidence handling, stale-plan invalidation, and zero-mutation decisions in `sandbox/resources/host_memory/policy.py`
- [ ] T048 [US3] Complete provider path, filesystem, ownership, service, capability, and foreign-artifact preflight checks before all mutation in `sandbox/resources/host_memory/provider.py`
- [ ] T049 [US3] Enforce strict request schemas and bounded failure mapping across `mcp/wp-server/server.py`, `sandbox/core/_remote.py`, and `sandbox/commands/resources.py`
- [ ] T050 [US3] Run the User Story 3 tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_remote.py`, and `tests/test_host_memory_interfaces.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Unsafe, unsupported, ambiguous, and unowned cases fail closed with no host mutation.

---

## Phase 6: User Story 4 - Review Bounded Aggregate History (Priority: P2)

**Goal**: Collect and return bounded aggregate memory/swap samples and warnings with rotation, freshness, and privacy limits.

**Independent Test**: Synthetic timer samples rotate at the configured bound; history returns only allowed aggregate fields in chronological order with freshness and warning state, while raw process, command, path, environment, and unbounded data never persist or render.

### Tests for User Story 4 - RED first

- [ ] T051 [P] [US4] Add failing sample, freshness, warning, serialization, ordering, and maximum-count tests in `tests/test_host_memory_models.py`
- [ ] T052 [P] [US4] Add failing monitor collection tests for host/effective memory, swap totals, bounded warning transitions, unsupported evidence, and aggregate-only capture in `tests/test_host_memory_provider.py`
- [ ] T053 [P] [US4] Add failing repository tests for atomic append, deterministic rotation, corrupt-tail handling, retention bounds, and no sensitive fields in `tests/test_host_memory_repository.py`
- [ ] T054 [P] [US4] Add failing service, control-contract, and CLI tests for bounded `host_memory.history` and `resources swap-history` limits in `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`
- [ ] T055 [US4] Run `tests/test_host_memory_models.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`; confirm the User Story 4 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 4 - GREEN after T055

- [ ] T056 [US4] Implement bounded sample, history, freshness, and warning serialization in `sandbox/resources/host_memory/models.py`
- [ ] T057 [US4] Implement aggregate-only monitor sampling and warning derivation inputs in `sandbox/resources/host_memory/provider.py`
- [ ] T058 [US4] Implement atomic bounded sample append, rotation, ordering, and corrupt-data isolation in `sandbox/resources/host_memory/repository.py`
- [ ] T059 [US4] Implement bounded history orchestration and register the strict authorized `host_memory.history` action in `sandbox/resources/host_memory/service.py` and `mcp/wp-server/server.py`
- [ ] T060 [US4] Add typed history transport and `resources swap-history` text/JSON presentation with enforced limits in `sandbox/core/_remote.py` and `sandbox/commands/resources.py`
- [ ] T061 [US4] Run the User Story 4 tests in `tests/test_host_memory_models.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Operators can inspect a privacy-bounded aggregate history without gaining a raw host telemetry channel.

---

## Phase 7: User Story 5 - Disable Only Fully Owned Configuration (Priority: P2)

**Goal**: Plan and disable the feature only when every affected artifact is still fully owned and the host remains within safety limits.

**Independent Test**: A fully owned synthetic configuration disables in journaled reverse order and removes only owned artifacts; altered, foreign, ambiguous, insufficient-headroom, or stale-plan fixtures refuse before mutation.

### Tests for User Story 5 - RED first

- [ ] T062 [P] [US5] Add failing disable-plan tests for ownership signatures, active-use checks, post-disable headroom, fixed artifact inventory, stale confirmation, and foreign-state refusal in `tests/test_host_memory_policy.py`
- [ ] T063 [P] [US5] Add failing disable-transaction tests for monitor stop, timer/service removal, swap deactivation, owned-file removal, reverse ordering, read-back verification, and foreign-artifact preservation in `tests/test_host_memory_provider.py`
- [ ] T064 [P] [US5] Add failing service, remote-contract, and CLI tests for separately confirmed disable planning/apply, authorization, replay, and stable refusal output in `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`
- [ ] T065 [P] [US5] Add failing repository tests for disable phase journaling, ownership snapshot binding, terminal convergence, and preservation of prior bounded history in `tests/test_host_memory_repository.py`
- [ ] T066 [US5] Run `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resources_cli.py`, and `tests/test_host_memory_repository.py`; confirm the User Story 5 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 5 - GREEN after T066

- [ ] T067 [US5] Implement owned-disable planning, post-disable headroom, active-use, confirmation, and fail-closed foreign-state rules in `sandbox/resources/host_memory/policy.py`
- [ ] T068 [US5] Implement journaled reverse-order disable and owned-only cleanup with read-back verification in `sandbox/resources/host_memory/provider.py`
- [ ] T069 [US5] Implement disable orchestration and strict authorized disable control dispatch in `sandbox/resources/host_memory/service.py` and `mcp/wp-server/server.py`
- [ ] T070 [US5] Add typed disable transport and confirmed CLI flow without arbitrary artifact selection in `sandbox/core/_remote.py` and `sandbox/commands/resources.py`
- [ ] T071 [US5] Run the User Story 5 tests in `tests/test_host_memory_policy.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_resources_cli.py`, and `tests/test_host_memory_repository.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Disable is bounded to fully owned state and cannot remove foreign or ambiguous host configuration.

---

## Phase 8: User Story 6 - Recover Safely from Interruptions and Replays (Priority: P2)

**Goal**: Reconcile every interrupted enable or disable phase from durable evidence, replay the same request safely, and roll back only proven owned partial state.

**Independent Test**: Fault injection at every phase boundary, duplicate request identity, lost response, malformed response, and partial rollback converges or returns a typed blocked state; no second operation identity launches and no unowned artifact changes.

### Tests for User Story 6 - RED first

- [ ] T072 [P] [US6] Add failing journal recovery tests for every enable/disable phase, duplicate request IDs, stale in-progress records, terminal replay, and conflicting operation identity in `tests/test_host_memory_repository.py`
- [ ] T073 [P] [US6] Add failing provider fault-injection tests for interruption before and after every side effect, read-back reconciliation, owned rollback, rollback failure, and ambiguous ownership in `tests/test_host_memory_provider.py`
- [ ] T074 [P] [US6] Add failing service tests for same-identity replay, ledger lookup before retry, reconcile-versus-rollback decisions, and typed `blocked` or `acceptance_unknown` states in `tests/test_host_memory_service.py`
- [ ] T075 [P] [US6] Add failing transport tests for timeout, empty output, malformed output, lost response, bounded ledger lookup, and prohibition on a second request identity in `tests/test_host_memory_remote.py`
- [ ] T076 [P] [US6] Add failing CLI tests for replay guidance, stable ambiguous-result exit classes, no automatic retry under a new identity, and bounded JSON errors in `tests/test_resources_cli.py`
- [ ] T077 [US6] Run `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`; confirm the User Story 6 assertions fail and record RED evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`

### Implementation for User Story 6 - GREEN after T077

- [ ] T078 [US6] Implement durable operation lookup, phase reconciliation, terminal replay, conflict detection, and bounded failure retention in `sandbox/resources/host_memory/repository.py`
- [ ] T079 [US6] Implement phase-aware read-back reconciliation and owned-only rollback with blocked-state preservation in `sandbox/resources/host_memory/provider.py`
- [ ] T080 [US6] Implement replay orchestration, ambiguous-acceptance handling, reconcile/rollback selection, and stable terminal results in `sandbox/resources/host_memory/service.py`
- [ ] T081 [US6] Implement bounded ledger lookup and same-identity replay behavior across `mcp/wp-server/server.py`, `sandbox/core/_remote.py`, and `sandbox/commands/resources.py`
- [ ] T082 [US6] Run the User Story 6 tests in `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, and `tests/test_resources_cli.py`; require GREEN and record bounded output in `specs/046-host-swap-monitor/acceptance-evidence.md`

**Checkpoint**: Interruptions and uncertain transport outcomes reconcile from durable evidence without duplicate mutation or unsafe cleanup.

---

## Phase 9: Documentation, Regression Proof, and External Acceptance Gates

**Purpose**: Synchronize operator guidance, prove adjacent boundaries, and retain non-local release gates as explicit unchecked work.

- [ ] T083 Document status, plan, apply, history, disable, ownership, fixed paths, privacy, replay, rollback, and Feature 047 read-only composition boundaries in `docs/resource-monitoring.md`
- [ ] T084 [P] Update the resource command overview, JSON examples, refusal semantics, and external acceptance caveats in `README.md`
- [ ] T085 [P] Update CLI-first operator guidance, confirmation rules, replay rules, and no-SSH-fallback constraints in `skills/sandbox-cli/SKILL.md`
- [ ] T086 [P] Add command registration, help, JSON schema, error-class, and documentation-link regression coverage in `tests/test_cli_help.py` and `tests/test_resources_cli.py`
- [ ] T087 [P] Add regression tests proving Spec 043 disk monitoring remains separate and workspace/remote contracts gain no direct state access in `tests/test_storage_monitor_runner.py`, `tests/test_workspace_contracts.py`, and `tests/test_remote.py`
- [ ] T088 Run all Feature 046 and adjacent regression tests in `tests/test_host_memory_models.py`, `tests/test_host_memory_policy.py`, `tests/test_host_memory_repository.py`, `tests/test_host_memory_provider.py`, `tests/test_host_memory_service.py`, `tests/test_host_memory_remote.py`, `tests/test_host_memory_interfaces.py`, `tests/test_resources_cli.py`, `tests/test_cli_help.py`, `tests/test_storage_monitor_runner.py`, `tests/test_workspace_contracts.py`, and `tests/test_remote.py`; record exact bounded results in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T089 Run the repository-supported full test and static-check gates, verify links and task/spec/source synchronization, and record exact commands plus bounded results in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T090 Obtain and record explicit human review of authentication, authorization, privileged fixed paths, ownership proof, rollback, privacy, cryptographic identities, dependency trust, and production-path risk in `specs/046-host-swap-monitor/acceptance-evidence.md`; do not treat local tests as this approval
- [ ] T091 After separate deployment authorization and T090 approval, update an approved disposable Linux host only through the supported Sandbox lifecycle, independently verify the installed revision matches the accepted SHA, and record redacted evidence in `specs/046-host-swap-monitor/acceptance-evidence.md`
- [ ] T092 After T091 and separate live-test authorization, execute the eligible, insufficient-space, existing-swap, foreign-artifact, interruption/replay, monitoring/history, and owned-disable live-Linux matrix and record aggregate redacted results in `specs/046-host-swap-monitor/acceptance-evidence.md`; local tests cannot satisfy this gate
- [ ] T093 Keep reboot persistence explicitly unverified in `specs/046-host-swap-monitor/acceptance-evidence.md` unless separately authorized; if authorized, verify status, monitoring, ownership, replay, and bounded history after reboot without changing the accepted revision
- [ ] T094 Reconcile local, human-review, live-Linux, and reboot evidence against `specs/046-host-swap-monitor/spec.md`, leaving every unperformed external gate open and making no release-readiness claim in `specs/046-host-swap-monitor/acceptance-evidence.md`

---

## Dependencies and Execution Order

### Phase dependencies

- **Setup (Phase 1)**: Starts immediately.
- **Foundational (Phase 2)**: Depends on Phase 1. Blocks every user story.
- **US1 (Phase 3)**: Depends on Phase 2. This is the read-only MVP and creates the only Feature 046 surface Feature 047 may consume.
- **US2 (Phase 4)**: Depends on US1 status evidence and Phase 2 planning foundations.
- **US3 (Phase 5)**: Depends on US2 contract shapes so the full refusal matrix can cover every mutation boundary.
- **US4 (Phase 6)**: Depends on US1 observation and Phase 2 repository foundations; it can proceed in parallel with US2/US3 once those shared interfaces are stable.
- **US5 (Phase 7)**: Depends on US2 ownership and enable transaction behavior plus US3 fail-closed rules.
- **US6 (Phase 8)**: Depends on US2 and US5 phase journals and transaction boundaries.
- **Documentation and gates (Phase 9)**: T083-T087 follow the implemented stories. T088-T089 follow all local implementation and docs. T090 is a distinct human gate. T091-T092 require separate authorization and must follow T090. T093 requires separate reboot authorization to perform reboot proof. T094 reports only evidence that actually exists.

### User-story dependency graph

```text
Setup -> Foundational -> US1 (MVP)
                          |-> US2 -> US3 -> US5 -> US6
                          |-> US4 ------------------|
                                                    v
                                             Docs/local gates
                                                    v
                                             Human review gate
                                                    v
                                      Authorized live-Linux gate
```

### Within each user story

1. Add all listed tests without production behavior.
2. Run the story RED task and confirm failures are caused by missing behavior.
3. Implement models/policy/repository/provider before service orchestration.
4. Implement the fixed remote control action before CLI presentation.
5. Run the story GREEN task and retain bounded evidence.
6. Do not move an external gate to complete based on local or mocked evidence.

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
After T021: T022 provider -> T023 service; T024 server and T025 controller transport can then be split before T026 CLI
```

### User Story 2

```text
T028 plan tests | T029 provider tests | T030 service tests | T031 remote tests | T032 CLI tests
After T033: T034 policy and T035 repository can proceed in parallel, then T036 provider -> T037 service -> T038/T039 remote -> T040 CLI
```

### User Story 3

```text
T042 policy matrix | T043 provider preflight | T044 remote boundary | T045 Feature 047 composition boundary
After T046: T047 policy -> T048 provider; T049 transport mapping follows the stable refusal types
```

### User Story 4

```text
T051 model tests | T052 provider tests | T053 repository tests | T054 interface tests
After T055: T056 models; then T057 provider and T058 repository in parallel -> T059 service/server -> T060 transport/CLI
```

### User Story 5

```text
T062 policy tests | T063 provider tests | T064 interface tests | T065 repository tests
After T066: T067 policy -> T068 provider; T069 service/server -> T070 transport/CLI
```

### User Story 6

```text
T072 repository recovery | T073 provider fault injection | T074 service replay | T075 transport uncertainty | T076 CLI ambiguity
After T077: T078 repository -> T079 provider -> T080 service -> T081 remote/CLI
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

1. Add US2 deterministic plan and authorized owned enable.
2. Complete US3 refusal coverage before treating mutation as safe.
3. Add US4 bounded history independently of host governance policy.
4. Add US5 owned-only disable.
5. Add US6 interruption and replay convergence.
6. Complete local docs and regressions, then retain human and live-Linux gates as separate authorization-bound work.

---

## Notes

- Every task is unchecked by design. Implementation evidence determines completion later.
- `[P]` means different files or isolated test surfaces with no unmet dependency.
- Story labels appear only on user-story work; setup, foundational, documentation, and acceptance-gate tasks remain shared.
- Exact fixed paths, sizes, action names, schemas, identities, ownership signatures, retention bounds, and warning thresholds must come from the approved Feature 046 design artifacts, not ad hoc implementation choices.
- Never expose secrets, inherited environment values, raw process details, raw command output, arbitrary host paths, or unbounded telemetry in tests, logs, contracts, evidence, or user output.
- Never deploy, access a live host, reboot, or mutate remote state while generating or analyzing these tasks.
