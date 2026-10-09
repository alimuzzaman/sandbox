# Feature Specification: Per-Target Hosting Operations

**Feature Branch**: `060-per-target-hosting-operations`

**Created**: 2026-10-09

**Status**: Draft

**Input**: `specs/060-per-target-hosting-operations/prd.md` (READY FOR SPECKIT; independent GPT-6.1-Sol PASS, round 4, 2026-10-09; feedback adccd6b7, 04439999, f72c4279)

## Context

One remote hosts several unrelated projects, and each is deployed from its own
agent session, often within seconds of another and often from different
controller machines. Today a hosted apply holds two controller-wide locks for
its whole build-and-deliver phase. As a result:

- applies for unrelated projects, or for different remotes from one machine,
  stall or fail with `operation_busy`;
- the same target applied from two machines is not serialized at all;
- busy refusals leave no retained evidence.

This feature makes the target (remote, project, environment) the unit of
serialization. Lease, hold and per-target operation state become
authoritative on the remote's runtime service, so controllers on different
machines coordinate. Only short shared edge steps take a remote-wide lease.
Busy and cap refusals become spec 054 retained pre-admission outcomes.
Delivery and recovery state is partitioned per target, with a one-way
conversion.

Selective host teardown is a separate follow-up (see Out of Scope).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Unrelated projects deploy at the same time (Priority: P1)

An agent session applies its project while an unrelated project's apply is in
progress on the same remote, or on a different remote from the same
controller. The second apply is admitted at once and runs to completion. The
only wait between them is a brief shared edge step.

**Why this priority**: This is the critical feedback (adccd6b7). Every parallel
session on one controller serializes behind every other, so a long build on
one project delays an unrelated one-line change on another.

**Independent Test**: Start two applies for different targets on one remote
within five seconds of each other, then two applies from one controller to
two remotes. Measure admission, build start and wait times.

**Acceptance Scenarios**:

1. **Given** Lenzora development is mid-build on `xcloud-london`, **When** a
   second session applies xspeed-hub (environment `sandbox`) on the same
   remote, **Then** it is admitted immediately and both report success with
   no retry. It starts its build within 10 seconds of admission, and its total
   wait on the first apply is under 60 seconds.
2. **Given** one controller applies to two different remotes, **When** both
   run, **Then** neither waits on the other for more than 2 seconds at any
   point, and each starts its build within 10 seconds of admission.
3. **Given** two different targets reach their edge-routing step at the same
   moment, **When** they proceed, **Then** one waits only for the other's
   shared step, never for its build, delivery or post-change verification.
4. **Given** an apply for one target is in progress, **When**
   `sb host login-url` runs for an idle target on the same remote, **Then** it
   returns within 5 seconds.

---

### User Story 2 - Same-target operations are serialized across controllers, with a caller-chosen wait (Priority: P1)

Two sessions, on the same controller or on different ones, act on the same
target. The second one waits for a bounded time and names the holder while
it waits, or it refuses immediately with a typed result that names the
holder. The caller chooses which. A refusal is retained and inspectable, and
it creates no effects.

**Why this priority**: Without remote-side authority the real contention
(several controllers on one target, feedback f72c4279) is not serialized at
all. Busy refusals currently leave unexplained failed jobs (04439999).

**Independent Test**: From two controllers, apply the same target with a
bounded wait, then with no wait. Check for overlap, holder reporting,
timings, and the retained refusal in `sb delivery inspect`.

**Acceptance Scenarios**:

1. **Given** a target is mid-apply, **When** another controller applies it
   with a bounded wait, **Then** within 2 seconds it reports waiting and names
   the holder (operation, request, controller, start time). It starts within
   5 seconds of the holder ending, provided ordinary admission permits it
   (the holder succeeded, or ended with no unresolved recovery fence).
2. **Given** the bound expires first, **Then** the apply ends with a typed
   busy result that names the holder. Nothing changed and the refusal is
   retained.
3. **Given** a target is mid-apply, **When** a frontend applies it with no
   wait, **Then** within 2 seconds it returns a typed busy refusal that names
   the holder. `sb delivery inspect` for the target shows the refusal as a
   retained pre-admission outcome with the holder's operation identity, and
   no record needs retiring.
4. **Given** two controllers apply the same target, **Then** the operations
   never overlap.

---

### User Story 3 - Uncertain predecessors keep the target fenced (Priority: P1)

A session's lease expires while it is disconnected, or while a remote phase it
dispatched keeps running. Lease expiry stops the old holder from starting
new effects. It never by itself admits a successor while the predecessor's
effect is uncertain.

