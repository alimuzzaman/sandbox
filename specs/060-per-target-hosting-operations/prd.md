# Product Requirements Draft: Per-Target Hosting Operations

**Status**: Validated

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "Per-target hosting operations: independent locks and delivery state per remote, project and environment, bounded queueing, and selective host teardown that preserves named projects"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); Haiku 5.5 read-only agents for ledger and PRD inventory. Revised 2026-10-09 by a Claude Opus 5.5 root applying an independent Opus readiness review (verdict `REOPEN`) and Fable decisions delegated by the user; revised again 2026-10-09 by a Claude Opus 5.5 root applying the second-round Opus review (verdict `REOPEN`) and round-2 Fable decisions.

**Final Validation**: `PASS` — third independent Opus 5.5 review (read-only, 2026-10-09, on `bf0bcc4`) returned `REOPEN` on one decision (reclaim scope) and one ambiguity (hold maximum); both were decided by Fable (delegated by user) and applied with the reviewer's wording fixes. The check of those edits was a root review by Claude Opus 5.5, not an independent one, because the user stopped further sub-agents on 2026-10-09

**Validated On**: 2026-10-09

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
session, often within seconds of another, and often from different controller
machines.

Hosted apply holds two controller-wide locks on the submitting machine for
its whole build-and-deliver phase: the hosting state lock and the remote
registration lock, which covers every registered remote on that controller.
Applies to different remotes from one machine therefore serialize, while the
same target applied from two machines is not serialized at all. A per-target
lock already exists on the controller, but apply also takes both
controller-wide locks, so the per-target lock buys no concurrency. A second apply from the same controller, for an unrelated
project or even a different remote, fails with `operation_busy` (feedback
`adccd6b7`, critical, 2026-10-06 and 2026-10-07). That refusal is raised
before any delivery attempt is recorded, so it leaves a failed job and no
retained delivery evidence: `sb delivery inspect` cannot show it, and the
caller cannot tell whether anything happened before retrying (feedback
`04439999`). `sb host login-url` contends for the same controller-wide lock:
today it waits up to 30 seconds and then fails with `operation_busy`. The
stop-gap since commit `5d76399` queues an apply for up to a bounded wait
instead of failing, which turns a failure into a stall: every parallel session
on one controller still serializes behind every other, and a long build on one
project delays an unrelated one-line change on another. The operator
documentation repeats the wrong framing, describing the lock as shared per
host rather than per controller.

There is also no way for sessions that share one target to coordinate on
purpose. On 2026-10-08 four agent threads took turns deploying Lenzora
development by messaging each other, because Sandbox offers no claim or hold
on a hosted environment; the project added a controller-local hold under its
own home directory, which no other controller machine can see (feedback
`f72c4279`). Because the contending sessions run on different controllers,
any lease or hold that lives only on a controller cannot coordinate them.

The cost is paid on every parallel agent session: detect busy, find the holder,
poll, retry, and reconstruct what happened from a failed job. It will grow as
more sites move to the shared xCloud server.

## Users and Desired Outcomes

- **Agent session deploying one project**: an apply for its own project is
  admitted and runs even while an unrelated project's apply is in progress on
  the same remote, or on another remote from the same controller, with no
  manual retry.
- **Sessions on different controllers sharing one target**: their operations
  on that target are serialized by the remote, not by whichever controller
  they happen to run on, and they can claim an explicit hold to coordinate.
- **Operator running several projects on one remote**: can see, per remote,
  which targets are busy or held, who holds each, and which operations are
  waiting, without reading lock files over SSH.
- **Owner of a hosted production site**: a concurrent apply for a different
  project never touches their site's containers, volumes, routes, or retained
  delivery and recovery state.
- **Reviewer of a failed or refused deploy**: a busy refusal is retained and
  inspectable like any other pre-admission refusal, including which target and
  operation or hold was in the way.

## Goals

- Hosting operations are scoped and serialized per target, where a target is
  one (remote, project, environment). Two applies for different targets on one
  remote proceed concurrently, and applies to different remotes from one
  controller never serialize against each other. No controller-wide lock,
  including the remote registration lock, is held across a build, a source
  transfer, a delivery, or a verification wait.
- The lease, hold, and per-target operation state for a target are
  authoritative on the remote, so operations on one target from any number of
  controllers are serialized against each other. A controller keeps only a
  cache of that state plus its own retained outcomes.
