# Product Requirements Draft: Per-Target Hosting Operations

**Status**: Refined

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "Per-target hosting operations: independent locks and delivery state per remote, project and environment, bounded queueing, and selective host teardown that preserves named projects"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); Haiku 5.5 read-only agents for ledger and PRD inventory. No independent readiness review has run.

**Final Validation**: `PENDING` — independent readiness review

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

One remote now hosts several unrelated projects. On the retired
`scaleway-sandbox` and on its replacement `xcloud-london`, Lenzora
(development and production), xspeed-hub, Amar Sonar Bangla, and
alimuzzaman.me share one server. Each project is deployed from its own agent
session, often within seconds of another.

Hosted apply treats the whole remote as one unit of work. A `sb host apply`
holds the remote-wide hosting lease for its entire build-and-deliver phase,
which takes minutes. A second apply for an unrelated project on the same remote
fails with `operation_busy` (feedback `adccd6b7`, critical, 2026-10-06 and
2026-10-07), the failed attempt leaves a delivery record that has to be retired
by hand, and `sb host login-url` waits on the same lease. The stop-gap since
commit `5d76399` queues an apply for up to a bounded wait instead of failing,
which turns a failure into a stall: every parallel session still serializes
behind every other, and a long build on one project delays an unrelated
one-line change on another.

The same whole-remote model has no way to remove hosting for everything except
a named project. When the operator authorized removing every remote container
resource except one hosted project, there was no supported teardown; the
resources inventory timed out and could not express "preserve this project",
so the work was done through the SSH escape hatch with a hand-made dependency
inventory (feedback `83dd053a`). A concurrent deploy refused with
`operation_busy` also leaves no retained pre-admission refusal evidence that
delivery inspect can show (feedback `04439999`), so the caller cannot tell
whether anything happened before retrying.

There is also no way for sessions that share one target to coordinate on
purpose. On 2026-10-08 four agent threads took turns deploying Lenzora
development by messaging each other, because Sandbox offers no claim or hold
on a hosted environment; the project added a controller-local hold under its
own home directory, which no other controller machine can see (feedback
`f72c4279`).

The cost is paid on every parallel agent session: detect busy, find the holder,
poll, retry, retire the failed record. It will grow as more sites move to the
shared xCloud server.

## Users and Desired Outcomes

- **Agent session deploying one project**: an apply for its own project is
  admitted and runs even while an unrelated project's apply is in progress on
  the same remote, with no manual retry and no record to retire afterwards.
- **Operator running several projects on one remote**: can see, per remote,
  which targets are busy, who holds each, and which operations are waiting,
  without reading lock files over SSH.
- **Operator decommissioning a shared remote**: can plan and apply a teardown
  that removes every hosted target except named ones, with a reviewable plan
  before anything is removed and evidence afterwards.
- **Owner of a hosted production site**: a teardown or a concurrent apply for a
  different project never touches their site's containers, volumes, routes, or
  retained delivery and recovery state.
- **Reviewer of a failed or refused deploy**: a busy refusal is retained and
  inspectable like any other pre-admission refusal, including which target and
  operation held the lease.

## Goals

- Hosting operations are scoped and serialized per target, where a target is
  one (remote, project, environment). Two applies for different targets on one
  remote proceed concurrently.
- Only genuinely shared steps on a remote (public edge routing changes, DNS
  record changes, shared ingress reloads, shared pool allocation) are serialized
  remote-wide, and they are held briefly, never for the length of a build.
- When a target is busy, a new operation on the same target either waits for a
  bounded, caller-chosen time and reports the holder while waiting, or refuses
  immediately with a typed result that names the holder; the caller chooses.
- A busy refusal creates no delivery effects, advances no generation, and is
  retained as a pre-admission refusal that delivery inspect can show for that
  target.
- Delivery and recovery state is kept per target, so an operation on one target
  cannot corrupt, block on, or be blocked by another target's state, and an
  interrupted operation leaves only its own target in an uncertain state.
- Existing remotes keep working through the change: retained delivery history,
  recovery receipts, and generations for every existing target remain
  readable and authoritative after the change, with a supported one-way
  conversion and a visible, refusable mixed state.
