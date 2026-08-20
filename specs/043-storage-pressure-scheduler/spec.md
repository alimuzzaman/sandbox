# Feature Specification: Scheduled storage-pressure monitor and safe-tier reaper

**Feature Branch**: `043-storage-pressure-scheduler`

**Created**: 2026-08-16

**Status**: Draft

**Input**: User description: "Scheduled storage-pressure monitor and safe-tier reaper for a configured remote: a confirmation-gated systemd timer/service plan that runs `sb resources status` + `sb workspace reap` on a schedule, warns at 15% free and 5% free with concrete numbers, optionally auto-runs the safe reclaim tier only (off by default, opt-in per remote via config), surfaces the warning in CLI status, `sb doctor`, and job/status output, and closes the MCP tier-parity gap so resource_cleanup_plan/apply accept a tier."

## Context

Feature 042 delivered measurement (`sb resources status`), tiered reclamation
(`sb resources plan|cleanup --tier safe|tmp|all`), retention (`sb workspace
release|ttl|reap`), a durable deletion manifest, and pure-policy capacity-pressure
classification with an automatic-tier gate. None of it runs unattended: the pressure
classification is computed only when a human asks for status, the automatic gate has no
caller, and nothing warns anywhere else. The incident that motivated 042 — a remote host
reaching 97% full before anyone noticed — is therefore still possible. This feature is the
missing periodic caller, its configuration surface, its visibility, and the interface
parity that lets a non-CLI caller reach the tiers.

## Clarifications

### Session 2026-08-16

- Q: Where do the monitoring settings live? → A: machine-wide defaults alongside the other
  machine defaults, overridden per target alongside the other per-target settings in the
  per-machine store. Nothing goes in a project repository.
- Q: What cadence does the rendered schedule use by default? → A: hourly, with a randomized
  delay, so an activated monitor notices pressure within an hour rather than a day.
- Q: How does the health check learn a remote target's capacity without paying for a
  round trip on every invocation? → A: it reads the durable record written by the last
  monitor run; if there is no record, or it is older than a stated staleness bound, it says
  so instead of implying health. The health check never contacts a remote target by itself.
- Q: What does a run record contain and how long is it kept? → A: the last run per target is
  kept as a single durable owner-only record containing the target, timestamp, level,
  capacity numbers, thresholds used, and what the automatic path did or refused to do.
- Q: Does the scheduled check ever run against the operator's local machine? → A: yes, the
  same command with no target named checks the local machine, with the same defaults and the
  same off-by-default automatic path.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The operator is warned before the host fills (Priority: P1)

An operator wants to learn that a managed host is running out of storage from the tools
they already run, before a deploy fails. Whenever they ask a Sandbox command about that
host, a host that is under storage pressure says so, in the same breath, with the actual
numbers: how much is free, what share of the disk that is, which threshold was crossed,
and the single next command to run.

**Why this priority**: Without visibility nothing else in this feature matters; a schedule
that computes a warning nobody sees is the same failure as no schedule at all. This story
alone (warning surfaced on demand across the existing commands) already removes the
"nobody noticed" failure mode for an operator who runs anything at all against the host.

**Independent Test**: Point a health command and the storage status command at a host whose
free share is below the warning threshold and confirm both report the level, the free
bytes, the free percentage, the threshold crossed, and a next action. Confirm a healthy
host reports normally and adds no warning noise.

**Acceptance Scenarios**:

1. **Given** a target whose free space is above the warning threshold, **When** the operator
   runs the health check or the storage status command, **Then** the output contains no
   capacity warning and the exit status is unchanged.
2. **Given** a target whose free space is at or below the warning threshold but above the
   critical threshold, **When** the operator runs the health check, **Then** the output
   contains a warning that names the free bytes, the free percentage, the total size, the
   threshold that was crossed, and the specific next command, and the health check reports
   a warning rather than a failure.
3. **Given** a target whose free space is at or below the critical threshold, **When** the
   operator runs the health check, **Then** the output escalates to a critical warning with
   the same numbers and the critical next action.
4. **Given** a target whose capacity could not be measured, **When** the operator runs the
   health check, **Then** the output states that capacity is unmeasured and how to measure
   it, and never implies the host is healthy.

---

### User Story 2 - A schedule watches the host without being installed by surprise (Priority: P1)

An operator wants the check to happen on a timer instead of depending on someone running a
command. They ask Sandbox for the schedule; Sandbox renders exactly what it would install —
the units, the commands, the cadence, the target — and installs nothing. Activating the
schedule is a separate, explicitly confirmed step, and any activation is refused unless the
operator confirms it.