- Only genuinely shared steps on a remote (public edge routing changes, DNS
  record changes, shared ingress reloads) take a remote-wide lease, and no
  remote-wide lease is held longer than 60 seconds. Verification waits are
  never inside it.
- When a target is busy or held, a new operation on the same target either
  waits for a bounded, caller-chosen time and reports the holder while
  waiting, or refuses immediately with a typed result that names the holder;
  the caller chooses.
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
- Operators can list, per remote, every target with its current operation or
  hold, holder identity, start time, and queue of waiting operations.
- A session can claim an explicit, expiring hold on a target, visible to every
  controller through the same listing; while a hold exists, other sessions'
  operations on that target wait or refuse exactly as they do for a running
  operation, naming the hold's holder and purpose.
- Until host resource governance exists, a remote admits at most a set number
  of concurrent build phases, held by the remote runtime service (default two,
  one value for every controller); further applies wait or refuse by caller
  choice.

## Non-Goals

- **Follow-up "selective host teardown"**: removing every hosted target on a
  remote except named preserved targets (feedback `83dd053a`). Moved out of
  this feature. Decisions already taken for that follow-up: plan first, then
  confirmed apply with the confirmation bound to the digest of the target
  inventory the plan was made from, so the apply refuses if the inventory
  changed; remove only ownership-proven items under the exact-identity,
  manifest-before-delete, no-wildcard rules of spec 042 (host storage
  reclamation); the plan lists Sandbox-marked DNS records of removed targets
  and removes them by default, with `--keep-dns` to retain them; retained
  delivery and recovery history of removed targets is kept read-only and never
  deleted; a fenced target refuses teardown until it is retired; a held target
  refuses teardown. It reuses this feature's per-target leases and holds.
- Changing what one apply does to its own target (build, deliver, initializers,
  edge proof). Spec 054 owns delivery outcomes and spec 051 owns immutable
  activation; this feature only changes which operations may run at once,
  where their coordination state is authoritative, and how state is
  partitioned.
- Running two operations on the same target at once. Same-target operations
  stay strictly serialized.
- Host-wide resource governance (CPU, memory, disk admission). Feature 047 owns
  that. This feature adds only a fixed per-remote cap on concurrent build
  phases as an interim guard.
- Tearing down non-hosting resources (durable jobs, workspaces, node stores,
  disposable instances). Those stay with `sb resources` and `sb workspace`.
- Removing a remote's registration or its control-plane service.
- Coordination of one operation that spans several remotes. Independence
  between remotes (no shared lock across them) is in scope; a joint
  multi-remote transaction is not.
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
- **User action**: Another session, on the same or a different controller,
  applies Lenzora development with a bounded wait.
- **Expected outcome**: The second apply reports that it is waiting, names the
  holder (operation, request, controller, start time) while it waits, and
  starts when the first finishes. If the bound expires, it ends with a typed
  busy result that names the holder; nothing was changed and the refusal is
  retained.

### Scenario 3 — Same target, refuse-immediately mode

- **Starting state**: As in Scenario 2.
- **User action**: A deployment frontend applies the same target with no wait.
- **Expected outcome**: An immediate typed busy refusal naming the holder, no
  effects, and `sb delivery inspect` for that target shows the refusal as a
  retained pre-admission outcome with the holder identity. The job, if any,
  is not left as an unexplained failure.

### Scenario 4 — Shared step contention is brief

- **Starting state**: Two different targets apply concurrently and both reach
  their edge-routing step at the same moment.
- **User action**: None; the operations proceed.
- **Expected outcome**: One edge change waits for the other to finish its brief
  shared step, then proceeds; neither waits for the other's build, delivery,
  or post-change verification.

### Scenario 5 — Operator inventory of busy targets

- **Starting state**: Four targets on one remote: one applying, one waiting,
  one held, one idle.
- **User action**: The operator asks for the remote's hosting operation status.
- **Expected outcome**: One bounded, secret-free listing shows each target, its
  current operation or hold and holder, how long it has held, the hold's
  expiry and purpose, and what is queued behind it.

### Scenario 6 — Interrupted apply isolates its own target

- **Starting state**: Two targets applying; the session driving one of them
  loses its connection mid-delivery.
- **User action**: The other session continues; later the first session
  reconnects.
- **Expected outcome**: The unaffected target completes normally. The
  interrupted target is the only one in an uncertain state; its retained
  operation is discoverable by the original identity (spec 054 behavior), and
  no other target's state was touched.

