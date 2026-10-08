# Feature Specification: Scheduled Safe Cleanup on a Remote

**Feature Branch**: `057-remote-scheduled-cleanup`

**Created**: 2026-10-08

**Status**: Draft

**Input**: `specs/057-remote-scheduled-cleanup/prd.md` (READY FOR SPECKIT; feedback 8a3e8c35)

## Clarifications

### Session 2026-10-08

Decisions delegated by the user to an independent Fable reviewer (yolo mode).

- Q: What cadence grammar does enable accept? → A: Any systemd calendar expression, validated on the host; default `daily`; not read from `schedule_calendar`.
- Q: What happens to an enabled routine after the remote runtime is migrated? → A: It keeps running under the new runtime and records the revision per run.
- Q: Is a run missed while the host was down caught up? → A: Yes, once at next boot, as an ordinary run.
- Q: Is the run start jittered? → A: Yes, by the existing `schedule_randomized_delay` policy (default 5 minutes).
- Q: How is overlap with other reclaiming work detected on the host? → A: One shared host reclaim guard taken by the routine, `resources cleanup` and the pressure-triggered cleanup; host apply is detected by probing its existing host-side transaction locks (Caddy hosting, nginx edge, Docker pool) without changing host apply. Apply work outside those transactions is not detected; cleanup only touches reclaim-eligible classes that apply does not create mid-transaction.
- Q: Where do the time bound and jitter come from? → A: The operator's resolved storage-monitor policy for the remote, sent at enable, validated on the host and recorded with the routine.
- Q: Who records `timed_out` when the bound is hit? → A: The run enforces the bound itself and writes the record; the init-system timeout is the bound plus 60 seconds as a backstop, and an unfinished record is finalized as `timed_out` at the next run or status read.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recurring safe cleanup runs on the remote (Priority: P1)

A remote operator turns on a recurring cleanup for one registered remote. From
then on, once a day, the remote itself removes the waste that a manual safe
cleanup would remove — orphaned workspace directories, their workspace-scoped
package volumes, and workspaces past their time-to-live — without the operator
logging in or keeping a laptop awake. Everything it removes is listed in a
deletion manifest before removal.

**Why this priority**: This is the whole product need. Without it, routine
cleanup stays manual, and a host at normal fill never gets cleaned.

**Independent Test**: Enable the routine on a test remote seeded with
eligible, excluded, STOPPED, PROTECTED and LIVE resources, trigger one run, and
compare the manifest and the host state with the seeded set.

**Acceptance Scenarios**:

1. **Given** a provisioned remote whose installed runtime matches the
   operator's revision and no routine enabled, **When** the operator enables
   the routine with explicit confirmation, **Then** the remote reports the
   routine enabled with its cadence, time bound, effective exclusions and next
   run time, and nothing is deleted at enable time.
2. **Given** the routine is enabled and the remote holds orphaned workspace
   directories with their package volumes, one STOPPED workspace, one
   workspace matching an exclusion and one live instance, **When** a run fires,
   **Then** a manifest listing only the eligible candidates is written before
   any removal, only those candidates are removed, and the run record shows
   bytes reclaimed.
3. **Given** the same seeded host, **When** the run completes, **Then** the
   STOPPED workspace, the live instance and every PROTECTED resource are
   untouched and absent from the candidates, and the excluded workspace
   appears only as skipped with reason `excluded_by_request`.
4. **Given** the operator enables the routine without confirmation, **When**
   the command runs, **Then** it refuses and changes nothing.

---

### User Story 2 - See what the routine did (Priority: P2)

The operator, or any later reader, asks for the routine's status on a remote
and sees whether it is enabled, its cadence and exclusions, when it last ran,
what happened, how much it reclaimed, which runtime revision it runs under, and
where the manifest is — without opening a shell on the host.

**Why this priority**: Unattended deletion is only acceptable if it is
auditable and its refusals are visible.

**Independent Test**: After one reclaiming run, one nothing-to-do run and one
refused run, request status and check each record.

**Acceptance Scenarios**:

1. **Given** at least one completed run, **When** the operator requests
   status, **Then** the output names enabled state, cadence, effective
   exclusions, last run time, last outcome, bytes reclaimed and runtime
   revision in one response.
2. **Given** a run that attempted removals, **When** status is read, **Then**
   it includes the reference to that run's manifest; a run with nothing to do
   reports no manifest reference.
3. **Given** more than 30 completed runs, **When** status history is read,
   **Then** only the most recent 30 run records are retained.
4. **Given** the remote is unreachable from the operator, **When** it becomes
   reachable again, **Then** status shows the runs that happened meanwhile.

---

