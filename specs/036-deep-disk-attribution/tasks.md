# Tasks: Deep Disk Attribution

**Input**: Design documents from `specs/036-deep-disk-attribution/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md,
contracts/deep-attribution.md, quickstart.md

**Tests**: Required by the feature specification, constitution, and live done
gate. Tests precede implementation within each story.

## Phase 1: Setup

**Purpose**: Establish feature-owned normalized attribution types and fixtures.

- [x] T001 Create deep-attribution model and parser module in `sandbox/resources/attribution.py`
- [x] T002 [P] Add deep-attribution fixture builders in `tests/resource_fixtures.py`

---

## Phase 2: Foundational

**Purpose**: Define validated evidence and additive scan transport shared by
local, remote, service, CLI, and MCP surfaces.

- [x] T003 [P] Add failing validation, redaction, ranking, and reconciliation tests in `tests/test_resource_attribution.py`
- [x] T004 Add validated filesystem, capability, coverage, finding, and reconciliation entities in `sandbox/resources/attribution.py`
- [x] T005 Add optional deep-attribution transport to provider snapshots and storage scans in `sandbox/resources/adapters.py` and `sandbox/resources/models.py`
- [x] T006 Add deep request orchestration, mode, completeness, and additive response serialization in `sandbox/resources/service.py`

**Checkpoint**: Pure models and service fixtures reconcile without running host
commands.

---

## Phase 3: User Story 1 - Reconcile an Unexplained Capacity Gap (Priority: P1)

**Goal**: Inventory filesystem boundaries, deeply measure selected roots, rank
allocated consumers, and return a conservative capacity reconciliation.

**Independent Test**: A deterministic multi-filesystem fixture ranks known
allocation, marks every mount's coverage, excludes nested mounts, and returns a
non-negative residual without mutation.

- [x] T007 [P] [US1] Add failing mount inventory, selection, gdu parsing, du fallback, hard-link, timeout, and zero-mutation tests in `tests/test_resource_attribution.py`
- [x] T008 [P] [US1] Add failing local deep-provider budget and partial-category tests in `tests/test_resource_adapters.py`
- [x] T009 [US1] Implement bounded installed-gdu and standard-du directory collectors plus mount selection in `sandbox/resources/attribution.py`
- [x] T010 [US1] Integrate local deep collectors with capacity snapshots and category outcomes in `sandbox/resources/adapters.py`
- [x] T011 [US1] Implement capacity-accounted observed allocation, overlap exclusion, residual, overage, and drift in `sandbox/resources/service.py`

**Checkpoint**: Local deep status closes known fixture gaps and names every
incomplete boundary.

---

## Phase 4: User Story 2 - Identify Deleted-Open Storage (Priority: P2)

**Goal**: Detect and safely aggregate deleted-open regular files without
exposing paths or process arguments.

**Independent Test**: A known deleted-open fixture is counted once by
filesystem/process, while unavailable privilege returns partial evidence.

- [x] T012 [P] [US2] Add failing lsof field parsing, file-identity deduplication, privilege fallback, platform limitation, and secret-corpus tests in `tests/test_resource_attribution.py`
- [x] T013 [US2] Implement bounded deleted-open capability selection, parsing, aggregation, and redaction in `sandbox/resources/attribution.py`
- [x] T014 [US2] Integrate deleted-open findings and coverage with local capacity reconciliation in `sandbox/resources/adapters.py`

**Checkpoint**: Deleted-open bytes reduce the residual only when distinct,
measured, and not directory-visible.

---

## Phase 5: User Story 3 - Understand Container Storage Overlap (Priority: P3)

**Goal**: Add detailed structured container diagnostics without inflating
capacity-accounted bytes.

**Independent Test**: Shared and unique image fixtures remain diagnostic and
engine-root directory allocation remains the only capacity-accounted Docker
root.

- [x] T015 [P] [US3] Add failing structured Docker detailed-accounting, byte-size, activity, overlap, and unavailable-engine tests in `tests/test_resource_attribution.py`
- [x] T016 [US3] Implement bounded structured Docker detailed parser and diagnostic findings in `sandbox/resources/attribution.py`
- [x] T017 [US3] Integrate Docker root discovery and detailed diagnostics in `sandbox/resources/adapters.py`

**Checkpoint**: Docker diagnostics explain shared/unique/logical values while
reconciliation remains physically conservative.

---

## Phase 6: User Story 4 - Receive Honest Partial Results (Priority: P4)

**Goal**: Provide equivalent bounded named-remote deep collection and stable
CLI/MCP partial semantics.

**Independent Test**: Fake remote tool absence, permission failure, timeout,
delivered partial payload, and total transport loss preserve only received
evidence and return equivalent CLI/MCP fields.

- [x] T018 [P] [US4] Add failing remote deep request, compact payload, timeout, delivered-partial, total-transport-loss, and partial-evidence tests in `tests/test_resource_remote.py`
- [x] T019 [P] [US4] Add failing `--deep` validation, human/JSON rendering, and CLI/MCP parity tests in `tests/test_resource_interfaces.py`
- [x] T020 [US4] Implement self-contained bounded remote mount, directory, deleted-open, and Docker deep collectors in `sandbox/resources/remote.py`
- [x] T021 [US4] Implement `--deep` status-only CLI parsing and deep human rendering in `sandbox/commands/resources.py`
- [x] T022 [US4] Add the additive `deep` MCP status argument in `mcp/wp-server/tools/resources.py`

**Checkpoint**: Local and named-remote deep status share the same validated
contract and never hide incomplete evidence.

---

## Phase 7: User Story 5 - Review Safe Cleanup Guidance (Priority: P5)

**Goal**: Classify findings into existing managed scopes, manual remediation,
monitoring-only evidence, or non-cleanable overhead without creating deletion.

**Independent Test**: Mixed managed, unmanaged, deleted-open, overhead, active,
permanent, job, and backup fixtures reference only existing eligible cleanup
scopes.

- [x] T023 [P] [US5] Add failing cleanup-guidance boundary and no-new-deletion-path tests in `tests/test_resource_attribution.py` and `tests/test_resource_interfaces.py`
- [x] T024 [US5] Implement conservative guidance classification from existing resource evidence in `sandbox/resources/attribution.py`
- [x] T025 [US5] Verify plan and cleanup behavior remain unchanged in `tests/test_resource_service.py`

**Checkpoint**: Deep findings are informative only; existing cleanup policy
remains the sole mutation authority.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T026 [P] Document deep scans, installed-tool fallback, reconciliation, and remediation boundaries in `docs/resource-monitoring.md`
- [x] T027 [P] Add deep command discovery and remote routing guidance in `README.md` and `skills/sandbox-cli/SKILL.md`
- [x] T028 Verify additive contract, architecture-boundary, manifest, and packaging assertions in `tests/test_cli.py`, `tests/test_architecture_boundaries.py`, and `tests/test_mcp.py`
- [x] T029 Run focused resource suites and deterministic acceptance checks from `specs/036-deep-disk-attribution/quickstart.md`
- [x] T030 Run the full repository test suite and live read-only local and named-remote deep status checks from `specs/036-deep-disk-attribution/quickstart.md`

---

## Dependencies & Execution Order

- Phase 1 precedes Phase 2.
- Foundation blocks every user story.
- US1 provides mount and directory attribution.
- US2 and US3 depend on US1 reconciliation but are independently testable.
- US4 depends on US1-US3 normalized evidence.
- US5 depends on normalized findings and existing feature-035 cleanup evidence.
- Polish follows all stories.

```text
Foundation
  └─ US1 filesystem attribution
      ├─ US2 deleted-open
      └─ US3 Docker diagnostics
          └─ US4 remote and interfaces
              └─ US5 cleanup guidance
                  └─ Polish
