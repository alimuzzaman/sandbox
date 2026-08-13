# Tasks: Resource Monitoring and Safe Cleanup

**Input**: Design documents from `specs/035-resource-monitoring-cleanup/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/resources.md, quickstart.md

**Tests**: Required by the specification, constitution, and quickstart done gate.

**Organization**: Tasks are grouped by user story so monitoring, planning,
cleanup, and automation parity can be implemented and verified incrementally.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it owns different files and has no
  dependency on an incomplete task in the same phase.
- **[Story]**: Maps the task to a user story in `spec.md`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish feature-owned package and public composition slots.

- [X] T001 Create the resource package exports and module documentation in `sandbox/resources/__init__.py`
- [X] T002 [P] Add feature 035 test fixture builders for targets, observations, and fake providers in `tests/resource_fixtures.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create shared models, errors, persistence, and provider contracts
used by every user story.

**Critical**: No user story work begins until this phase passes focused tests.

- [X] T003 [P] Add failing validation and state-transition tests for scans, plans, candidates, and runs in `tests/test_resource_models.py`
- [X] T004 [P] Add failing atomic persistence, expiry, target-match, and replay tests in `tests/test_resource_plans.py`
- [X] T005 Implement validated immutable resource entities and result envelopes in `sandbox/resources/models.py`
- [X] T006 Implement atomic restrictive plan storage and state transitions in `sandbox/resources/plans.py`
- [X] T007 Define bounded local/remote provider protocols and safe provider result types in `sandbox/resources/adapters.py`
- [X] T008 Compose the shared resource service, registry/job ownership providers, plan store, and named-remote transport in `sandbox/resources/context.py`

**Checkpoint**: Models and persistence pass without Docker, SSH, or live
filesystem mutation.

---

## Phase 3: User Story 1 - Understand Host Storage (Priority: P1) MVP

**Goal**: Return read-only local capacity and ranked managed/unmanaged resource
observations through the CLI.

**Independent Test**: A local fixture with active managed data and an unmanaged
path reports capacity, ownership gaps, lifecycle states, and zero mutation.

### Tests for User Story 1

- [X] T009 [P] [US1] Add failing fast-scan classification, 15-second budget, capacity reconciliation, July-incident ranking, secret-corpus redaction, and no-mutation tests in `tests/test_resource_service.py`
- [X] T010 [P] [US1] Add failing CLI parser, human renderer, JSON envelope, target-default, and manifest ownership tests in `tests/test_resource_interfaces.py`

### Implementation for User Story 1

- [X] T011 [US1] Implement cheap local capacity, Sandbox-root, registry, runtime, cache, and host-category observations in `sandbox/resources/adapters.py`
- [X] T012 [US1] Implement fast-scan classification, summaries, deterministic ordering, confidence, and redaction in `sandbox/resources/service.py`
- [X] T013 [US1] Implement `resources status` parsing, JSON/human output, and nonzero failure handling in `sandbox/commands/resources.py`
- [X] T014 [US1] Register the feature-owned command module and command ownership bridge in `sandbox/commands/manifest.py`

**Checkpoint**: `sb resources status --json` works locally without an instance
and does not modify host state.

---

## Phase 4: User Story 2 - Perform Thorough Attribution (Priority: P1)

**Goal**: Add expensive per-category local and named-remote measurements with
overall budgets, partial results, progress, and drift detection.

**Independent Test**: Slow, unavailable, private-mount, and concurrent-change
fixtures finish within budget and explicitly report incomplete evidence.

### Tests for User Story 2

- [X] T015 [P] [US2] Add failing bounded directory/volume measurement, timeout, and partial-result adapter tests in `tests/test_resource_adapters.py`
- [X] T016 [P] [US2] Add failing named-remote identity, unreachable target, bounded category, and no-auto-retry tests in `tests/test_resource_remote.py`

### Implementation for User Story 2

- [X] T017 [US2] Implement isolated thorough filesystem and container-engine inventory providers with per-category deadlines in `sandbox/resources/adapters.py`
- [X] T018 [US2] Implement configured remote resolution and compact read-only SSH probes without remote deployment in `sandbox/resources/adapters.py`
- [X] T019 [US2] Implement overall scan budgeting, progress events, partial completeness, confidence reduction, and drift reporting in `sandbox/resources/service.py`
- [X] T020 [US2] Add `--thorough` and validated `--budget` behavior plus partial human rendering in `sandbox/commands/resources.py`

