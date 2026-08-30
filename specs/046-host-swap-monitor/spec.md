# Feature Specification: Remote Host Swap and Memory Monitor Commands

**Feature Branch**: `codex/feature-046-specification`

**Created**: 2026-08-30

**Status**: Draft

**Input**: Product requirements draft from `prd.md`: "Make the verified remote swap lifecycle and aggregate memory monitoring reusable Sandbox CLI commands."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inspect host memory safety without mutation (Priority: P1)

As a Sandbox operator, I want one supported status view for a registered remote so I can
see whether swap and aggregate memory monitoring are usable, persistent, healthy, or
uncertain before I consider changing the host.

**Why this priority**: Truthful read-only evidence is the safety gate for every later
action. It provides immediate incident value without granting mutation authority.

**Independent Test**: Request status for healthy, unmanaged, stale, partially observed,
unsupported, and unreachable registered remotes. Confirm that each response reports the
known aggregate state, labels unknown evidence, and changes no remote state.

**Acceptance Scenarios**:

1. **Given** a reachable registered Linux remote with active Sandbox-owned swap and fresh
   monitoring, **When** an operator requests status, **Then** the response reports aggregate
   RAM, swap capacity and use, persistent configuration, configured preference, monitor
   state, log freshness, next sample, and ownership evidence without mutation.
2. **Given** a remote with active or persistent swap that Sandbox does not own, **When** an
   operator requests status, **Then** the response identifies the unmanaged state without
   adopting it or claiming that Sandbox can change it.
3. **Given** incomplete, stale, malformed, unavailable, or contradictory evidence, **When**
   status is requested, **Then** the response identifies each unknown or partial field and
   does not infer a healthy, persistent, or owned state.
4. **Given** host swap is available but container swap eligibility is unavailable, limited,
   or unknown, **When** status is requested, **Then** the response keeps those observations
   separate and does not claim that an individual container can use host swap.

---

### User Story 2 - Review and enable a bounded emergency buffer (Priority: P1)

As a Sandbox operator, I want to review the exact eligibility checks and intended changes,
then explicitly confirm the same plan, so I can add a bounded emergency swap buffer and
aggregate monitor without using raw host commands.

**Why this priority**: Enabling host swap and persistent monitoring is the primary reusable
outcome, but it must remain protected by a current, reviewable plan.

**Independent Test**: Plan the default enable on an eligible remote, verify that planning
changes nothing, confirm the exact plan, and verify the active, persistent, preference,
monitor, sample, receipt, and replay results. Repeat with every size and reserve boundary.

**Acceptance Scenarios**:

1. **Given** an eligible remote with no active or persistent swap, **When** the operator
   requests the default enable plan, **Then** the plan shows the 4 GiB buffer, global host
   preference of 15, five-minute sampling, retention bounds, ownership target, capacity
   checks, intended changes, rollback scope, and plan identity without changing the host.
2. **Given** a current enable plan, **When** apply is requested without explicit confirmation
   or with a different, expired, or drifted plan identity, **Then** the operation is refused
   and changes zero host state.
3. **Given** a current eligible plan and explicit confirmation of that exact plan, **When**
   enable completes, **Then** the buffer is active and persistent, the preference is verified,
   monitoring is active, a fresh aggregate sample exists, a secret-free ownership receipt is
   recorded, and the next sample is reported.
4. **Given** the confirmed intent is already fully current, **When** the same replay-safe
   intent is submitted again, **Then** the result is `already_current` and no duplicate swap,
   configuration, monitor, receipt, or schedule is created.
5. **Given** a requested size below 1 GiB, above 8 GiB, above 50% of physical RAM, above 10%
   of filesystem capacity, or leaving less than the larger of 10 GiB or 15% filesystem free,
   **When** planning or apply evaluates the request, **Then** it refuses before mutation and
   reports the failed bound with the observed and permitted values.

---

### User Story 3 - Refuse unsafe or unowned changes (Priority: P1)

As a Sandbox operator, I want protected operations to fail closed when the platform,
capacity, ownership, revision, or current state is unsafe, so Sandbox never takes over or
overwrites host state it cannot prove it owns.

**Why this priority**: A convenient lifecycle command is unacceptable if ambiguity can turn
it into an unrestricted privileged host-writing surface.

**Independent Test**: Attempt enable and disable with an unsupported platform, unregistered
target, insufficient capacity, conflicting swap, unsafe existing file, ownership drift,
concurrent change, revision mismatch, and incomplete evidence. Confirm typed refusal and
zero unrelated mutation in every case.