- A selective teardown exists: plan first, then confirmed apply, removing every
  hosted target on a remote except explicitly named preserved targets and
  the shared infrastructure those preserved targets need.
- Operators can list, per remote, every target with its current operation,
  holder identity, start time, and queue of waiting operations.
- A session can claim an explicit, expiring hold on a target, visible to
  every controller through the same listing; while a hold exists, other
  sessions' operations on that target wait or refuse exactly as they do for a
  running operation, naming the hold's holder and purpose. The holder, or an
  expiry, releases it.

## Non-Goals

- Changing what one apply does to its own target (build, deliver, initializers,
  edge proof). Spec 054 owns delivery outcomes and spec 051 owns immutable
  activation; this feature only changes which operations may run at once and
  how state is partitioned.
- Running two operations on the same target at once. Same-target operations
  stay strictly serialized.
- Host-wide resource governance (CPU, memory, disk admission). Feature 047 owns
  that; this feature does not decide whether a remote has capacity for two
  concurrent builds, only that locking no longer forbids it.
- Tearing down non-hosting resources (durable jobs, workspaces, node stores,
  disposable instances). Those stay with `sb resources` and `sb workspace`.
- Removing a remote's registration or its control-plane service.
- Cross-remote coordination.
- Any change to the local (non-remote) instance lifecycle.

## Product Scenarios

### Scenario 1 — Two projects deploy at once

- **Starting state**: Lenzora development is mid-apply on `xcloud-london`
  (build phase, several minutes left).
- **User action**: A second session runs `sb host apply` for xspeed-hub
  (environment `sandbox`) on the same remote.
- **Expected outcome**: The second apply is admitted immediately, runs to
  completion, and both targets report success. Neither apply waited for the
  other beyond the brief shared edge-routing step. No record needs retiring.

### Scenario 2 — Same target, second apply waits

- **Starting state**: Lenzora development is mid-apply.
- **User action**: Another session applies Lenzora development with a bounded
  wait.
- **Expected outcome**: The second apply reports that it is waiting, names the
  holder (operation, request, start time) while it waits, and starts when the
  first finishes. If the bound expires, it ends with a typed busy result that
  names the holder; nothing was changed and the refusal is retained.

### Scenario 3 — Same target, refuse-immediately mode

- **Starting state**: As in Scenario 2.
- **User action**: A deployment frontend applies the same target with no wait.
- **Expected outcome**: An immediate typed busy refusal naming the holder, no
  effects, and `sb host delivery inspect` for that target shows the refusal as
  a retained pre-admission outcome with the holder identity.

### Scenario 4 — Shared step contention is brief

- **Starting state**: Two different targets apply concurrently and both reach
  their edge-routing step at the same moment.
- **User action**: None; the operations proceed.
- **Expected outcome**: One edge change waits for the other to finish its brief
  shared step, then proceeds; neither waits for the other's build or delivery.

### Scenario 5 — Operator inventory of busy targets

- **Starting state**: Three targets on one remote: one applying, one waiting,
  one idle.
- **User action**: The operator asks for the remote's hosting operation status.
- **Expected outcome**: One bounded, secret-free listing shows each target, its
  current operation and holder, how long it has held, and what is queued
  behind it.

### Scenario 6 — Interrupted apply isolates its own target

- **Starting state**: Two targets applying; the session driving one of them
  loses its connection mid-delivery.
- **User action**: The other session continues; later the first session
  reconnects.
- **Expected outcome**: The unaffected target completes normally. The
  interrupted target is the only one in an uncertain state; its retained
  operation is discoverable by the original identity (spec 054 behavior), and
  no other target's state file was touched.

### Scenario 7 — Selective teardown preserving one project

- **Starting state**: A remote hosting four targets. The operator wants only
  Amar Sonar Bangla production kept.
- **User action**: The operator requests a teardown plan preserving that
  target, reviews it, then applies with confirmation.
- **Expected outcome**: The plan lists, per target to be removed, the exact
  containers, images, named and anonymous volumes, bind paths, routes, and
  certificates that will go, and per preserved target what is kept, including
  shared ingress it needs. The apply removes exactly the planned items, keeps
  the preserved target serving throughout, and records post-apply evidence
  showing the preserved target intact and the removed targets absent.