**Why this priority**: Moving authority to the remote and adding expiry must
not open a race in which two sessions change one target. That race would be
worse than today's stall.

**Independent Test**:
- Expire a holder's lease while its remote phase runs, then try a successor
  from another controller before and after the phase stops.
- Repeat with a phase that ended after Compose had entered.
- Reconnect the old holder.

**Acceptance Scenarios**:

1. **Given** a holder's lease expired and another session was admitted,
   **When** the original holder reconnects, **Then** it performs no further
   forward effects on the target. Rollback under a reacquired lease remains
   permitted. It records its own operation as `effect_unknown` with the
   reason.
2. **Given** an expired holder dispatched a remote phase that is still running,
   **When** a successor from any controller asks to apply, **Then** it is
   refused with the predecessor's operation identity and the inspect command.
   This lasts until the phase is proven to have stopped or reached a terminal
   state.
3. **Given** the phase is proven stopped or terminal, **Then** the successor is
   admitted only if ordinary admission checks pass and every applicable
   recovery fence (054 FR-008, 062) is cleared, that is, the outcome was
   adopted, proven to have had no effect, or rolled back. Cessation is
   necessary, never sufficient.
4. **Given** the phase ended terminal-failed after the Compose step had
   entered, **Then** the successor stays refused until the outcome is
   adopted, proven no-effect or rolled back.
5. **Given** a session loses its connection mid-delivery on one target while
   another target applies, **Then** the other target completes normally. Only
   the interrupted target is uncertain, and every other target's retained
   history is byte-for-byte unchanged.

---

### User Story 4 - Explicit holds coordinate sessions (Priority: P2)

A session claims an expiring hold on a target, with a purpose. The hold is
visible to every controller. The holder's own operations, which present the
hold identity, are admitted. Every other session waits or refuses, naming the
hold. Holds can be renewed within a four-hour total life and released. A
stale hold can be broken with a recorded reason.

**Why this priority**: Agents currently coordinate deploys by messaging each
other, and they use controller-local hold files that other machines cannot
see (f72c4279).

**Independent Test**:
- Claim, renew, use, release and break holds from two controllers.
- Let a hold expire on an idle target and on a fenced target.

**Acceptance Scenarios**:

1. **Given** an idle target, **When** a session claims a hold with a purpose
   and the default duration (1 hour), **Then** within 5 seconds the hold is
   visible from a second controller, with holder, hold identity, controller,
   purpose and expiry.
2. **Given** a held target, **When** the holder applies and presents the hold
   identity (flag or environment setting), **Then** it is admitted.
3. **Given** a held target, **When** another session applies without the hold
   identity, including a session on the holder's own controller, **Then** it
   waits or refuses and names the hold. It is never admitted.
4. **Given** a hold, **When** the holder renews it, **Then** expiry moves, but
   never past four hours after the original claim. A renewal past that is
   refused and names the remaining allowance. A hold claimed after expiry has
   a new identity and start time, and it queues like any other.
5. **Given** a hold request longer than four hours, **Then** it is refused,
   naming the maximum.
6. **Given** a holder dies without releasing, **When** another session waits on
   the target, **Then** expiry removes only the hold as a blocker:
   - on an idle, unfenced target the waiting apply is admitted within
     5 seconds of expiry;
   - if an operation under the hold dispatched a remote phase, the waiting
     apply follows User Story 3 (it keeps waiting within its bound or refuses
     until the fence clears, then it is admitted within 5 seconds).
7. **Given** another session's hold, **When** an operator releases it without
   `--break-hold`, **Then** the release is refused and names the holder.
   **When** the operator releases with `--break-hold` and a reason, **Then**
   the hold is released, and the breaker's identity, the reason and the
   broken hold are recorded in the target's retained history.

---

### User Story 5 - Operator sees every target's operation, hold and queue (Priority: P2)

An operator asks for a remote's hosting operation status and gets one bounded,
secret-free listing. For each target it shows:

- the current operation or hold, and the holder;
- how long the holder has held it;
- the hold's expiry and purpose;
- what is queued behind it;
- the remote's build cap and its current build holders.

**Why this priority**: Today this is found by reading lock files over SSH.

**Independent Test**: Put four targets on one remote into these states:
applying, waiting, held and idle. Run the listing from two controllers.

**Acceptance Scenarios**:

