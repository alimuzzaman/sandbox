# Tasks: Managed server installation

Source implementation authorized. Runtime/cutover tasks require root authorization;
unchecked acceptance must not be described as passed. No broad test runs this session.

## Phase 1: Foundations

- [x] T001 Read constitution/router/planning skills and reserve089 with root.
- [x] T002 Generate/refine PRD and obtain independent readiness PASS.
- [x] T003 Generate specification, clarification, plan, research, data model and contracts through Spec Kit.
- [x] T004 Analyze cross-artifact consistency before implementation.

## Phase 2: US1 identity

- [ ] T005 Implement sandbox/installation/paths.py and private stable identity/policy storage in binding.py, explicit initialization only.
- [ ] T006 Register installation command and wire owner lifecycle initialization without moving existing owner layout.
- [ ] T007 Expose authenticated GET/hosting/installation and read-only validate_current, preserving actor authority and policy drift checks.
- [ ] T008 Add truthful installed capability extension port for085/086 integration; no claims from context/descriptors alone.
- [ ] T009 Add focused read-only, malformed/drift, stable retry and capability fixtures after implementation and supported workflow exercise.

## Phase 3: US2 managed lifecycle

- [ ] T010 Implement accepted path/release/principal plans with explicit collision checks, immutable release selection and generated managed system unit.
- [ ] T011 Implement supported install/update/remove adapter orchestration, failure fencing and preserved data; guard personal uninstall from managed deletion.
- [ ] T012 Document exact supported operations, policy/ownership, release evidence and current integration limits in docs/managed-installation.md and README.
- [ ] T013 Add focused lifecycle/release/collision/removal fixtures and short helper checks.

## Phase 4: US3 migration and recovery

- [ ] T014 Implement plan-bound migration journal and supported inventory/recovery/fencing/copy/verify/switch adapters with real prerequisites.
- [ ] T015 Preserve identities/receipts/ownership and retained source; refuse live binds/unverified backup or restore/active writers/conflicts.
- [ ] T016 Implement crash resume, pre-write rollback and post-write stale rollback refusal, with explicit forward/reverse reconciliation boundary.
- [ ] T017 Add focused fixture migration/recovery/crash tests and helper checks, no server mutation.

## Phase 5: Integration and controlled acceptance

- [ ] T018 Integrate root-approved current latest, recheck source contracts and short meaningful checks, independent personal Sol review.
- [ ] T019 Prepare exact revision-bound xspeed-dev cutover/rollback package with per-host approvals and protected resource inventory.
- [ ] T020 Root-authorized disposable/live install/migration/restore acceptance; retain full evidence and unresolved limits.

## Dependencies and strategy

T001-T004 precede managed implementation. US1 T005-T009 precedes086 binding
consumption and managed lifecycle. T010-T013 precede migration activation;
T014-T017 can develop against explicit adapters after identity contract exists.
T018-T019 are source handoff. T020 remains a separate authority gate and cannot
be checked from mocked tests. No delegated host/browser consumers. Parent owns
integration; bounded personal Sol helpers own short test/review tasks only.

Independent parallel opportunities: fixture-only review of US1 contract versus
read-only host metadata research; neither writes sibling-owned085/086 files.