### Scenario 8 — Teardown refuses on an ambiguous dependency (negative)

- **Starting state**: As in Scenario 7, but a volume or network is used by
  both a target to be removed and the preserved target.
- **User action**: The operator requests the plan.
- **Expected outcome**: The plan marks the shared item as preserved with the
  reason, or if ownership cannot be proven, refuses to include it and says so.
  The apply never removes an item whose sole ownership by a removed target is
  unproven.

### Scenario 9 — Teardown on a busy remote (negative)

- **Starting state**: One target is mid-apply.
- **User action**: The operator applies a teardown that would remove that
  target.
- **Expected outcome**: Refused before any effect, naming the active operation.
  A teardown that preserves the busy target may still be refused if a shared
  step would conflict; the refusal says which.

### Scenario 10 — Existing remote after upgrade (negative)

- **Starting state**: A remote with retained delivery history from before this
  feature, mid-way between old and new state layouts because a conversion was
  interrupted.
- **User action**: Any hosting command.
- **Expected outcome**: The command reports the mixed state and the supported
  conversion step, and refuses to mutate until the state is consistent. No
  history is lost, and the conversion can be re-run to completion.

### Scenario 11 — Older controller against a converted remote (negative)

- **Starting state**: The remote has per-target state; a local checkout
  predating this feature targets it.
- **User action**: The old checkout runs host apply.
- **Expected outcome**: A typed limitation naming the required capability. No
  partial writes to either state layout.

## Proposed Product Behavior

- The unit of hosting serialization becomes the target. A target's apply,
  recover, retire-delivery, login-url, and teardown-of-that-target are mutually
  exclusive with each other and with nothing else.
- Shared-remote steps are named explicitly in product output (for example edge
  routing, DNS, shared ingress reload, pool allocation) and are the only steps
  that take a remote-wide lease. A remote-wide lease is never held across a
  build, a source transfer, or a delivery wait.
- Waiting is the caller's choice with a bounded maximum; waiting output
  identifies the holder. Refusing is typed and retained.
- Per-target delivery and recovery state means a corrupt, locked, or in-flight
  state for one target has no effect on commands for another target. Existing
  remote-wide records are converted once, in a supported direction, with the
  result verifiable before mutation resumes.
- Teardown is a protected two-step operation (plan, then confirmed apply) with
  an exact item-level plan, explicit preserved targets, ownership-proven
  removal only, and post-apply evidence. It uses the existing exact-identity
  and no-wildcard rules of host storage reclamation.
- Every result in this feature is bounded and secret-free, and CLI and MCP
  agree on its meaning.

## Constraints and Dependencies

- Ownership note: the hosting command and delivery modules
  (`sandbox/commands/hosting.py`, `sandbox/delivery/hosting.py`,
  `sandbox/core/_remote.py`, `sandbox/core/_hosting.py`) are currently owned
  by thread `6343a530` (host-apply observability). Specification and
  implementation of this feature must be coordinated with that owner.
- Spec 054 (Recoverable Delivery Outcomes) defines admission, retained
  refusals, and the diagnosis query. This feature's busy refusal must be one of
  054's retained pre-admission outcomes, not a parallel mechanism.
- Spec 051 (Immutable Activation and Recovery) and spec 052 (Owned Storage
  Authority) define the state this feature partitions; their fences and
  receipts must survive the per-target split unchanged in meaning.
- Spec 042 (One-Click Host Storage Reclamation) sets the exact-identity,
  manifest-before-delete, no-wildcard rules that teardown inherits.
- Feature 047 (Host Resource Governance) decides capacity admission. Until it
  ships, concurrent applies are admitted without a capacity check; the
  operator accepts that risk on a 16 GB host and can fall back to waiting.
- Remote-side behavior changes take effect only after a remote runtime
  migrate; the Lenzora repin procedure applies.