### Scenario 7 — Expired lease, holder returns (negative)

- **Starting state**: A session's lease on a target expired while it was
  disconnected, and another session has since been admitted on that target.
- **User action**: The original session reconnects and tries to continue.
- **Expected outcome**: The returning session performs no further effects on
  the target and records its own operation as `effect_unknown` with the
  reason, so delivery inspect shows what it may have done before losing the
  lease.

### Scenario 8 — Claim a hold

- **Starting state**: Lenzora development is idle.
- **User action**: A session claims a hold on it with a purpose and the
  default duration, then runs an apply under that hold.
- **Expected outcome**: The hold is visible in the remote listing from every
  controller with holder, hold identity, purpose, and expiry. The holder's own
  apply, presenting the hold identity, is admitted. Another session's apply on
  that target, including one on the same controller without the hold
  identity, waits or refuses, naming the hold. The holder may renew the hold, never past four hours after the claim,
  and may release it.

### Scenario 9 — Hold expires (negative)

- **Starting state**: A session holds a target and then dies without
  releasing it.
- **User action**: Another session applies the target with a bounded wait.
- **Expected outcome**: The hold ends at its expiry and the waiting apply is
  admitted then; expiry is the only way an abandoned hold is detected. A
  request for a hold longer than the maximum is refused with the maximum
  named.

### Scenario 10 — Break another session's hold

- **Starting state**: A target is held by another session whose purpose is
  stale.
- **User action**: The operator releases the hold without `--break-hold`, then
  again with `--break-hold` and a reason.
- **Expected outcome**: The first attempt is refused, naming the holder. The
  second releases the hold and records the breaker's identity, the reason,
  and the broken hold in the target's retained history.

### Scenario 11 — Third concurrent build waits for the cap

- **Starting state**: Two targets on `xcloud-london` are in their build phase
  and the remote's build cap is two.
- **User action**: A third session applies a third target, first with a
  bounded wait, then in a separate attempt with no wait.
- **Expected outcome**: With a wait, the apply reports the cap and the two
  current build holders while waiting, and starts its build when one finishes.
  With no wait, it returns a typed, retained refusal naming the cap and the
  holders, with no effects.

### Scenario 12 — Existing remote after upgrade (negative)

- **Starting state**: A controller with retained delivery history for a remote
  from before this feature, mid-way between old and new state layouts because
  a conversion was interrupted.
- **User action**: Any hosting command for that remote.
- **Expected outcome**: The command reports the mixed state and the supported
  conversion step, and refuses to mutate until the state is consistent. The
  conversion converts this controller's retained state for the remote to the
  per-target layout and establishes the remote-side lease authority. No
  history is lost, and the conversion can be re-run to completion.
- **Across controllers**: conversion is per controller, so a remote may
  already have lease authority while a second controller still holds
  unconverted retained state for it. That controller's hosting mutations for
  the remote refuse with the same mixed-state result until it runs its own
  conversion; the converted controller and the remote are unaffected.

### Scenario 13 — Older controller against a converted remote (negative)

- **Starting state**: The remote has remote-side lease authority; a controller
  checkout predating this feature targets it.
- **User action**: The old checkout runs host apply.
- **Expected outcome**: The remote returns the spec 061 protocol verdict
  `protocol_too_old` naming the required protocol. No partial writes to either
  the remote's or the controller's state.

### Scenario 14 — Lease authority unreachable (negative)

- **Starting state**: The remote runtime service is unreachable or not yet
  migrated.
- **User action**: Any hosting mutation for a target on that remote.
- **Expected outcome**: A typed, retained pre-admission refusal
  `lease_authority_unavailable` within 15 seconds, with zero effects and no
  controller-local fallback admission; the remedy names the standard remote
  runtime migrate/repin procedure.

## Proposed Product Behavior

- The unit of hosting serialization becomes the target. Every registered
  target-mutation operation (apply, sync, login-url, edge-continue,
  failed-apply recover, and the image stage, provision, activate, adopt,
  rollback, recover and settle operations), plus retire-delivery and hold
  claim, renew and release, is mutually exclusive with every other on the same
  target, and with nothing on any other target except the shared steps and the
  build cap.
- The lease for a target, any hold on it, and its current-operation record are
  authoritative on the remote's runtime service. A controller may cache them
  for display but never decides admission from its cache.