**Acceptance Scenarios**:

1. **Given** a non-Linux host, unregistered target, unsupported required facility, or remote
   revision mismatch, **When** any protected operation is requested, **Then** it returns an
   actionable typed refusal and never falls back to direct host access.
2. **Given** any active or persistent swap without a matching Sandbox ownership receipt,
   **When** enable or disable is requested, **Then** the operation refuses without adopting,
   overwriting, deactivating, or changing policy around the unmanaged state.
3. **Given** a conflicting or unsafe existing file, ambiguous ownership, insufficient disk
   or RAM, incomplete required evidence, or a host state that changed after planning,
   **When** apply starts, **Then** it changes nothing and reports the specific refusal and a
   safe recovery hint.
4. **Given** another lifecycle operation is active or an incomplete rollback blocks
   unrelated mutation, **When** a different mutation is requested, **Then** it is refused
   while read-only status and bounded log reads remain available.

---

### User Story 4 - Read bounded aggregate pressure history (Priority: P2)

As a developer or incident responder, I want a bounded recent history of aggregate RAM,
swap, and memory pressure so I can distinguish an emergency buffer holding cold pages from
sustained memory pressure without exposing process or secret data.

**Why this priority**: History explains whether swap use is transient or operationally
important, while strict aggregation keeps diagnostics safe to retain and share.

**Independent Test**: Read valid, empty, stale, malformed, and oversized history windows.
Confirm that every response is bounded, declares its completeness and range, and contains
only allowed aggregate fields.

**Acceptance Scenarios**:

1. **Given** retained valid samples, **When** a bounded recent window is requested, **Then**
   the response contains only timestamps and aggregate RAM, swap, and memory-pressure
   fields, plus requested range, observed range, completeness, freshness, and truncation
   evidence.
2. **Given** samples are missing, stale, malformed, outside the requested range, or exceed
   the output bound, **When** history is requested, **Then** the response labels those
   conditions and never fills gaps or reports a complete window from incomplete evidence.
3. **Given** swap use remains at or above 512 MiB for three consecutive samples, **When**
   monitor health or history is read, **Then** it reports a sustained-use warning and reports
   memory pressure separately rather than labeling swap use alone as active thrashing.
4. **Given** retained monitor data and structured or human output, **When** it is inspected,
   **Then** it contains no process names, identities, command lines, arguments, environment
   values, arbitrary private paths, or secret-like fields.

---

### User Story 5 - Disable only a proven owned configuration (Priority: P2)

As a Sandbox operator, I want to review and safely remove only Sandbox-owned swap and
monitoring state, so cleanup cannot create memory exhaustion or damage unrelated host
configuration.

**Why this priority**: Safe removal completes the lifecycle and is required for rollback,
but it depends on the ownership and observation paths established by higher-priority stories.

**Independent Test**: Plan and confirm removal on a fully owned healthy setup, then repeat
with inadequate RAM headroom, drifted owned state, partial ownership, and unmanaged swap.
Verify exact cleanup or refusal without collateral changes.

**Acceptance Scenarios**:

1. **Given** a fully proven Sandbox-owned configuration, **When** the operator requests a
   disable plan, **Then** the plan lists the exact owned state to remove, current swap use,
   required RAM headroom, drift checks, rollback scope, and plan identity without mutation.
2. **Given** available RAM does not exceed current swap use plus the larger of 1 GiB or 10%
   of physical RAM, **When** disable is planned or applied, **Then** the operation refuses and
   leaves the working setup intact.
3. **Given** a current disable plan, sufficient headroom, unchanged fully owned state, and
   explicit confirmation, **When** disable completes, **Then** only the owned active swap,
   persistence, preference, monitor, retention policy, and ownership evidence are reconciled;
   future sampling stops, while prior bounded aggregate history and a minimal disabled-state
   ownership receipt remain read-only for recovery, and the result verifies that final state.
4. **Given** ownership or current state cannot be proven completely, **When** disable is
   requested, **Then** it refuses rather than removing a subset or touching unmanaged state.

---

### User Story 6 - Recover truthfully from an interrupted operation (Priority: P2)

As an automation client or operator, I want interrupted operations to preserve a stable
identity and exact partial-state evidence, so I can safely reconcile the same intent without
guessing whether the first attempt succeeded.

**Why this priority**: Privileged remote operations can lose transport or verification at
any point. Replay and rollback truth prevent duplicate or contradictory mutations.