### User Story 3 - Turn the routine off (Priority: P3)

The operator disables the routine with confirmation. No further runs fire, and
past run records and manifests remain readable.

**Why this priority**: Required for safe operation and rollback, but only
after the routine exists.

**Independent Test**: Enable with a short test cadence, disable, wait two
cadence periods, and confirm no run fired and no schedule remains on the host.

**Acceptance Scenarios**:

1. **Given** an enabled routine, **When** the operator disables it with
   confirmation, **Then** no run fires afterwards and the host keeps no
   schedule for the routine.
2. **Given** a disabled routine, **When** status is read, **Then** it reports
   disabled and still lists the retained run history.

---

### Edge Cases

- **Runtime mismatch**: the remote's installed runtime differs from the
  operator's revision; enable refuses, names the mismatch, and changes no
  schedule.
- **Run exceeds its time bound**: the run stops at the bound (the init system
  stops it at most 60 seconds later as a backstop), records `timed_out`, and removes nothing after the bound.
  Removals made before the bound are listed. The next run starts a fresh plan;
  items already gone are reported `already_absent`.
- **Overlap**: a run fires while another scheduled run, an operator-initiated
  cleanup (local or through the control plane), a pressure-triggered automatic
  cleanup, or a host apply is in progress on the remote; the new run records
  `skipped: busy` and does nothing.
- **Incomplete inventory**: the remote cannot classify every resource (for
  example a capacity-only or partial probe); the run removes nothing and
  records a refusal with its reason.
- **Invalid exclusion**: an exclusion that cannot be parsed refuses enable;
  it is never silently ignored.
- **Non-systemd host or missing user lingering**: enable refuses with a typed
  reason.
- **Unregistered or unprovisioned remote**: enable refuses before contacting
  the host.
- **Re-enable with new settings**: enabling an already enabled routine
  replaces its settings in one step; no duplicate schedules exist.
- **Host down at the scheduled time**: the run happens once at next boot; if
  the inventory is not yet complete it removes nothing and records a refusal.
- **Runtime migrated while enabled**: the routine keeps running; status shows
  the enable-time and last-run revisions.
- **Remote removed locally while enabled**: the routine keeps running on the
  host; registering the remote again lets the operator see and disable it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Operators MUST be able to enable a recurring cleanup routine for
  one named, registered, provisioned remote with a single command that
  requires explicit confirmation.
- **FR-002**: Operators MUST be able to disable the routine with a single
  command that requires explicit confirmation; disabling MUST leave no
  schedule for the routine on the host.
- **FR-003**: The routine MUST be off by default for every remote.
- **FR-004**: The routine MUST run on the remote host under its own init
  system, independently of the operator's machine and of free-space pressure.
  A scheduled time missed because the host was down MUST run once when the
  host next boots; a catch-up run is an ordinary run, subject to FR-019.
- **FR-005**: The default cadence MUST be `daily`. Operators MAY pass any
  systemd calendar expression at enable; enable MUST validate it on the host
  before writing any schedule and refuse with `invalid_cadence` otherwise. The
  cadence is recorded with the routine and MUST NOT be read from the
  `schedule_calendar` policy key.
- **FR-006**: Each run MUST be a single safe-tier reclamation pass with the
  same candidate selection as an operator-run safe-tier workspace reap,
  including workspaces past their time-to-live.
- **FR-007**: A run MUST NOT consider any tier other than safe.
- **FR-008**: A run MUST write the deletion manifest entry for every removal
  before that removal happens, using the existing deletion manifest.
- **FR-009**: Manifest records written by the routine MUST carry a trigger
  value distinct from manual, reap and pressure-triggered cleanups.
- **FR-010**: PROTECTED and LIVE resources, and STOPPED workspaces, MUST never
  appear as candidates, manifest intents or removals in a routine run.
- **FR-011**: Exclusions MUST be name globs matched against workspace and
  deployment entry names, using the same grammar as the existing workspace
  reap exclusions.
- **FR-012**: The effective exclusions MUST be the union of the exclusions
  recorded with the routine at enable time and the remote's own configured
  reclaim exclusions; the operator's local configuration MUST NOT affect a
  remote run.
- **FR-013**: Excluded items MUST be reported as skipped with reason
  `excluded_by_request` and MUST NOT appear as candidates or manifest intents.
- **FR-014**: An exclusion that cannot be parsed MUST cause enable to refuse.
- **FR-015**: Each run MUST have a time bound, enforced by the run itself with
  the host's init system as a backstop (FR-016); the default MUST be the `schedule_timeout` of the operator's resolved
  storage-monitor policy for that remote (30 minutes) unless overridden at
  enable. Each run start MUST be jittered by the `schedule_randomized_delay` of
  that same resolved policy (default 5 minutes), which is not settable at
  enable. Both values MUST be sent in the enable request, validated on the host
  with the storage-monitor schedule-field rules, and recorded with the routine;
  a run MUST use the recorded values, not the host's config at run time.