**Checkpoint**: Thorough local and fake-remote scans return complete or explicit
partial results within their selected budgets.

---

## Phase 5: User Story 3 - Review a Safe Cache Cleanup Plan (Priority: P2)

**Goal**: Produce a durable no-write plan containing only positively owned
unused disposable caches and explicit exclusions.

**Independent Test**: A fixture with managed cache, active containers, named
volumes, logs, retained artifacts, and unmanaged data plans only eligible cache
and leaves every resource unchanged.

### Tests for User Story 3

- [X] T021 [P] [US3] Add failing cache eligibility, named-volume exclusion, plan expiry, exact candidate, and zero-mutation tests in `tests/test_resource_service.py`
- [X] T022 [P] [US3] Add failing cache-plan CLI contract and plan-record permission tests in `tests/test_resource_interfaces.py`

### Implementation for User Story 3

- [X] T023 [US3] Implement cache ownership and eligibility providers for download cache, terminal job artifacts, and exact Sandbox-labelled unused engine resources in `sandbox/resources/adapters.py`
- [X] T024 [US3] Implement cache-plan construction, exclusions, evidence digests, target binding, 15-minute expiry, and atomic persistence in `sandbox/resources/service.py`
- [X] T025 [US3] Implement `resources plan --scope cache` JSON/human output in `sandbox/commands/resources.py`

**Checkpoint**: Cache planning is demonstrably read-only and named volumes are
never candidates.

---

## Phase 6: User Story 4 - Execute Confirmed Safe Cache Cleanup (Priority: P2)

**Goal**: Apply one reviewed cache plan with confirmation, target/expiry/replay
checks, per-item revalidation, exact deletion, and itemized outcomes.

**Independent Test**: Missing confirmation is refused, a candidate made active
after planning is skipped, eligible disposable cache is removed, and replay is
refused.

### Tests for User Story 4

- [X] T026 [P] [US4] Add failing confirmation, target mismatch, expiry, replay, became-active, partial-failure, and already-absent service tests in `tests/test_resource_service.py`
- [X] T027 [P] [US4] Add failing exact local/remote delete, remote timeout receipt, and no-broad-prune adapter tests in `tests/test_resource_adapters.py`

### Implementation for User Story 4

- [X] T028 [US4] Implement exact cache deletion adapters, immediate liveness revalidation, bounded outcomes, and remote idempotency receipts in `sandbox/resources/adapters.py`
- [X] T029 [US4] Implement confirmed apply orchestration, plan state transitions, indeterminate handling, capacity drift, and audit outcomes in `sandbox/resources/service.py`
- [X] T030 [US4] Implement `resources cleanup --plan-id ... --confirm` refusal and result rendering in `sandbox/commands/resources.py`

**Checkpoint**: Safe-cache cleanup protects all active and persistent fixtures
and produces one terminal outcome per determinate candidate.

---

## Phase 7: User Story 5 - Remove Proven Stale Managed Resources (Priority: P3)

**Goal**: Separately plan and confirm exact stale worktree or named-volume
removal under stronger ownership and non-use evidence.

**Independent Test**: Only positively owned, unmounted, unreferenced fixtures
enter the plan; ambiguous and permanent resources stay excluded and a newly
referenced candidate is skipped.

### Tests for User Story 5

- [X] T031 [P] [US5] Add failing managed-root, Compose-label, registry/job/backup/permanent-reference, and ambiguity classification tests in `tests/test_resource_service.py`
- [X] T032 [P] [US5] Add failing exact worktree/volume boundary and revalidation tests in `tests/test_resource_adapters.py`

### Implementation for User Story 5

- [X] T033 [US5] Implement positive ownership and protection evidence for deployment worktrees and named volumes in `sandbox/resources/adapters.py`
- [X] T034 [US5] Implement stale-scope planning and stronger candidate gates in `sandbox/resources/service.py`
- [X] T035 [US5] Implement exact boundary-checked stale worktree/volume deletion and became-referenced skips in `sandbox/resources/adapters.py`