**Independent Test**: Interrupt each protected lifecycle phase and make verification fail
after partial progress. Confirm that the same intent either resumes safely or proves prior
state restoration, while unrelated intents remain blocked after an unproven rollback.

**Acceptance Scenarios**:

1. **Given** an operation is interrupted after partial progress, **When** status or the same
   replay-safe plan identity is requested, **Then** the response reports the exact observed
   partial state and never converts empty, malformed, or ambiguous output into success.
2. **Given** the same current intent can safely continue, **When** it is replayed, **Then** it
   reconciles idempotently from the observed state and reports one stable final outcome.
3. **Given** apply cannot complete and every prior-state element is verified restored,
   **When** rollback verification ends, **Then** the result is `rollback_complete` with the
   verified restoration evidence.
4. **Given** any prior-state element cannot be verified restored, **When** rollback
   verification ends, **Then** the result remains `rollback_incomplete`, identifies the
   unresolved state, and blocks unrelated mutation until reconciliation.

### Edge Cases

- The remote becomes unreachable between planning and apply, or transport output is empty,
  malformed, duplicated, late, or ambiguous: apply does not claim success and retains the
  replay-safe identity for read-only reconciliation.
- Physical RAM, filesystem size, free capacity, swap use, ownership, persistence,
  preference, monitor state, or remote revision changes after planning: the protected
  operation refuses before relying on stale observations.
- A size is exactly 1 GiB or 8 GiB, exactly 50% of RAM, exactly 10% of filesystem capacity,
  or leaves exactly the required free reserve: equality is accepted only where every stated
  bound is met; exceeding any maximum or falling below the reserve is refused.
- Available RAM is exactly swap use plus the required disable reserve: disable refuses
  because available RAM must exceed, not merely equal, the threshold.
- Monitoring has never sampled, the newest sample is exactly two intervals plus one minute
  old, or the system clock moves: health reports freshness from bounded observed timestamps
  and does not infer missing samples.
- Swap use crosses 512 MiB for only one or two samples, or falls below it before the third:
  no sustained-use warning is emitted for that sequence.
- Rotation encounters more than eight weekly history files or more than 32 MiB total:
  oldest history is removed before either retained bound is exceeded; read output remains
  bounded even if malformed foreign files are present.
- A monitor run cannot collect a complete aggregate sample: it records a partial or failed
  outcome without process-level fallback and does not fabricate counters.
- A reboot has not been separately authorized and observed: persistent configuration may
  be reported, but reboot persistence remains unverified and is never claimed.
- Multiple swap areas or a swap partition exist: status observes them, while first-version
  enable and disable refuse because only one Sandbox-owned swap file is supported.

## Requirements *(mandatory)*

### Functional Requirements

**Command and authority boundaries**

- **FR-001**: The system MUST provide supported remote-host operations for swap and memory
  monitor status, read-only enable and disable planning, explicitly confirmed enable and
  disable application, and bounded recent aggregate history reads within the existing
  resource-operations command family.
- **FR-002**: All operations MUST target an explicitly registered remote Linux host and MUST
  use the existing registered-remote authority, authenticated control transport, remote
  revision evidence, and protected-operation confirmation model.
- **FR-003**: Status, planning, and bounded history reads MUST be read-only and MUST NOT
  require confirmation; enable and disable MUST be protected operations requiring explicit
  confirmation of a current matching plan.
- **FR-004**: The system MUST NOT reboot a remote, provide arbitrary host command or file
  access, manage container memory limits, manage swap inside containers, or automatically
  enable or resize swap because a pressure threshold is crossed.
- **FR-005**: A platform, facility, registration, transport, or remote revision mismatch
  MUST return an actionable typed result and MUST NOT fall back to raw or direct host access.

**Status and evidence**

- **FR-006**: Status MUST report aggregate RAM totals and availability; every observed swap
  area's type, capacity, and use; active and persistent state; global host preference;
  Sandbox ownership; monitoring state; latest sample age; configured interval; next sample;
  retention state; and separately authorized reboot-verification state.
- **FR-007**: Status MUST distinguish known, unknown, stale, malformed, unsupported,
  unmanaged, partial, and drifted evidence and MUST NOT derive success, persistence,
  ownership, or health from missing or ambiguous observations.
- **FR-008**: Status MUST observe unmanaged active or persistent swap without adopting it,
  and every protected lifecycle operation MUST refuse while any such swap lacks a matching
  Sandbox ownership receipt.
- **FR-009**: Host swap availability and per-container swap eligibility MUST be reported as
  separate observations; absent cgroup evidence MUST remain unknown and outside mutation
  authority.