1. **Given** the four states, **When** the listing runs, **Then** it returns
   within 5 seconds, showing each target's operation or hold, holder, start
   time, hold expiry and purpose, and the waiting queue.
2. **Given** the same remote, **When** the listing runs from a second
   controller, **Then** it shows the same state.
3. **Given** the listing, **Then** CLI and MCP return the same meaning, and no
   field carries a secret.

---

### User Story 6 - The build cap protects a shared host (Priority: P2)

Until host resource governance (feature 047) exists, each remote admits at
most a set number of concurrent build phases. The default is two, and one
value applies to every controller. Further applies wait or refuse, as the
caller chooses.

**Why this priority**: Concurrency on a shared production host can exhaust
memory once per-target admission removes the accidental serialization.

**Independent Test**: With the cap at two and two builds running, start a
third apply with a wait, then with no wait. Change the cap through the
confirmed remote operation.

**Acceptance Scenarios**:

1. **Given** two builds are running and the cap is two, **When** a third apply
   waits, **Then** it reports the cap and both build holders, and it starts
   its build when one finishes. A third build never runs alongside two.
2. **Given** the same state, **When** the third apply uses no wait, **Then** it
   returns a typed, retained refusal naming the cap and the holders, with no
   effects.
3. **Given** an operator changes the cap, **Then** the change requires
   explicit confirmation, records who changed it and when, and is shown in
   the listing. A controller's remote record never carries or overrides the
   cap.

---

### User Story 7 - Existing remotes and older controllers upgrade safely (Priority: P2)

Existing remotes keep working. Retained delivery history, recovery receipts
and generations stay readable and authoritative after the state is converted
to the per-target layout. A half-converted state refuses to mutate and points
at the resumable conversion. An older controller is told it is too old and
writes nothing. An unreachable or unmigrated lease authority refuses instead
of falling back to controller-local admission.

**Why this priority**: The production remote (`xcloud-london`) carries
retained history for five live sites, and fences that assumed one file must
survive the split.

**Independent Test**:
- Convert a fixture controller state built from spec 051/054 fixtures.
- Interrupt the conversion and re-run it.
- Run a hosting mutation from a second, unconverted controller.
- Run an older checkout against the converted remote.
- Stop the remote runtime service.

**Acceptance Scenarios**:

1. **Given** retained pre-feature state, **When** the conversion runs, **Then**
   every delivery outcome, recovery receipt and generation is queryable with
   the same request id and meaning. Only the scope key moves from checkout to
   project, per feature 062.
2. **Given** an interrupted conversion, **When** any hosting command runs for
   that remote, **Then** it reports the mixed state and the conversion step,
   and refuses to mutate. Re-running the conversion completes it.
3. **Given** a remote with lease authority and a second controller with
   unconverted state, **When** that controller runs a hosting mutation,
   **Then** it gets the same mixed-state refusal. The converted controller and
   the remote are unaffected.
4. **Given** a converted remote, **When** a controller checkout that predates
   this feature runs host apply, **Then** it receives the spec 061
   `protocol_too_old` verdict naming the required protocol, and neither side's
   state is written.
5. **Given** the remote runtime service is unreachable or not migrated, **When**
   any hosting mutation runs, **Then** within 15 seconds it returns the typed,
   retained refusal `lease_authority_unavailable` with zero protected delivery
   effects and no controller-local fallback. The remedy names the standard
   remote runtime migrate/repin procedure.

---

### Edge Cases

- **Re-registration during an operation.** Re-registering a remote while an
  operation is admitted on one of its targets never repoints that operation.
- **Reclamation during per-target work.** Host storage reclamation started
  while a target has an admitted operation or a live hold, but no remote-wide
  lease or edge/address-pool transaction is active, runs and removes nothing
  that belongs to a hosted target.
- **Reclamation during shared work.** Reclamation started during a remote-wide
  lease or a host edge/pool transaction reports `host_reclaim_busy` and
  removes nothing.
- **Slow but live holder.** A live holder renews its lease, so a slow
  operation does not lose its lease and turn into `effect_unknown`.
- **Edge rollback.** An edge and DNS rollback (feature 064) that runs after the
  remote-wide lease was released re-acquires the lease first and stays within
  the 60-second bound.
- **Waits.** A wait of 0 refuses immediately. A wait above 3600 seconds is
  refused, naming the maximum. The default wait is 600 seconds.
- **Waiting caller interrupted.** A waiting caller that is interrupted while
  waiting leaves no queue entry behind and no effects.
- **Corrupt state on another target.** A corrupt, locked or in-flight state
  for one target never affects commands for another target.