**Checkpoint**: Dangling or old is never sufficient; only exact proven stale
managed resources can be removed.

---

## Phase 8: User Story 6 - Use Equivalent Automated Monitoring (Priority: P3)

**Goal**: Expose status, plan, and apply through an explicit MCP group with the
same service semantics as the CLI.

**Independent Test**: Equivalent CLI and MCP requests against one fake service
agree on target, classifications, candidates, exclusions, confirmation, and
terminal outcomes.

### Tests for User Story 6

- [X] T036 [P] [US6] Add failing CLI/MCP parity and MCP confirmation-before-provider tests in `tests/test_resource_interfaces.py`
- [X] T037 [P] [US6] Update exact MCP group/catalog and tool ownership expectations in `tests/test_mcp_composition.py` and `tests/test_mcp.py`

### Implementation for User Story 6

- [X] T038 [US6] Implement explicitly registered `resource_status`, `resource_cleanup_plan`, and `resource_cleanup_apply` adapters in `mcp/wp-server/tools/resources.py`
- [X] T039 [US6] Register the explicit resources tool group and scoped catalogs in `mcp/wp-server/tools/manifest.py`

**Checkpoint**: CLI and MCP parity tests pass and missing MCP confirmation
returns before any mutation provider is called.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Complete operator guidance, architecture checks, regression
coverage, and live done gates.

- [X] T040 [P] Document status, thorough scans, plans, cleanup scopes, safety exclusions, and examples in `docs/resource-monitoring.md`
- [X] T041 [P] Add resource command discovery and safe routing guidance in `README.md` and `skills/sandbox-cli/SKILL.md`
- [X] T042 Update public command/tool counts, explicit manifest expectations, architecture boundaries, and packaging assertions in `tests/test_cli.py`, `tests/test_command_composition.py`, and `tests/test_architecture_boundaries.py`
- [X] T043 Run focused unit/contract suites, fast-budget and secret-corpus acceptance checks, and repository self-tests from `specs/035-resource-monitoring-cleanup/quickstart.md`
- [X] T044 Run live `sb resources status`, read-only cache/stale planning, existing `sb status`, and disposable-fixture revalidation checks from `specs/035-resource-monitoring-cleanup/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks every user story.
- **US1 / Fast status (Phase 3)**: Depends on Foundational and is the MVP.
- **US2 / Thorough attribution (Phase 4)**: Depends on US1 scan/result behavior.
- **US3 / Cache planning (Phase 5)**: Depends on US1 and US2 observations.
- **US4 / Cache apply (Phase 6)**: Depends on US3 plans.
- **US5 / Stale resources (Phase 7)**: Depends on US2 observation and US4 apply
  safeguards, but is independently testable with its own scope.
- **US6 / Automation parity (Phase 8)**: Depends on the shared service behavior
  from US1-US5.
- **Polish (Phase 9)**: Depends on all selected stories.

### User Story Dependency Graph

```text
Foundation
   └─ US1 Fast status
       └─ US2 Thorough attribution
           ├─ US3 Cache plan ──> US4 Cache apply
           │                         └─ US5 Stale plan/apply
           └───────────────────────────────────┘
                                             └─ US6 MCP parity