- **FR-010**: Persistent configuration MUST NOT be represented as reboot-verified unless a
  separately authorized reboot acceptance run has observed the expected post-reboot state.

**Enable planning and eligibility**

- **FR-011**: An enable plan MUST identify the remote, requested and effective policy,
  current observations, every eligibility calculation, intended owned changes, monitor and
  retention settings, rollback scope, expiry or freshness bound, and stable plan identity.
- **FR-012**: The default enable policy MUST be one 4 GiB Sandbox-owned swap file, global
  host `vm.swappiness` 15, aggregate sampling every five minutes, and retention of the
  current history plus no more than eight weekly historical files and 32 MiB total.
- **FR-013**: An explicit size override MUST be from 1 through 8 GiB inclusive, MUST NOT
  exceed 50% of physical RAM or 10% of the relevant filesystem capacity, and MUST preserve
  at least the larger of 10 GiB or 15% of filesystem capacity as free space.
- **FR-014**: Every selected size, observed capacity, computed threshold, reserve, and
  pass/refusal result MUST appear in the plan so the operator can independently review
  eligibility.
- **FR-015**: Planning and apply MUST refuse insufficient disk or RAM, an unsafe or
  conflicting existing path, ambiguous ownership, unmanaged swap, concurrent lifecycle
  mutation, incomplete required evidence, or any state that cannot be restored or
  reconciled within the declared ownership boundary.

**Protected enable and idempotency**

- **FR-016**: Enable application MUST require explicit confirmation bound to the exact
  current plan identity and MUST refuse missing, mismatched, expired, replay-incompatible,
  or drifted confirmation before mutation.
- **FR-017**: Immediately before each consequential phase, enable MUST revalidate the plan's
  target, revision, capacities, ownership, current state, and safety observations; any
  mismatch MUST stop further mutation and enter verified reconciliation.
- **FR-018**: A successful enable MUST verify active and persistent swap capacity, global
  host preference, active monitoring, a fresh aggregate sample, retention limits, next
  scheduled sample, and a secret-free owner-safe receipt before reporting `applied`.
- **FR-019**: Repeating the same confirmed intent against fully matching effective state
  MUST return `already_current` without creating or duplicating state.
- **FR-020**: Sandbox MUST own only the single swap file and associated configuration it
  created and can prove with its receipt; it MUST NOT adopt, overwrite, rename, remove, or
  change policy around unowned or ambiguously owned swap state.

**Aggregate monitoring and retention**

- **FR-021**: Each monitor sample MUST contain a bounded timestamp and aggregate RAM, swap,
  and memory-pressure counters only; samples and all derived output MUST omit process names
  and identities, command lines, arguments, environment values, arbitrary private paths,
  and secret-like fields.
- **FR-022**: The configured sampling interval MUST default to five minutes, and a latest
  sample MUST be considered fresh through two configured intervals plus one minute.
- **FR-023**: Monitor health MUST report missing, stale, malformed, partial, failed, and
  truncated sampling evidence without filling gaps or substituting process-level data.
- **FR-024**: A sustained swap-use warning MUST require at least 512 MiB used in three
  consecutive valid samples; memory-pressure evidence MUST be reported separately so swap
  use alone is not called active thrashing.
- **FR-025**: Monitor execution under normal supported host conditions MUST complete within
  five seconds or report a bounded timeout or failed sample without overlapping the next
  run.
- **FR-026**: Retained history MUST include at most the current file plus eight weekly
  historical files and MUST total no more than 32 MiB; rotation MUST remove oldest owned
  history before either retained bound is exceeded and MUST NOT remove unowned files.
- **FR-027**: Bounded history reads MUST report requested and observed ranges, sample count,
  freshness, completeness, malformed or missing samples, and truncation, in both human and
  structured results.

**Disable safety and ownership**

- **FR-028**: A disable plan MUST identify the exact Sandbox-owned active, persistent,
  preference, monitoring, retention, history, and receipt state proposed for reconciliation,
  plus current swap use, RAM availability, safety calculation, drift observations, rollback
  scope, stable plan identity, and the required preservation of prior bounded aggregate
  history plus a minimal disabled-state ownership receipt.
- **FR-029**: Disable MUST refuse unless available RAM is strictly greater than current swap
  use plus the larger of 1 GiB or 10% of physical RAM.
- **FR-030**: Disable MUST require explicit confirmation of a current matching plan and MUST
  revalidate RAM headroom, ownership, revision, persistence, preference, monitoring, and
  concurrent state immediately before consequential phases.