**Why this priority**: Unattended execution against a shared host is the highest-risk part
of this feature. It must be impossible to acquire by accident, and the operator must be
able to read the exact unattended command before it can ever run.

**Independent Test**: Ask for the schedule for a named target and confirm the output is a
plan marked not-enabled, containing the rendered units and both scheduled commands, with no
file written and no timer registered. Then ask to activate without confirmation and confirm
it is refused with a protected-operation reason.

**Acceptance Scenarios**:

1. **Given** any configured target, **When** the operator asks for the storage schedule,
   **Then** the response is a plan that shows the cadence, the monitored target, both
   scheduled commands, and an explicit "not enabled" state, and nothing is installed.
2. **Given** a rendered schedule plan, **When** activation is requested without explicit
   confirmation, **Then** it is refused with a protected-operation reason and no unit file,
   timer, or scheduled entry is created.
3. **Given** a rendered schedule plan, **When** activation is requested with explicit
   confirmation, **Then** the operator is told exactly what was written, where, and how to
   deactivate it.
4. **Given** an activated schedule, **When** the scheduled run starts while a previous run
   is still going, **Then** the new run is skipped rather than overlapping.

---

### User Story 3 - The scheduled run reports pressure and never deletes by default (Priority: P1)

When the schedule fires, it measures the target's capacity, classifies the pressure, records
the result durably, and — by default — stops there. It deletes nothing. Automatic
reclamation is a separate opt-in per target, and even when enabled it can only ever run the
safest tier.

**Why this priority**: The default must be observation. An operator who accepts a monitor
must not thereby accept a deleter.

**Independent Test**: Run the scheduled action against a target with automatic reclamation
unconfigured and confirm the outcome records the pressure level and performs no deletion.
Then request the scheduled action with a non-safe automatic tier configured and confirm it
is refused rather than downgraded.

**Acceptance Scenarios**:

1. **Given** a target with automatic reclamation not enabled, **When** the scheduled run
   fires at any pressure level, **Then** it records the capacity numbers and the pressure
   level, deletes nothing, and reports what a human would have to run to reclaim.
2. **Given** a target with automatic reclamation enabled for the safe tier, **When** the
   scheduled run fires and free space is at or below the automatic threshold, **Then** the
   safe tier is executed, the run is recorded in the deletion manifest with an automatic
   trigger, and the reclaimed amount is reported.
3. **Given** a target with automatic reclamation enabled for the safe tier, **When** the
   scheduled run fires and free space is above the automatic threshold, **Then** nothing is
   deleted and the run records why it did not act.
4. **Given** a target configured with an automatic tier other than the safe tier, **When**
   the scheduled run fires or the configuration is read, **Then** the configuration is
   rejected with a specific reason and no reclamation runs at any tier.
5. **Given** a target whose automatic threshold is configured above the warning threshold,
   **When** the configuration is read, **Then** it is rejected, because the automatic path
   must never be more eager than the warning.

---

### User Story 4 - Expired workspaces are reaped on the same schedule (Priority: P2)

The retention reaper from the previous feature also runs on this schedule, so workspaces
that nobody released still stop accumulating. It honours the existing default retention
window, an explicit per-workspace extension, and an explicit release, and it is subject to
the same off-by-default rule: a scheduled reap that would delete requires the same per-target
opt-in as automatic reclamation.

**Why this priority**: Retention is what stops the accumulation recurring, but it is second
to being told about the problem at all, and it is the part most likely to surprise someone
who left a workspace idle over a weekend.

**Independent Test**: With retention opt-in disabled, run the scheduled action and confirm
the reap is reported as a dry run listing what would be reclaimed. With it enabled, confirm
an expired, unleased, not-in-use workspace is reclaimed while a leased one and a
recently-active one are skipped with stated reasons.

**Acceptance Scenarios**:

1. **Given** the scheduled action with reaping not opted in, **When** it runs, **Then** the
   reap is performed as a dry run and reports the candidates it would have reclaimed.
2. **Given** the scheduled action with reaping opted in, **When** a workspace has been idle
   beyond the retention window with no lease and no active work, **Then** it is reclaimed and
   recorded in the manifest.
3. **Given** a workspace with an unexpired retention extension or an explicit hold, **When**
   the scheduled reap runs, **Then** it is skipped with the lease as the stated reason.
4. **Given** a workspace that was explicitly released, **When** the scheduled reap runs,
   **Then** it is reclaimed regardless of the default window.

---

### User Story 5 - A non-CLI caller can reach the tiers (Priority: P2)

