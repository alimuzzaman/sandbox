# Feature Specification: Remote Storage Reservation

**Feature Branch**: `latest`

**Created**: 2026-10-13

**Status**: Draft

**Input**: `specs/066-remote-storage-reservation/prd.md` (READY FOR SPECKIT, GPT-6.1-Sol round-3 PASS)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Submissions cannot eat the production reserve (Priority: P1)

A developer submits a remote test or job run to a remote that also serves
hosted production. If admitting it would take projected free space below the
remote's storage floor, it is refused before anything is uploaded, with the
numbers that explain why.

**Why this priority**: This is the direct stop for the outage path of
2026-10-10/12; it protects production before any cleanup improvement exists.

**Independent Test**: Configure a floor on a remote, simulate free space and
in-flight reservations, submit, and observe accept/refuse with no remote
change on refusal.

**Acceptance Scenarios**:

1. **Given** 80 GB free, floor 30 GB, estimate 3 GB, no in-flight work, **When** a run is submitted, **Then** it is admitted and its 3 GB estimate is reserved until the run ends.
2. **Given** 32 GB free, floor 30 GB, estimate 3 GB, **When** a run is submitted, **Then** it is refused with `remote_storage_floor` showing free, reserved, floor and estimate, and nothing is transferred or created remotely.
3. **Given** 35 GB free, floor 30 GB, one admitted 3 GB run in flight, **When** a second 3 GB run is submitted, **Then** it is refused (35 - 3 - 3 < 30).
4. **Given** free space cannot be measured, **When** a run is submitted, **Then** it is refused with `remote_storage_floor` stating the measurement failed.
5. **Given** no previous workspaces of that repository on the remote, **When** a run is submitted, **Then** the default estimate is used and the result says the estimate is a default.
6. **Given** free minus reserved minus estimate equals the floor exactly, **When** a run is submitted, **Then** it is admitted (refusal only when strictly below).
7. **Given** a remote with no floor configured and no default applying, **When** a run is submitted, **Then** behaviour is unchanged from today.

---

### User Story 2 - Finished workspaces are removed completely (Priority: P1)

An operator (or the opt-in scheduled routine) reaps finished remote test
workspaces; each eligible workspace loses its service stack, its exclusively
owned volumes and networks, and then its source, with every deletion recorded
first.

**Why this priority**: Retained stacks and sources were the volume that filled
the disk; admission alone only delays the next outage.

**Independent Test**: Seed workspaces in various job states and ages, run reap,
and compare survivors and the deletion record against expectations.

**Acceptance Scenarios**:

1. **Given** a workspace whose last job finished 8 h ago with a stale job marker and a running database stack, **When** reap runs, **Then** containers, exclusive volumes, exclusive networks and then source are removed and each appears in the deletion record.
2. **Given** a workspace with a running job, **When** reap runs at any idle age, **Then** it is untouched.
3. **Given** a workspace with a queued job, or whose job state cannot be determined, **When** reap runs, **Then** it is kept and reported with the reason.
4. **Given** a workspace idle 5 h with a 6 h threshold, **When** reap runs, **Then** it is kept as not yet eligible.
5. **Given** a volume used by the workspace and by another project, **When** reap runs, **Then** that volume is kept and reported; other exclusive resources are removed.
6. **Given** the deletion record cannot be written, **When** reap runs, **Then** nothing is deleted and the run reports the failure.
7. **Given** stack teardown fails or is interrupted, **When** reap ends, **Then** the source is kept, the run is reported partial, and a later reap completes it without error.
8. **Given** a submission for a workspace arrives while reap is selecting or tearing it down, **When** both proceed, **Then** they never act on the same workspace at once: either the submission waits/refuses or reap skips the workspace.
9. **Given** the scheduled routine is disabled, **When** time passes, **Then** nothing is reaped unattended; **Given** it is enabled, **Then** an eligible idle workspace is removed on its next run.

---

### User Story 3 - Hosted production is never selected by cleanup (Priority: P1)

**Why this priority**: The feature exists to protect production; a cleanup
that could touch it is worse than none.

**Independent Test**: Run reap and job cleanup on a remote with hosted
projects whose names resemble workspaces; confirm zero hosted resources are
selected.

**Acceptance Scenarios**:

1. **Given** a remote hosting `sandbox-host-lenzora-production`, **When** any reap or job cleanup runs, **Then** no container, volume or network of a hosted project is selected, in dry run or real run.

---

### User Story 4 - Safe recovery of exhausted hosted services (Priority: P2)

An operator restores hosted services that stopped after exhausting their
restart allowance, once space is back.

**Why this priority**: Recovery was manual for ~20 h; valuable but only after
prevention.

**Independent Test**: Simulate exited-after-restart-limit services with free
space above, equal to, and below the floor; run preview and confirmed heal.

**Acceptance Scenarios**:

1. **Given** two hosted services exited after their restart limit and free space strictly above the floor, **When** heal runs without confirmation, **Then** it lists exactly those services and restarts nothing.
2. **Given** the same state, **When** heal runs with confirmation, **Then** it re-checks space and eligibility, restarts only the listed services, leaves all others untouched, and reports each service's resulting state.
3. **Given** free space equal to or below the floor, or unmeasurable, **When** heal runs with confirmation, **Then** it refuses with free and floor and restarts nothing.
4. **Given** free space dropped below the floor between preview and confirmation, **When** confirmation runs, **Then** it refuses.
5. **Given** a hosted service that stopped normally (not restart-exhausted), **When** heal runs, **Then** it is not listed or restarted.
6. **Given** heal runs, **Then** it never recreates, pulls, rebuilds or deletes anything.