```

## Parallel Opportunities

- T001 and T002 touch different files.
- T003 can run before T004-T006.
- Within US1, T007 and T008 are separate test files.
- US2 and US3 failing tests can be prepared independently after US1 models.
- T018 and T019 cover separate remote and interface contracts.
- T026 and T027 own separate documentation files.

## Implementation Strategy

1. Land pure validated evidence and reconciliation first.
2. Add local mount/directory attribution as the P1 MVP.
3. Add deleted-open and Docker diagnostics without changing cleanup.
4. Mirror the collectors through the bounded named-remote probe.
5. Add interfaces, guidance, docs, regression, and live proof.

## Safety Stop Conditions

- Stop if implementation would install a package or executable during status.
- Stop if directory measurement would cross a discovered filesystem boundary.
- Stop if raw paths, process arguments, environment values, or mount options
  would enter public output.
- Stop if an overlapping diagnostic would contribute to accounted bytes.
- Stop if deep evidence would create a cleanup candidate or broaden an existing
  deletion scope.
- Stop after two failed attempts using the same host collector approach.

## Phase 9: Convergence

- [x] T031 CRITICAL Capture retained local and named-remote live deep-scan evidence, elapsed delivery time, and before/after zero-mutation baselines per Constitution IV and plan live-stack gate (partial)
- [x] T032 CRITICAL Reconcile capacity and attributed bytes within the same filesystem scope, including APFS/firmlink mapping and all selected filesystems without double counting per FR-005, FR-026, FR-028, and US1/AC1 (contradicts)
- [x] T033 Collect truthful mount type, flags, writability, and parent relationships and explicitly exclude nested mounts during scans per FR-005, FR-006, FR-009, US1/AC2, and the nested-mount edge case (partial)
- [x] T034 Pass typed known-managed registry and job roots into local and remote deep selection and scan each distinct filesystem per FR-007 (partial)
- [x] T035 Fall back from a failed or incompatible installed preferred scanner to standard `du` while budget remains per FR-014 and US4/AC1 (partial)
- [x] T036 Measure deleted-open allocated blocks, map device identity to its filesystem, and aggregate/deduplicate by filesystem and process per FR-017 through FR-019 and US2/AC1 through US2/AC2 (contradicts)
- [x] T037 Detect insufficient deleted-open privilege and surface partial coverage with explicit reasons instead of claiming completeness per FR-025 and US2/AC3 (contradicts)
- [x] T038 Model Docker unique, shared, active, inactive, and potentially reclaimable diagnostics independently while keeping overlap diagnostic-only per FR-020 and US3/AC1 through US3/AC2 (partial)
- [x] T039 Retain valid partial remote payloads on timeout or interruption, distinguish total transport loss, and enforce delivery within budget plus five seconds per FR-003, FR-004, US4/AC3 through US4/AC5, and SC-006 (contradicts)
- [x] T040 Propagate cancellation through CLI, MCP, service, and collectors and emit explicit cancelled or disconnected completion while preserving completed evidence per FR-003, FR-023, and the Deep Attribution Request entity (missing)
- [x] T041 Extend coverage with errors, exclusions, and limitations and isolate category failures so completed evidence survives unexpected provider or parser failures per FR-022, FR-039, and FR-040 (partial)
- [x] T042 Render target, filesystems, capacity, full coverage, reconciliation, drift, limitations, and per-filesystem rankings with semantic human/structured parity per FR-005, FR-010, FR-033, and SC-011 (partial)
- [x] T043 Measure and report local and remote capacity plus attributed-byte drift using the 1% or 64 MiB materiality rule per FR-038 and SC-009 (partial)
- [x] T044 Match cleanup guidance to exact normalized resource identity and locator evidence while preserving every existing protection predicate per FR-030, FR-032, and US5/AC1 (partial)
- [x] T045 Add deterministic hard-link, multi-filesystem, nested-mount, permission-partial, delivered-partial timeout, repeatability, semantic-parity, isolation, zero-mutation, and live acceptance evidence per SC-001 through SC-011, SC-013, SC-014, and T029 (missing)