An agent driving Sandbox through its tool interface can plan and apply a tiered reclamation
exactly as the command line can, with the same confirmation requirement and the same refusal
of unsafe combinations. Today that caller can only reach the older scope-based plans, so it
has to shell out to do the thing the tool interface claims to expose.

**Why this priority**: It is a parity gap, not a new capability; it matters because the
scheduled and agent-driven paths otherwise diverge from the human path.

**Independent Test**: Ask the tool interface for a tiered plan and confirm it returns the
same tiered plan shape as the command line; ask it to apply without confirmation and confirm
refusal; ask for both a tier and a scope in one call and confirm refusal.

**Acceptance Scenarios**:

1. **Given** the tool interface, **When** a tiered plan is requested for a valid tier,
   **Then** the response matches the command line's tiered plan for the same target.
2. **Given** the tool interface, **When** a tiered apply is requested without confirmation,
   **Then** it is refused with the same reason the command line gives.
3. **Given** the tool interface, **When** a call supplies both a tier and a scope, or an
   unknown tier, **Then** it is refused with a specific reason and nothing is planned or
   applied.

### Edge Cases

- Capacity cannot be measured (probe unavailable, target unreachable): the run records an
  unmeasured outcome, warns that it could not answer, and never triggers the automatic path.
- The measurement itself is incomplete (partial inventory): the warning still reports the
  capacity numbers, and the automatic path may still run the safe tier, because the safe
  tier's own guards are independent of inventory completeness — but the run records that the
  inventory was partial.
- Two scheduled runs overlap, or a human run overlaps a scheduled run: the second run is
  skipped rather than executing concurrently against the same target.
- The configuration names a target that is not registered: reading the configuration fails
  with the unknown target named; it does not fall back to the local machine.
- A target is configured with both an automatic tier and a threshold, but the disk is full
  enough that even the manifest cannot be written: the run refuses to delete and reports the
  refusal, as the existing manifest rule requires.
- The schedule is activated twice: the second activation is idempotent and reports that the
  schedule already exists rather than creating a duplicate.

## Requirements *(mandatory)*

### Functional Requirements

**Configuration**

- **FR-001**: The system MUST provide a per-target storage-monitoring configuration with a
  warning threshold, a critical threshold, an automatic-reclamation switch, an automatic
  tier, an automatic threshold, and a retention/reap switch.
- **FR-002**: Configuration MUST resolve as machine defaults overridden by per-target
  settings, and MUST be readable without contacting the target.
- **FR-003**: Defaults MUST be: warning at 15% free, critical at 5% free, automatic
  reclamation off, automatic tier `safe`, automatic threshold equal to the critical
  threshold, and scheduled reaping off.
- **FR-004**: The configuration MUST reject an automatic tier other than the safe tier, an
  automatic threshold greater than the warning threshold, a critical threshold greater than
  the warning threshold, and any threshold outside the range 0 to 1 exclusive.
- **FR-005**: Configuration MUST register through the existing explicit configuration
  manifest rather than being read ad hoc by the consuming code.
- **FR-006**: An unknown or unregistered target named in configuration MUST be reported by
  name and MUST NOT silently resolve to another target.

**Thresholds and classification**

- **FR-007**: Free space at or below the warning threshold and above the critical threshold
  MUST classify as `warning`; at or below the critical threshold MUST classify as
  `critical`; above the warning threshold MUST classify as `normal`; unmeasurable capacity
  MUST classify as `unknown`.
- **FR-008**: Classification MUST use the configured thresholds for the target rather than
  fixed constants.
- **FR-009**: The automatic path MUST be eligible only when the switch is on AND free space
  is at or below the automatic threshold AND the tier is the safe tier.

**Schedule**

- **FR-010**: The system MUST render a schedule plan for a named target showing the cadence,
  the target, the exact commands that would run unattended, and an explicit not-enabled
  state, without writing anything.
- **FR-011**: The schedule MUST include both the storage check and the retention reap for
  the same target.
- **FR-012**: Activating a schedule MUST be refused unless the caller explicitly confirms,
  and the refusal MUST identify it as a protected operation.
- **FR-013**: Activation MUST report every path it wrote and the exact command that
  deactivates it.
- **FR-014**: Scheduled runs MUST NOT overlap; a run that starts while another is in
  progress MUST be skipped with a stated reason.
- **FR-015**: The unattended command MUST be bounded by a finite timeout and MUST be exactly
  the command shown in the plan.
- **FR-016**: Activation MUST be idempotent: activating an existing identical schedule
  reports the existing schedule instead of duplicating it.

**Scheduled run behaviour**