- **FR-016**: A run that reaches its time bound MUST stop, record `timed_out`,
  remove nothing after the bound, and list removals made before it. The run
  MUST enforce the bound itself and write that record; the init-system timeout
  MUST be the bound plus 60 seconds as a backstop. If the backstop fires, the
  next run or status read MUST finalize the open record as `timed_out` from the
  manifest's completed entries.
- **FR-017**: A run MUST NOT resume an interrupted earlier run; it MUST start a
  fresh plan and report already-removed items as `already_absent`.
- **FR-018**: The routine, `resources cleanup`, and the pressure-triggered
  automatic cleanup MUST take one shared non-blocking host reclaim guard on the
  host before any removal, whichever machine initiated them. A run MUST record
  `skipped: busy` and do nothing when that guard is held or when any existing
  host-side apply transaction lock (Caddy hosting, nginx edge, or Docker pool)
  is held; those locks are only probed, never taken, and host apply itself is
  not changed.
- **FR-019**: A run MUST remove nothing and record a refusal when the
  inventory is incomplete.
- **FR-020**: Enable MUST refuse, without changing any schedule, when the
  remote's installed runtime revision differs from the operator's revision,
  when the host lacks the required init system or user lingering, or when the
  remote is unregistered or unprovisioned.
- **FR-020a**: A remote service migrate MUST leave an enabled routine in
  place; later runs execute the installed runtime and record its revision, and
  status MUST show both the revision recorded at enable and the revision of the
  last run.
- **FR-021**: Enabling an already enabled routine MUST replace its settings
  atomically; at most one schedule for the routine exists per host.
- **FR-022**: Each run MUST produce a run record with start time, end time,
  outcome (reclaimed, nothing to do, refused, timed out, skipped busy), bytes
  reclaimed, runtime revision, refusal reason when refused, and manifest
  reference when any removal was attempted.
- **FR-023**: The host MUST retain the most recent 30 run records and discard
  older ones; manifests follow existing manifest retention rules.
- **FR-024**: Operators MUST be able to read the routine's status and run
  history through the authenticated control plane in one command, without
  shell access to the host.
- **FR-025**: All enable, disable, run and status operations MUST go through
  the authenticated control plane; none may use a raw shell path.
- **FR-026**: Status and run records MUST NOT disclose secrets, SSH targets or
  credential material.

### Key Entities

- **Cleanup routine**: per-remote configuration — enabled state, cadence, time
  bound, recorded exclusions, runtime revision at enable, enable time.
- **Run record**: one execution — start and end time, outcome, bytes
  reclaimed, skipped items with reasons, refusal reason, runtime revision,
  manifest reference.
- **Deletion manifest**: the existing pre-deletion record of intended and
  completed removals, extended with the routine's trigger value.
- **Exclusion**: a name glob identifying workspaces or deployments the routine
  must never select.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator enables recurring cleanup on a remote with one
  confirmed command, and the first scheduled run happens with no further
  operator action.
- **SC-002**: On a host seeded with eligible, excluded, STOPPED, PROTECTED and
  LIVE resources, 100% of removed items are in a manifest written before
  removal, and 0 excluded, STOPPED, PROTECTED or LIVE items are candidates,
  manifest intents or removals.
- **SC-003**: A run forced past its time bound stops within the bound plus 60
  seconds and records `timed_out`.
- **SC-004**: Status answers "is it on, when did it last run, what happened,
  how much was reclaimed" in one command with no host shell access.
- **SC-005**: After disable, no run fires across two cadence periods
  (verified with a short test cadence) and no schedule remains on the host.
- **SC-006**: Two overlapping cleanup triggers on one host never both remove
  resources; the later one records `skipped: busy`.
- **SC-007**: Run history never exceeds 30 records per host.

## Assumptions

- Target remotes are Linux hosts with systemd and user lingering, as the
  existing control-plane service already requires.
- The safe-tier classifier and reclaim exclusions shipped in 530fe3a are
  correct for unattended use; this feature does not change classification.
- The routine runs the remote's installed runtime, so classifier fixes reach
  it only after a remote service migrate; installing on a shared remote is
  coordinated with active deploy owners operationally.
- Pressure-triggered automatic cleanup keeps its current behavior; this
  feature only adds host-side mutual exclusion with it.
- Scheduling from the operator's local machine, other tiers, and non-Sandbox
  hosts are out of scope.