- **Hold-only operations.** Hold claim, renew and release are themselves
  same-target exclusive. A claim on a busy target waits or refuses like any
  operation.

## Requirements *(mandatory)*

### Functional Requirements

**Serialization unit and exclusion**

- **FR-001**: The unit of hosting serialization MUST be the target: (remote,
  project, environment).
- **FR-002**: Every registered target-mutation operation MUST be mutually
  exclusive with every other on the same target. This covers:
  - apply, sync, login-url, edge-continue and failed-apply recover;
  - the image stage, provision, activate, adopt, rollback, recover and settle
    operations;
  - retire-delivery;
  - hold claim, renew and release.
- **FR-003**: Operations on different targets MUST NOT exclude each other,
  except through the shared-remote steps (FR-006) and the build cap (FR-022).
- **FR-004**: No controller-wide lock, including the remote registration
  lock, MUST be held across a build, a source transfer, a delivery or a
  verification wait. Applies to different remotes from one controller MUST
  NOT serialize against each other.
- **FR-005**: Re-registering a remote MUST NOT repoint an operation already
  admitted on one of its targets.

**Shared steps**

- **FR-006**: Only these steps MAY take a remote-wide lease: DNS and
  edge-routing mutation, and shared ingress reload. They MUST be named as
  shared steps in product output.
- **FR-007**: A remote-wide lease MUST NOT be held across a build, a source
  transfer, a delivery wait or a verification wait, and MUST NOT be held
  longer than 60 seconds.
- **FR-008**: A rollback of edge or DNS changes that runs after the
  remote-wide lease was released MUST re-acquire the lease first and stay
  within the 60-second bound.

**Remote authority**

- **FR-009**: The lease for a target, any hold on it, and its
  current-operation record MUST be authoritative on the remote's runtime
  service.
- **FR-010**: A controller MAY cache that state for display, but MUST NOT
  decide admission from its cache.
- **FR-011**: If the remote runtime service is unreachable or not migrated,
  every hosting mutation MUST refuse with `lease_authority_unavailable`
  within 15 seconds, as a retained pre-admission outcome with zero protected
  delivery effects and no controller-local fallback admission. The remedy
  MUST name the standard remote runtime migrate/repin procedure.

**Busy handling**

- **FR-012**: When a target is busy or held, the caller MUST be able to choose
  one of two behaviors:
  - a bounded wait: default 600 seconds, maximum 3600 seconds, and a wait of
    0 refuses immediately;
  - an immediate refusal.
- **FR-013**: A waiting operation MUST report within 2 seconds that it is
  waiting, and name the holder: operation, request identity, controller and
  start time (or the hold identity, holder, purpose and expiry).
- **FR-014**: A waiting operation MUST start within 5 seconds of the blocker
  ending, provided ordinary admission permits it.
- **FR-015**: A busy refusal, a wait timeout or a cap refusal MUST:
  - be a typed result naming the holder or holders;
  - create no delivery effects and advance no generation;
  - be retained as a spec 054 pre-admission outcome, visible in
    `sb delivery inspect` for the target with the holder's operation identity;
  - never leave a record that needs retiring.

**Leases and uncertainty**

- **FR-016**: A live holder MUST keep its lease renewed, so that a slow but
  live operation does not lose it.
- **FR-017**: A lease whose holder disappears MUST expire. A holder that
  returns after its lease expired MUST perform no further forward effects
  (rollback under a reacquired lease remains permitted), and MUST record its
  own operation as `effect_unknown` with the reason.
- **FR-018**: Lease expiry alone MUST NOT clear the predecessor's uncertainty
  fence. If the expired holder dispatched a remote phase:
  - no successor from any controller MUST be admitted to effects on the
    target until the phase is proven to have ceased or reached a terminal
    state;
  - even then, a successor MUST be admitted only once ordinary admission
    checks pass and every applicable recovery fence (054 FR-008, 062) is
    cleared (adopted, proven no-effect, or rolled back);
  - the refusal MUST name the predecessor's operation identity and the
    inspect command.

**Holds**

- **FR-019**: A session MUST be able to claim a hold on a target with a
  purpose and a duration. The default is 1 hour and the maximum is 4 hours
  from the original claim. A request above the maximum MUST be refused,
  naming the maximum.