- **FR-017**: A scheduled run MUST measure capacity, classify pressure, and record the level,
  free bytes, free share, total bytes, threshold crossed, and target in a durable local
  record.
- **FR-018**: A scheduled run MUST NOT delete anything unless automatic reclamation is
  enabled for that target and the automatic gate is satisfied.
- **FR-019**: When the automatic gate is satisfied, the run MUST execute only the safe tier
  and MUST record the run in the deletion manifest with an automatic trigger distinguishable
  from a manual one.
- **FR-020**: A scheduled run MUST refuse, not downgrade, a configured non-safe automatic
  tier.
- **FR-021**: A scheduled reap MUST run as a dry run unless scheduled reaping is opted in for
  that target, and MUST in all cases honour the existing retention window, per-workspace
  extensions, explicit releases, and in-use protection.
- **FR-022**: A scheduled run MUST produce a machine-readable outcome including whether it
  warned, whether it reclaimed, and why it did not act when it did not.

**Visibility**

- **FR-023**: The storage status output MUST show the capacity warning with free bytes, free
  percentage, total bytes, the threshold crossed, and the next command, whenever the level is
  warning or critical.
- **FR-024**: The health check MUST report the same warning for the target it checks, at a
  severity matching the level, and MUST distinguish unmeasured capacity from healthy
  capacity.
- **FR-025**: The scheduled run's own output MUST carry the same numbers, so reading the
  last run answers the question without re-measuring.
- **FR-026**: No warning output may be a bare statement that the disk is full; every warning
  MUST include the measured numbers and a next action.
- **FR-027**: A `normal` level MUST add no warning output to any surface.

- **FR-031**: The health check MUST NOT contact a remote target; it MUST read the durable
  record of the last monitor run for that target.
- **FR-032**: A missing or stale last-run record MUST be reported as such, naming its age and
  how to refresh it, and MUST NOT be presented as a healthy result.

**Tool-interface parity**

- **FR-028**: The tool interface's cleanup planning and application MUST accept a tier in
  addition to the existing scope, registered through the existing tool-group manifest.
- **FR-029**: Supplying both a tier and a scope, or neither, MUST be refused with a specific
  reason.
- **FR-030**: A tiered application through the tool interface MUST require the same explicit
  confirmation as the command line and MUST return the same outcome shape.

### Key Entities

- **Storage monitor policy**: the resolved per-target thresholds and switches — warning
  ratio, critical ratio, automatic enabled, automatic tier, automatic ratio, reap enabled.
- **Schedule plan**: a target, a cadence, a bounded timeout, the exact unattended commands,
  and an enabled/not-enabled state.
- **Monitor run record**: target, timestamp, pressure level, capacity numbers, whether the
  automatic path ran, what it reclaimed, and the reason for inaction.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a host below 15% free, an operator running the routine health check sees
  the free amount, the free percentage, and the next command without running any additional
  command.
- **SC-002**: No schedule can be brought into existence without an explicit confirmation
  step; an unconfirmed activation attempt leaves zero files and zero registered timers.
- **SC-003**: With default configuration, a scheduled run against a host at any pressure
  level deletes zero bytes.
- **SC-004**: With automatic reclamation enabled, the set of things a scheduled run can
  delete is exactly the safe tier's set; any other configured tier produces a refusal, in
  100% of cases, before any host-side action.
- **SC-005**: A tiered plan requested through the tool interface and through the command line
  for the same target and tier produce the same candidate set.
- **SC-006**: Threshold classification is correct at every boundary value, including exactly
  at the warning ratio, exactly at the critical ratio, and with unmeasurable capacity.

## Assumptions

- The scheduled runner is the operator's existing init/timer facility on the monitored host's
  controlling machine; Sandbox renders and, on confirmation, installs units there, mirroring
  how the recovery schedule is already handled.
- "Free space" means the available share of the filesystem that holds the target's managed
  deployment storage, as already measured by the existing status path.
- Alerting means output on surfaces the operator already reads. Delivering notifications to
  an external channel (email, chat, paging) is out of scope.
- The safe tier's definition, the protected-volume rules, the hosted-site protections, and
  the manifest format are inherited unchanged from the previous feature and are not
  redefined here.
- Per-target configuration lives with the other per-machine target settings, not in a project
  repository, because it describes a machine an operator owns rather than a project.

## Out of Scope

- Changing what the safe, tmp, or all tiers contain.
- External notification transports (email, chat, webhooks, paging).
- Automatic reclamation beyond the safe tier, at any threshold, under any configuration.
- Scheduling anything other than the storage check and the retention reap.
- Fixing the structural causes of accumulation (per-workspace package stores, full clones).