---

### User Story 5 - Phase B isolation plan (Priority: P3)

The user receives a reviewed plan comparing hard-isolation options; nothing is
executed.

**Independent Test**: The plan exists and every step names downtime, rollback
and its approval point.

**Acceptance Scenarios**:

1. **Given** Phase A is live, **When** the plan is delivered, **Then** it compares (a) a fixed ~100 GB preallocated filesystem for test workspace source and test volumes, (b) a capped build cache, (c) moving production data to its own reserved storage, each with downtime, rollback and approval point, and no system change has been made.

### Edge Cases

- Floor configured larger than the disk: every submission refused; status shows the misconfiguration.
- Estimate history contains a huge outlier: estimate uses recent same-repository workspaces; the outlier raises the estimate (conservative), never lowers it.
- In-flight reservation for a run that crashed without reporting completion: the reservation is released when the run is known ended (job terminal state), not held forever.
- Remote runtime not yet migrated to a version that knows the floor: the controller reports the remote lacks the capability rather than skipping the check silently.
- Reap of a workspace whose source is already gone but stack remains: the stack is still removed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each remote MUST support a configurable storage floor in GB; xcloud-london MUST have 30 GB; the effective floor MUST be shown in remote readiness/resources reporting.
- **FR-002**: Every remote test or job submission MUST be checked against the floor before any transfer, using a fresh free-space measurement of the filesystem holding container data and workspaces.
- **FR-003**: Admission MUST refuse when free - reserved - estimate < floor, with error code `remote_storage_floor` and the four numbers in the refusal.
- **FR-004**: Admission MUST refuse with `remote_storage_floor` when free space cannot be measured.
- **FR-005**: The estimate MUST derive from recent workspaces of the same repository on that remote, falling back to a stated conservative default with no history.
- **FR-006**: Each admitted run's estimate MUST be reserved until the run reaches a terminal state.
- **FR-007**: Refusal MUST leave no remote state change (no upload, workspace, stack or reservation).
- **FR-008**: A workspace MUST be reap-eligible only when it is idle beyond the threshold (default 6 h, configurable) AND there is affirmative evidence of no running or queued job; unknown or conflicting evidence MUST keep it.
- **FR-009**: Reap and job cleanup MUST remove an eligible workspace's containers, then its exclusively owned volumes and networks, then its source.
- **FR-010**: Resources shared with another project MUST be kept and reported.
- **FR-011**: Every deletion MUST be recorded before it happens; if the record cannot be written, nothing is deleted.
- **FR-012**: If stack teardown fails or is interrupted, source MUST be kept, the result reported as partial, and a later reap MUST complete it idempotently.
- **FR-013**: Admission and reap MUST NOT act on the same workspace concurrently.
- **FR-014**: Hosted-project resources MUST never be selected by reap or job cleanup.
- **FR-015**: Unattended idle reap MUST run only via the existing opt-in scheduled routine; its activation and locking rules are unchanged.
- **FR-016**: Heal MUST preview by default and act only on explicit confirmation.
- **FR-017**: Heal MUST act only on hosted services exited after exhausting their restart allowance, and only when current free space is strictly above the floor, re-checked at confirmation.
- **FR-018**: Heal MUST NOT recreate, pull, rebuild or delete anything, and MUST report each service's resulting state.
- **FR-019**: Phase B MUST be delivered as a plan document only; any production or container-daemon change requires explicit user approval and a maintenance window.
- **FR-020**: No part of this feature may change, move or prune lenzora production containers or volumes, except heal restarting listed services on explicit confirmation.

### Key Entities

- **Storage floor**: per-remote reserved free space (GB).
- **Footprint estimate**: expected disk use of one submission, with its source (history or default).
- **Reservation**: an admitted run's estimate, held until the run is terminal.
- **Workspace**: remote test workspace (source + service stack + owned volumes/networks), with job state and idle age.
- **Deletion record**: append-only list of resources to delete, written before deletion.
- **Heal candidate**: hosted service exited after exhausting restarts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of submissions whose projected free space is below the floor are refused before any byte is transferred.
- **SC-002**: Replaying the 2026-10-12 workspace population (~45 workspaces), admission refuses every submission that would have taken projected free space below 30 GB.
- **SC-003**: After a successfully completed reap, zero exclusively owned containers, volumes, networks or source remain for reaped workspaces.
- **SC-004**: Zero workspaces with running, queued or unknown job state are removed across all tested idle ages.
- **SC-005**: Zero hosted-project resources appear in any reap or cleanup selection.
- **SC-006**: Heal preview restarts 0 services; confirmed heal above the floor restarts exactly the listed services; at/below/unknown restarts 0.
- **SC-007**: A refused submission tells the developer why in one message containing free, reserved, floor and estimate.
- **SC-008**: Phase B plan covers all three options with downtime, rollback and approval point per step.

## Assumptions

- Hosted projects and test workspaces carry distinguishable project identities on the remote; exclusive ownership is checked per resource.
- The remote has a single filesystem holding container data and workspaces (xcloud-london); multiple filesystems are measured at the one holding workspaces.
- Changes take effect remotely only after the remote runtime is migrated; a protocol bump follows existing compatibility rules.
- The existing readiness gate is the admission point; its tolerance of `unknown` rows does not apply to the storage-floor check (feature decision).
- An admitted run that outgrows its estimate is not stopped; hard limits are Phase B.
- Disk usage alerts are delivered by spec 065.