- **FR-020**: A hold MUST belong to the claiming session, identified by a
  secret-free hold identity issued at claim:
  - renew, release and operations under the hold MUST present that identity,
    by flag or environment setting;
  - any session that does not present it, including one on the same
    controller, is not the holder: it MUST wait or refuse exactly as for a
    running operation, naming the hold;
  - the holder's own operations presenting the identity MUST be admitted,
    subject to the other admission rules.
- **FR-021**: Hold expiry and release:
  - The holder MAY renew a hold, but no renewal MAY set expiry later than four
    hours after the original claim. Such a renewal MUST be refused, naming the
    remaining allowance.
  - The holder MAY release a hold.
  - Expiry MUST be the only detection of a dead holder, and it MUST remove
    only the hold as a blocker (FR-018 still applies).
  - A hold claimed after expiry or release MUST carry a new identity and
    start time.
  - A non-holder release MUST be refused unless `--break-hold` is given with a
    reason. A break MUST record the breaker's identity, the reason and the
    broken hold in the target's retained history.

**Build cap**

- **FR-022**: Each remote's runtime service MUST admit at most a configured
  number of concurrent build phases. The default is 2, and one value applies
  to every controller. An apply that would exceed the cap MUST wait (bounded,
  showing the cap and the current build holders) or refuse, by caller choice,
  under FR-015.
- **FR-023**: The build cap:
  - MUST be shown in the per-remote listing;
  - MUST be changed only by an explicit, confirmed remote operation that
    records who changed it and when;
  - MUST NOT be carried or overridden by a controller's remote record.

**Listing**

- **FR-024**: A per-remote listing MUST return within 5 seconds and be
  bounded and secret-free. For every target it MUST show the current
  operation or hold, the holder, the start time, the hold expiry and purpose,
  and the waiting queue. It MUST also show the build cap and the build
  holders. It MUST show the same state from every controller.

**State partition and conversion**

- **FR-025**: Delivery and recovery state MUST be kept per target. A corrupt,
  locked or in-flight state for one target MUST NOT affect commands for
  another target. An interrupted operation MUST leave only its own target
  uncertain, and every other target's retained history MUST stay
  byte-for-byte unchanged.
- **FR-026**: The conversion:
  - A supported, one-way, re-runnable conversion MUST convert one
    controller's retained state for a remote to the per-target layout, and
    MUST establish the remote-side lease authority.
  - After conversion, every previously retained delivery outcome, recovery
    receipt and generation MUST remain queryable with the same request id and
    meaning. Only the scope key moves from checkout to project, shared with
    feature 062.
  - Spec 051/052/054 fences and receipts MUST keep their meaning.
- **FR-027**: A mixed or interrupted conversion state, on this controller or
  on a second controller that has not converted its own state for an already
  converted remote, MUST be reported with the conversion step, and MUST
  refuse every hosting mutation for that remote until the state is
  consistent.
- **FR-028**: A controller that predates this feature MUST receive the spec
  061 `protocol_too_old` verdict from a converted remote, naming the required
  protocol, and MUST cause zero state writes on either side.

**Reclamation and interfaces**

- **FR-029**: Host storage reclamation MUST yield only to shared-remote work:
  - while a remote-wide lease or a host edge or address-pool transaction lock
    is held, it MUST report `host_reclaim_busy` and remove nothing;
  - per-target operations and holds MUST NOT block it;
  - its exclusion of hosted-target resources MUST be unchanged.
- **FR-030**: Every result in this feature MUST be bounded and secret-free,
  and CLI and MCP MUST agree on its meaning.
- **FR-031**: Operator documentation MUST describe locking as per target with
  remote-side authority, replacing the "shared per host" wording.

### Key Entities

- **Target**: (remote, project, environment). The unit of serialization,
  state partition and hold.
- **Target lease**: authority on the remote to run effects on one target.
  - Holder: operation, request identity, controller and session.
  - Records the start time and renewal.
  - Ends by release or by expiry.
- **Hold**: a lease claimed on purpose.
  - Fields: hold identity (secret-free), holder session, controller, purpose,
    claim time and expiry.
  - Limits: maximum life of 4 hours from claim; breakable with a recorded
    reason.
- **Remote-wide lease**: a short lease on a remote for shared steps only, with
  a 60-second bound.
- **Build slot**: one of the remote's capped concurrent build phases. The cap
  value records who changed it and when.
- **Waiting entry**: a queued operation on a target, with its wait bound.
- **Busy/cap refusal**: a spec 054 retained pre-admission outcome naming the
  holder(s), the cap or the hold.
- **Uncertainty fence**: carried over from 054/062. It blocks successors until
  the predecessor's phase has ceased and its outcome is adopted, proven
  no-effect or rolled back.