- Shared-remote steps are named explicitly in product output and are the only
  steps that take a remote-wide lease: DNS and edge routing mutation and shared
  ingress reload. A remote-wide lease is never held across a build, a source
  transfer, a delivery wait, or a verification wait, and never longer than 60
  seconds.
- A lease whose holder disappears expires. A holder that returns after its
  lease expired performs no further effects and records `effect_unknown` for
  its own operation.
- Holds: the default duration is one hour and the maximum is four hours
  measured from the original claim; the holder may renew, but no renewal
  extends expiry past four hours after the claim; a hold needed longer is
  claimed again after expiry or release, as a new hold with a new identity
  that queues like any other; expiry is the only dead-holder detection. A hold belongs to the session that claimed it, identified by the
  hold identity Sandbox issues at claim (secret-free, shown in the listing
  with controller, purpose and expiry). Renew, release and operations run
  under the hold present that identity by flag or environment setting; a
  session that does not present it, including another session on the same
  controller, is not the holder and must wait, refuse, or use `--break-hold`.
  `--break-hold` needs a reason and is recorded with the breaker's identity.
- Interim build cap, until feature 047 provides capacity admission: the build
  cap is a property of the remote runtime service: one value per remote,
  default two, applied to every controller. It is read in the per-remote
  listing and changed only by an explicit, confirmed remote operation that
  records who changed it and when; a controller's remote record never carries
  or overrides it. An apply that would exceed it waits (bounded, holders shown) or
  refuses, by caller choice, with the same retained-refusal rules as a busy
  target.
- Waiting is the caller's choice: the default wait is 600 seconds, the maximum
  3600 seconds, and a wait of 0 refuses immediately. Waiting output identifies
  the holder. Refusing is typed and retained as a spec 054
  pre-admission outcome.
- Per-target delivery and recovery state means a corrupt, locked, or in-flight
  state for one target has no effect on commands for another target. Existing
  controller-wide records are converted once, in a supported direction, with
  the result verifiable before mutation resumes.
- Every result in this feature is bounded and secret-free, and CLI and MCP
  agree on its meaning.
- If the remote runtime service is unreachable or not yet migrated, no hosting
  mutation is admitted (see Scenario 14).
- Host storage reclamation yields only to shared-remote work: while a
  remote-wide lease is held, or a host edge or address-pool transaction lock
  is held, reclamation reports `host_reclaim_busy` and removes nothing. A
  per-target operation or hold never blocks reclamation; reclamation never
  touches a hosted target's containers, volumes, routes, or retained state,
  and that exclusion is unchanged by this feature.
- Re-registering a remote must not repoint an operation already admitted on
  one of its targets. Taking the controller-wide registration lock out of the
  long phases keeps that guarantee.

## Constraints and Dependencies

- Ownership note: the hosting command, delivery, and remote modules are under
  active change by the host-apply observability work. Specification and
  implementation of this feature must be coordinated with that owner; the
  plan records the specific modules and owner.
- Spec 054 (Recoverable Delivery Outcomes) defines admission, retained
  refusals, `effect_unknown`, and the diagnosis query. This feature's busy and
  cap refusals must be 054 retained pre-admission outcomes, not a parallel
  mechanism.
- Spec 051 (Immutable Activation and Recovery) and spec 052 (Owned Storage
  Authority) define the state this feature partitions; their fences and
  receipts must survive the per-target split unchanged in meaning.
- Spec 061 (Remote Runtime Revision Coexistence) supplies the protocol verdict
  an older controller receives; this feature does not define its own
  compatibility check.
- Feature 047 (Host Resource Governance) decides capacity admission. Until it
  ships, the per-remote build cap is the only capacity guard.
- Remote-side behavior changes take effect only after the standard remote
  runtime migrate/repin procedure.
- Feature 062 (Hosted Delivery Evidence Reconciliation) changes delivery
  identity in the same retained records this feature partitions. The two
  features use one shared conversion and one fixture set; which feature owns
  them is decided at plan time.
- Feature 064 (Edge and DNS Change Transactions) defines the edge and DNS
  step. That step, including its rollback, must fit under this feature's
  remote-wide lease and its 60-second bound; a rollback that runs after the
  lease was released re-acquires the lease first. The no-further-effects rule
  for an expired lease covers forward changes; rolling back journaled edge and
  DNS changes after re-acquiring the remote-wide lease is permitted and stays
  inside the 60-second bound.