- **FR-031**: A successful disable MUST reconcile only fully proven Sandbox-owned state and
  MUST verify the intended final active swap, persistence, preference, monitoring,
  retention, history, and receipt state before reporting `applied`. It MUST stop future
  sampling but MUST preserve previously retained bounded aggregate history and a minimal
  disabled-state ownership receipt for read-only recovery; first-version disable MUST NOT
  delete that history.
- **FR-032**: If safe removal, complete ownership, or final-state verification cannot be
  proven, disable MUST refuse or enter reconciliation and MUST leave a working owned setup
  intact whenever no protected phase has begun.

**Replay, rollback, and outcomes**

- **FR-033**: Every protected operation MUST use a stable replay-safe intent and plan
  identity, record bounded phase and observation evidence, and reconcile the same identity
  rather than create a second operation when delivery or output is uncertain.
- **FR-034**: Empty, malformed, duplicated, late, contradictory, or unavailable operation
  output MUST be treated as an unknown or partial result, never as success or safe replay
  authorization.
- **FR-035**: If an apply cannot finish, the system MUST either safely continue the same
  intent or restore and verify every relevant prior-state element before reporting
  `rollback_complete`.
- **FR-036**: If any prior-state element cannot be verified restored, the result MUST remain
  `rollback_incomplete`, MUST preserve the unresolved evidence, and MUST block unrelated
  mutation while allowing read-only status and history inspection.
- **FR-037**: Human and structured results MUST distinguish at least `planned`, `applied`,
  `already_current`, `refused`, `partial`, `failed`, `rollback_complete`, and
  `rollback_incomplete`, and MUST include stable reasons and safe recovery guidance where
  action is needed.

**Compatibility and release evidence**

- **FR-038**: Existing resource status, storage monitoring, cleanup, workspace, job,
  container-limit, and remote lifecycle behavior outside these new operations MUST retain
  its documented semantics.
- **FR-039**: Public command and structured-result changes MUST remain compatible with the
  repository's explicit command and protocol registration contracts and MUST carry matching
  operator documentation, tests, and remote revision evidence before release.
- **FR-040**: Release acceptance MUST include an authorized disposable or explicitly
  approved supported Linux remote and MUST demonstrate status, eligible enable, immediate
  replay, bounded history, sustained-use warning behavior, partial-operation reconciliation,
  disable, cleanup, privacy, and rollback outcomes. The complete refusal matrix MUST also be
  demonstrated: cases safe to create on that live target run there, while cases that would
  require an unsupported target, service skew, unsafe host damage, or unapproved capacity
  pressure use the fixed authenticated transport/provider acceptance harness. The evidence
  ledger MUST label synthetic, human-review, live-Linux, and reboot proof separately.

### Key Entities

- **Remote Swap State**: The observed active and persistent swap areas, capacity, use,
  preference, ownership classification, revision evidence, and separately observed
  container eligibility for one registered remote.
- **Swap Lifecycle Plan**: A time-bounded, immutable review record for enable or disable,
  including target identity, requested policy, current observations, eligibility and
  headroom calculations, intended owned changes, rollback scope, and plan identity.
- **Protected Swap Operation**: A replay-safe application of one confirmed plan, with phase,
  observation, mutation, verification, rollback, and terminal outcome evidence.
- **Ownership Receipt**: Secret-free proof that binds the Sandbox-created swap file,
  persistence, preference, monitoring, retention, and relevant effective state to one
  registered remote and lifecycle identity.
- **Aggregate Memory Sample**: One timestamped bounded observation of host-wide RAM, swap,
  and memory-pressure counters with completeness and validity state.
- **Monitor Health**: The configured cadence, most recent sample, freshness, next expected
  sample, sustained-use warning state, and any missing, malformed, partial, failed, or
  truncated evidence.
- **History Window**: A bounded read result describing the requested and observed time
  ranges, samples returned, completeness, freshness, and truncation.
- **Rollback Evidence**: The prior-state observations, restoration actions, verification
  results, unresolved elements, and mutation-blocking state for an incomplete operation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a healthy registered supported remote, an operator obtains complete swap
  and monitor status in one supported command with zero host mutation and no direct host
  access in 100% of acceptance runs.
- **SC-002**: Unconfirmed, mismatched-plan, expired-plan, drifted-plan, unsupported-platform,
  unmanaged-swap, unsafe-path, insufficient-capacity, concurrent-operation, and revision-
  mismatch cases change zero host state and return the correct typed refusal in 100% of the
  acceptance matrix.