- **Per-target state partition and conversion record**: the per-target
  retained delivery and recovery state, plus the conversion's progress per
  controller and remote.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Two applies for different targets on one remote, started
  within 5 seconds of each other, both succeed with no retry. The second
  starts its build within 10 seconds of admission whatever the first's build
  duration, and its total wait on the first is under 60 seconds.
- **SC-002**: Two applies from one controller to two remotes never wait on
  each other for more than 2 seconds at any point, and each starts its build
  within 10 seconds of admission.
- **SC-003**: Two applies for the same target from two controllers never
  overlap, in 100% of runs.
- **SC-004**: A same-target waiter reports the holder within 2 seconds and
  starts within 5 seconds of the holder ending when admission permits it. A
  no-wait caller gets a typed busy refusal within 2 seconds.
- **SC-005**: 100% of busy and cap refusals appear in delivery inspect for
  their target with the holder's operation identity, and none leaves a record
  that needs retiring.
- **SC-006**: In a run of ten concurrent mixed-target applies:
  - no remote-wide lease is held longer than 60 seconds;
  - every untouched target's edge health check, sampled every 5 seconds,
    records zero failures.
- **SC-007**: `sb host login-url` for an idle target returns within 5 seconds
  while another target on the remote applies.
- **SC-008**: The per-remote listing returns within 5 seconds with every
  field in FR-024.
- **SC-009**: Hold behavior:
  - a hold is visible from a second controller within 5 seconds of the claim;
  - an expired hold on an idle, unfenced target admits the next waiter within
    5 seconds;
  - on a fenced target the waiter is admitted within 5 seconds of the fence
    clearing, and never before;
  - claims above 4 hours, renewals past 4 hours from the claim, and non-holder
    releases without `--break-hold` are refused in 100% of attempts.
- **SC-010**: With the cap at two, a third concurrent build never starts
  while two run.
- **SC-011**: A returning holder whose lease expired performs zero further
  forward effects, and its operation is recorded `effect_unknown`.
- **SC-012**: A session without the hold identity, including one on the
  holder's controller, is never admitted on a held target.
- **SC-013**: With the runtime service unreachable or unmigrated, every
  hosting mutation refuses with `lease_authority_unavailable` within
  15 seconds, with zero protected delivery effects.
- **SC-014**: A successor on a target whose expired holder's remote phase is
  still running, or whose stopped phase left an unresolved recovery fence, is
  refused from every controller until the fence clears.
- **SC-015**: Reclamation during per-target work removes nothing of a hosted
  target. During a remote-wide lease it reports `host_reclaim_busy` and
  removes nothing.
- **SC-016**: After an interrupted apply on one target, every other target's
  commands succeed and their retained history is byte-for-byte unchanged.
- **SC-017**: After conversion, 100% of previously retained outcomes,
  receipts and generations are queryable with the same request id and
  meaning. An interrupted conversion is reported and resumable, and no
  mutation is admitted in the mixed state from any controller.
- **SC-018**: An older controller against a converted remote receives
  `protocol_too_old` and causes zero state writes.

## Assumptions

- Projects on one remote share only the public edge and the host address
  pools. They share no containers, volumes or secrets.
- Remote-side behavior takes effect only after the standard remote runtime
  migrate/repin procedure, which is run before this ships to a production
  remote.
- Feature 062 changes delivery identity in the same retained records. The
  conversion and its fixture set are shared with 062, and their owner is
  decided at plan time.
- Feature 064's edge and DNS transaction runs inside this feature's
  remote-wide lease and its 60-second bound.
- Lease renewal intervals are set so that a live holder on a slow but
  responsive connection keeps its lease. The exact durations are a planning
  decision, bounded by the timings above.
- The hosting command, delivery and remote modules are also changed by the
  host-apply observability work. Implementation is coordinated with that
  owner and scheduled after their current work lands.

## Out of Scope

- **Selective host teardown** (feedback 83dd053a). This is a follow-up that
  reuses this feature's leases and holds. Its decisions are already recorded
  in the PRD's Non-Goals.
- **What one apply does to its own target.** Specs 054 and 051 own this.
- **Running two operations on one target at once.**
- **Host-wide resource governance.** Feature 047 owns it. This feature adds
  only the interim build cap.
- **Teardown of non-hosting resources.** Removing a remote registration or
  its control plane is also out of scope.
- **Multi-remote joint transactions.**
- **Any change to the local instance lifecycle.**