- Today the remote registration lock is controller-wide: one lock on a
  controller covers every registered remote, shared by registration writers
  and authority readers.
- Constitution and CLAUDE.md module boundaries: new state and commands register
  through explicit manifests; no raw state file reads by consumers.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Unit of serialization | Target = (remote, project, environment) | Matches how operators think and how the shared remote is used; projects share nothing but the edge | Fable decision (delegated by user), 2026-10-08 |
| Remote-wide lease scope | Only DNS/edge mutation and shared ingress reload; never build, transfer, delivery wait, or verification wait; hard bound 60 seconds | Removes the multi-minute stall without allowing edge/DNS races | Fable decision (delegated by user), 2026-10-09 |
| Busy handling | Caller chooses bounded wait (holder shown) or immediate typed refusal; both retained | Frontends want fail-fast, interactive sessions want wait; both need evidence | Fable decision (delegated by user), 2026-10-08 |
| State partition | Delivery and recovery state kept per target, with a one-way supported conversion and a refusable mixed state | Independent targets cannot share a single state file without sharing failure modes | Fable decision (delegated by user), 2026-10-08 |
| Coordination authority | Lease, hold, and per-target operation state authoritative on the remote runtime service; controllers keep a cache plus their own retained outcomes | The real contention (feedback `f72c4279`) is several controllers on one target, which controller-local state cannot serialize | Fable decision (delegated by user), 2026-10-09 |
| Upgrade conversion | Converts the controller's retained state for the remote to per-target layout and establishes remote-side lease authority; older controllers get the spec 061 `protocol_too_old` verdict | State is controller-local today; compatibility verdicts belong to 061 | Fable decision (delegated by user), 2026-10-09 |
| Explicit holds | Same primitive as a running operation's lease, claimed on purpose with purpose string; default 1 h, max 4 h, renewable by holder within four hours of claim; expiry is the only dead-holder detection; non-holder release needs `--break-hold` with a reason, recorded with breaker identity | Agents coordinating over chat and controller-local hold files are the symptom; one remote-wide lease with a CLI is the fix | Fable decision (delegated by user), 2026-10-09 |
| Expired lease, returning holder | No further effects; own operation recorded `effect_unknown` | A fenced-out holder must not race the new owner | Fable decision (delegated by user), 2026-10-09 |
| Hold maximum | Four hours bounds total life from claim to expiry across renewals; renewals past that are refused; longer work re-claims | Unlimited renewal would recreate the abandoned-holder problem the maximum exists to stop | Fable decision (delegated by user), 2026-10-09 |
| Reclaim vs hosting work | Reclaim yields to the remote-wide lease and host edge/pool transaction locks only; per-target operations and holds never block it | Hosted target resources are already excluded from every reclaim tier, and a hold can last four hours while the shared steps last at most 60 s | Fable decision (delegated by user), 2026-10-09 |
| Hold ownership | A hold belongs to the claiming session, identified by the secret-free hold identity issued at claim; renew, release and operations under the hold present it by flag or environment setting; any other session, including one on the same controller, is not the holder | One controller runs many agent sessions, so a controller cannot stand for a holder | Fable decision (delegated by user), 2026-10-09 |
| Same-target exclusion | Every registered target-mutation operation, plus retire-delivery and hold claim, renew and release, is exclusive with every other on the same target and with nothing on other targets except the shared steps and the build cap | Listing only four operations left the other target mutations unserialized | Fable decision (delegated by user), 2026-10-09 |
| Lease authority unreachable | Typed, retained refusal `lease_authority_unavailable` within 15 s, zero effects, no controller-local fallback; remedy names the standard migrate/repin procedure | Admission without the authority would reopen cross-controller races | Fable decision (delegated by user), 2026-10-09 |
| Interim concurrency cap | 2 build phases per remote until 047; further applies wait (bounded, holders shown) or refuse by caller choice | Memory on a shared production host | Fable decision (delegated by user), 2026-10-09 |
| Build cap location | A property of the remote runtime service: one value per remote, default two, applied to every controller; read in the per-remote listing; changed only by an explicit, confirmed remote operation that records who and when; never carried or overridden by a controller's remote record | A per-controller value cannot cap builds that several controllers start on one host | Fable decision (delegated by user), 2026-10-09 |
| Capacity admission | Out of scope beyond the interim cap; feature 047 owns it | Keeps this feature small | Fable decision (delegated by user), 2026-10-08 |
| Selective teardown | Moved to follow-up "selective host teardown"; its decisions are recorded in Non-Goals | Shrinks this feature to coordination and state partition | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None.