```

### Parallel Opportunities

- T001 and T002 can run in parallel.
- T003 and T004 can run in parallel before T005-T008.
- Within each story, its two failing-test tasks can run in parallel.
- Documentation T040 and T041 can run in parallel after contracts stabilize.
- Separate writers must not edit the shared `adapters.py`, `service.py`, or
  `resources.py` command files concurrently.

## Parallel Examples

### User Story 1

```text
T009: Write service-level fast status tests in tests/test_resource_service.py
T010: Write CLI/manifest contract tests in tests/test_resource_interfaces.py
```

### User Story 2

```text
T015: Write local bounded-provider tests in tests/test_resource_adapters.py
T016: Write named-remote tests in tests/test_resource_remote.py
```

### User Story 6

```text
T036: Write CLI/MCP behavior parity tests in tests/test_resource_interfaces.py
T037: Update exact MCP manifest tests in tests/test_mcp_composition.py and tests/test_mcp.py
```

## Implementation Strategy

### MVP First

1. Complete T001-T008.
2. Complete T009-T014.
3. Run the US1 independent test and live local status.
4. Continue only after read-only monitoring is proven.

### Incremental Delivery

1. Add thorough partial attribution (US2).
2. Add no-write cache planning (US3).
3. Add confirmed exact cache apply (US4).
4. Add stronger stale persistent-resource scope (US5).
5. Add MCP parity (US6).
6. Complete docs, regression checks, and live done gates.

### Safety Stop Conditions

- Stop if implementation would require reading registry JSON directly.
- Stop if exact ownership cannot be proven without a broad prune operation.
- Stop if unrelated concurrent edits overlap a required file.
- Stop after two failed attempts using the same provider/transport approach.
- Never retry an indeterminate remote cleanup automatically.

## Notes

- Every task follows the required checkbox, ID, optional parallel marker,
  story label, and exact-path format.
- Tests precede implementation within each story.
- Existing `sb cache` remains compatible.
- No task authorizes deployment, release, or cleanup of permanent resources.

## Phase 10: Convergence — 2026-08-13 (27-feedback network lifecycle)

These tasks are intentionally open and do not change the completion state of
T001-T044.

- [ ] T045 [US1/US5] Add the canonical network lifecycle model regression for
  `a813480b`, covering owner identity, active references, allocation/release,
  reconciliation, and one authoritative state across status, plan, and apply.
- [ ] T046 [US3/US5] Add repeated create/stop/destroy/recreate fixture coverage
  for `bf05eeb9` proving idempotence, no orphan/duplicate growth, and release
  only after leases, containers, and jobs are inactive.
- [x] T047 [US3/US5] Add active/foreign/unattributed network protection cases for
  `0fac3b07`; each must remain an explicit exclusion before and after a plan
  revalidation and must not be deleted by an exact cleanup apply.
- [ ] T048 [US1/US2] Add constrained-pool collision/exhaustion and recovery
  coverage for `822b9323`; assert stable capacity errors, bounded retries, and
  no automatic deletion or disk-capacity misclassification.
- [X] T049 [US2] Add remote timeout/stale-control observation coverage for
  `78aaf583`; assert structured partial/unavailable evidence, no traceback or
  false success, and a required fresh rescan before planning.
- [x] T050 [US1/US6] Add the `6bc4c6d5` consumer regression proving resource
  monitoring uses the Spec 032 top-level job-list decoder and rejects a nested
  `.data` response without changing network state.

## Phase 11: Convergence — workspace index ownership projection (2026-08-13)

These tasks add typed consumption of the workspace index and do not authorize cleanup,
reset, destroy, network release, or broad prune. Completion marks reflect only the
implementation and evidence actually present in this branch.

- [X] T051 [US1/US2] Define the typed workspace resource binding/projection keyed by
  opaque `workspace_id` and `project_identity`, including lifecycle, alias/evidence
  digests, active references, index generation, completeness, and bounded errors.
- [X] T052 [US1/US2] Add complete/missing/unresolved/conflict/invalid/duplicate/stale
  workspace-index fixtures; prove unknown/indeterminate classifications, zero
  reclaimable bytes, and `workspace_index_incomplete`/`workspace_ownership_drift` errors.
- [X] T053 [US1/US5] Route local/remote resource providers through the typed projection
  and shared Spec 032 top-level job-list decoder; add boundary tests proving no direct
  SQLite or legacy `workspace.json` consumer exists.
- [X] T054 [US1/US3/US5] Add network/resource lifecycle tests proving a moved checkout or
  metadata locator does not release an active network and active/foreign/unknown aliases
  remain exclusions across rescan and plan/apply.
- [X] T055 [US6] Add CLI/MCP parity tests for checkout-independent workspace identity,
  incomplete index, alias collision, generation drift, remote timeout, and fresh-rescan
  requirements before planning.
- [ ] T056 [US1/US2] Record read-only before/after evidence for metadata migration and
  base relocation showing unchanged network/container/job/volume/upload/snapshot counts
  and no cleanup mutation.
- [X] T057 [US1/US2/US6] Update resource status/plan quickstart and operator guidance to
  keep unresolved ownership visible and forbid guessing from labels, paths, or age.