- Constitution and CLAUDE.md module boundaries: new state and commands register
  through explicit manifests; no raw state file reads by consumers.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Unit of serialization | Target = (remote, project, environment) | Matches how operators think and how the shared remote is used; projects share nothing but the edge | Fable decision (delegated by user), 2026-10-08 |
| Remote-wide lease scope | Only named shared steps, held briefly; never across build, transfer, or delivery wait | Removes the multi-minute stall without allowing edge/DNS races | Fable decision (delegated by user), 2026-10-08 |
| Busy handling | Caller chooses bounded wait (holder shown) or immediate typed refusal; both retained | Frontends want fail-fast, interactive sessions want wait; both need evidence | Fable decision (delegated by user), 2026-10-08 |
| State partition | Delivery and recovery state kept per target, with a one-way supported conversion and a refusable mixed state | Independent targets cannot share a single state file without sharing failure modes | Fable decision (delegated by user), 2026-10-08 |
| Teardown shape | Plan then confirmed apply; preserve-list explicit; remove only ownership-proven items; post-apply evidence | Same protected two-step pattern as reclamation and recovery | Fable decision (delegated by user), 2026-10-08 |
| Teardown scope | Hosted targets and their routes, certificates, containers, images, volumes only | Jobs, workspaces and registration have their own owners | Fable decision (delegated by user), 2026-10-08 |
| Capacity admission | Out of scope; feature 047 owns it | Keeps this feature small; concurrency without governance is an accepted interim risk | Fable decision (delegated by user), 2026-10-08 |
| Explicit holds | A target hold is the same primitive as a running operation's lease, claimed on purpose with an expiry and purpose string, visible remote-wide | Agents coordinating over chat and controller-local hold files are the symptom; one shared lease with a CLI is the fix | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None blocking. The independent readiness review should confirm whether the
  interim "concurrency without capacity admission" risk is acceptable on the
  16 GB xCloud server, or whether a simple concurrent-apply cap per remote
  should be added as a product rule.

## Acceptance Outcomes

- Two applies for different targets on one remote, started within five seconds
  of each other, both succeed with no retry, and the second starts its build
  no later than ten seconds after admission regardless of the first's build
  duration.
- A same-target apply with a bounded wait reports the holder within two seconds
  and starts within five seconds of the holder finishing; with no wait it
  returns a typed busy refusal within two seconds.
- Every busy refusal appears in delivery inspect for its target as a retained
  pre-admission outcome with the holder's operation identity; zero busy
  refusals leave a record that needs retiring.
- No remote-wide lease is held longer than the declared shared-step bound (to
  be set in specification, expected well under one minute) in a run of ten
  concurrent mixed-target applies.
- After an interrupted apply on one target, every other target's commands
  succeed and their retained history is byte-for-byte unchanged.
- After conversion of an existing remote, every previously retained delivery
  outcome, recovery receipt, and generation remains queryable with the same
  identity and meaning; an interrupted conversion is reported and resumable,
  and no hosting mutation is admitted in the mixed state.
- A selective teardown preserving one target removes every planned item and
  nothing else, the preserved site answers its edge health check throughout,
  and the post-apply evidence lists zero unplanned removals and zero
  unproven-ownership removals.
- An older controller against a converted remote receives a typed limitation
  and causes zero state writes.

## Risks and Assumptions

- **Risk**: Concurrent builds on a 16 GB host can exhaust memory before
  feature 047 exists. Mitigation is operator discretion and the wait option;
  the review may add a per-remote concurrency cap.
- **Risk**: Splitting shared state can break fences that assumed one file
  (generation, effect_unknown, initializer receipts). The conversion must be
  specified against 051/054 fixtures, not assumed.
- **Risk**: Teardown is destructive and remote; ownership proof for anonymous
  volumes and shared networks may be unavailable, in which case the plan must
  leave them and say so, which may leave residue the operator must handle.
- **Risk**: The hosting modules are under active change by another thread;
  specification must be scheduled after their current work lands.
- **Assumption**: Projects on one remote share only the public edge and host
  pools; they do not share containers, volumes, or secrets.
- **Assumption**: The remote control plane can be migrated with the standard
  repin procedure before this ships to a production remote.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred (delegated
      decisions, recorded above; user may overturn).
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