## Acceptance Outcomes

- Two applies for different targets on one remote, started within five seconds
  of each other, both succeed with no retry, the second starts its build
  no later than ten seconds after admission regardless of the first's build
  duration, and the second apply's total wait on the first is under 60
  seconds.
- Two applies from one controller to two different remotes run concurrently;
  neither waits on the other for longer than two seconds at any point, and the second starts its build no
  later than ten seconds after admission.
- Two applies for the same target from two different controllers never
  overlap: the second waits or refuses, naming the first.
- A same-target apply with a bounded wait reports the holder within two seconds
  and starts within five seconds of the holder finishing; with no wait it
  returns a typed busy refusal within two seconds.
- Every busy or cap refusal appears in delivery inspect for its target as a
  retained pre-admission outcome with the holder's operation identity; zero
  refusals leave a record that needs retiring.
- In a run of ten concurrent mixed-target applies, no remote-wide lease is held
  longer than 60 seconds.
- In the same run, every untouched target's edge health check, sampled every
  five seconds, records zero failures.
- `sb host login-url` for an idle target returns within five seconds while an
  apply for a different target on the same remote is in progress.
- The per-remote listing returns within five seconds and shows every target's
  operation or hold, holder, start time, hold expiry, and waiting queue.
- A hold is visible from a second controller within five seconds of being
  claimed; an expired hold admits the next waiting operation within five
  seconds of expiry; a hold request above four hours is refused; a non-holder
  release without `--break-hold` is refused, and with it is recorded with the
  breaker's identity and reason.
- A renewal that would set expiry later than four hours after the original
  claim is refused, naming the remaining allowance; a hold claimed after
  that expiry carries a new identity and start time.
- With the cap at two, a third concurrent build never starts while two are
  running.
- A returning holder whose lease expired performs zero effects and its
  operation is recorded as `effect_unknown`.
- A session on the same controller as a hold's holder, not presenting the
  hold identity, is never admitted on the held target.
- With the remote runtime service unreachable or not migrated, every hosting
  mutation refuses with `lease_authority_unavailable` within 15 seconds and
  makes zero writes.
- Host storage reclamation started while a target on the remote has an
  admitted operation or a live hold, but no remote-wide lease or edge/pool
  transaction is active, runs and removes nothing belonging to any hosted
  target; started during a remote-wide lease, it reports `host_reclaim_busy`
  and removes nothing.
- After an interrupted apply on one target, every other target's commands
  succeed and their retained history is byte-for-byte unchanged.
- After conversion of an existing remote, every previously retained delivery
  outcome, recovery receipt, and generation remains queryable with the same
  request id and meaning (the scope key moves from checkout to project, per
  feature 062); an interrupted conversion is reported and resumable,
  and no hosting mutation is admitted in the mixed state, including from a
  second controller that has not converted its own state.
- An older controller against a converted remote receives `protocol_too_old`
  and causes zero state writes.

## Risks and Assumptions

- **Risk**: Concurrent builds on the shared xCloud host can exhaust memory
  before feature 047 exists. The per-remote cap of two limits this; the
  operator can lower it with the confirmed remote operation.
- **Risk**: Splitting shared state can break fences that assumed one file
  (generation, effect_unknown, initializer receipts). The conversion must be
  specified against 051/054 fixtures, not assumed.
- **Risk**: Moving lease authority to the remote makes every hosting operation
  depend on the remote runtime service being reachable and migrated; an
  unreachable service means a `lease_authority_unavailable` refusal rather
  than a controller-local fallback.
- **Risk**: A lease or hold that is too short can expire under a slow but live
  holder, turning a successful operation into `effect_unknown`; specification
  must set lease renewal so a live holder does not lose it.
- **Risk**: The hosting modules are under active change by another work
  stream; specification must be scheduled after their current work lands.
- **Assumption**: Projects on one remote share only the public edge and host
  pools; they do not share containers, volumes, or secrets.
- **Assumption**: The remote control plane can be migrated with the standard
  remote runtime migrate/repin procedure before this ships to a production
  remote.

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
- [x] The latest readiness review verdict is `PASS` (root review of the final edits; see Final Validation).

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