- **SC-003**: Every valid size boundary and override is shown in the plan; 100% of requests
  outside the 1–8 GiB, 50%-of-RAM, 10%-of-filesystem, or free-reserve bounds are refused
  before mutation, while every otherwise eligible boundary value is evaluated consistently.
- **SC-004**: A confirmed default enable reaches verified active and persistent 4 GiB swap,
  host preference 15, active five-minute monitoring, a fresh aggregate sample, bounded
  retention, and an owner-safe receipt; immediate replay returns `already_current` with zero
  duplicate state in 100% of authorized live acceptance runs.
- **SC-005**: Every supported history read returns only allowed aggregate fields and states
  its range, completeness, freshness, malformed-sample state, and truncation; privacy probes
  find zero process identities, command arguments, environment values, arbitrary private
  paths, or secret-like fields in retained, human, and structured outputs.
- **SC-006**: Under normal supported host conditions, 100% of monitor samples finish within
  five seconds; sample freshness changes only after two configured intervals plus one minute,
  and sustained-use warnings appear only after three consecutive valid samples at or above
  512 MiB swap use.
- **SC-007**: Rotation retains no more than the current file plus eight weekly historical
  files and no more than 32 MiB total in 100% of retention-boundary acceptance runs.
- **SC-008**: Disable is refused whenever available RAM is less than or equal to swap use
  plus the larger of 1 GiB or 10% of RAM; every successful disable removes only proven
  Sandbox-owned active configuration, stops future sampling, preserves prior bounded
  aggregate history plus the disabled-state ownership receipt, and verifies that intended
  final state.
- **SC-009**: Every interrupted protected-operation acceptance ends with one truthful stable
  outcome: the same intent reaches verified state, every prior-state element is verified
  restored as `rollback_complete`, or unresolved elements remain explicit as
  `rollback_incomplete` and unrelated mutation stays blocked.
- **SC-010**: Status observes unmanaged swap in 100% of coverage cases without adoption and
  never represents host swap availability as proof that any individual container can use it.
- **SC-011**: A first-time operator can use the documented status, plan, confirmation,
  history, and recovery flow to identify whether the remote is current, refused, partial,
  failed, or needs reconciliation without reconstructing privileged host commands.

## Assumptions

- The target is an explicitly registered remote Linux host reached through the existing
  authenticated control path; local-host mutation and non-Linux support remain out of scope.
- The target exposes maintained host-wide swap, memory-pressure, service, scheduling, and
  log facilities needed for complete evidence. Missing facilities yield unsupported or
  partial results rather than guessed success.
- One Sandbox-owned swap file is the only swap form created or removed in the first version;
  arbitrary paths, swap partitions, multiple owned files, and autonomous sizing are future
  scope.
- First-version disable preserves already retained bounded aggregate history and a minimal
  disabled-state ownership receipt. Deleting that recovery evidence requires a separately
  specified ownership-safe cleanup operation and is out of scope.
- The default 4 GiB buffer is an emergency buffer for the observed class of host, not a
  universal RAM-sizing recommendation or a substitute for workload limits and right-sizing.
- The existing protected-operation confirmation, replay identity, revision evidence,
  ownership receipt, and remote registration models remain authoritative dependencies.
- The remote host monitor is separate from controller-side storage monitoring while both
  appear in the existing resource-operations command family.
- Live host behavior and reboot persistence require separately authorized acceptance.
  Local contract tests cannot prove host mutation safety or post-reboot state.

## Scope Boundaries and Dependencies

- **In scope**: Read-only remote swap/monitor status, bounded enable and disable planning,
  confirmed ownership-scoped lifecycle changes, aggregate sampling, bounded history,
  retention, disabled-state history preservation, typed outcomes, replay, rollback evidence,
  and operator documentation.
- **Out of scope**: Threshold-triggered swap activation, autonomous sizing, RAM
  right-sizing, process or cgroup killing, container swap management, container-limit
  changes, arbitrary swap partitions or paths, host reboot, raw host command/file access,
  non-Linux targets, and mandatory third-party monitoring or swap-management services.
- **Compatibility**: Existing resource, storage-pressure, cleanup, workspace, job,
  container-limit, remote authority, and remote revision behavior remains authoritative
  outside the explicitly added operations.
- **Release dependency**: Consequential host behavior requires human review and an
  authorized disposable or explicitly approved supported remote for live acceptance before
  release; specification and local contract evidence alone are not release proof.
