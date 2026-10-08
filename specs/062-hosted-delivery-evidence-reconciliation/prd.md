# Product Requirements Draft: Hosted Delivery Evidence Reconciliation

**Status**: Refined

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "After a hosted apply loses its client, times out, or fails, the local record should converge to what the remote actually did: project-scoped delivery identity, read-only reconciliation from remote evidence, automatic release of fences that no longer protect anything, request-scoped apply logs, and typed phase on every failure"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); evidence from the feedback backlog, spec 054 ledger, `docs/remote-hosting.md`, `docs/delivery-outcomes.md`, and `origin/latest` commits `543f179`, `d2d123c`, `ed3cf56`, `66d35ee`. No independent readiness review has run.

**Final Validation**: `PENDING` — independent readiness review

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Spec 054 (Recoverable Delivery Outcomes) made every hosted apply leave a
retained, inspectable outcome, and it fenced the target after any attempt
whose effect is uncertain so that the next apply cannot build on an unknown
state. That was the right first step: the fence is what stopped a second
deploy from landing on top of a half-applied one.

The second step is missing. The fence is released only by a human running
`sb host retire-delivery` with the previous request id, and the evidence that
would let Sandbox release it on its own is often already on the remote. On
2026-10-07 and 2026-10-08 the Lenzora, xspeed-hub and alimuzzaman.me deploys
produced this sequence repeatedly:

- A local apply job ended `interrupted` (`supervisor_lost`) right after the
  build phase while the remote delivery kept running and succeeded; the
  launcher exited 1 with "Deploy Failure", so a landed deploy was reported as
  failed (feedback `48c3e007`).
- A controller laptop went into clamshell sleep after every remote phase had
  finished; the readiness poll spent its budget on a dead network, the apply
  rolled back only the edge, and `host status` reported the previous revision
  while every container ran the new one (feedback `bc28549b`; `d2d123c` now
  holds a sleep assertion and renews the budget on wake, but the record still
  diverges when the client is actually gone).
- A readiness timeout left the recovery receipt without the observation it
  had already made, so `host recover` refused `partial_evidence` forever
  (feedback `346491a7`; `543f179` now refreshes the receipt on that path).
- Every failed attempt, including ones whose rollback completed, fenced the
  target; a probe deployment took four cycles of retire, apply (refused with
  `recovery_context_required`), then `job-start` (feedback `bae5cd4e`).
- A retire for a stale attempt from another worktree could not succeed,
  because the delivery record lives under the submitting checkout's project
  scope and the retire looked under the current one; the refusal told the
  caller to run exactly the command that cannot work (feedback `e4333c7b`).
  `delivery inspect` has the same scope: a succeeded operation is `missing`
  from any other clean checkout of the same project (feedback `f3329d32`).
- The apply log is shared by every apply of an environment, carries no request
  id, job id, operation id or source revision, has no terminal marker for the
  whole apply, and its phase markers are pushed out of the bounded read by
  build output (feedback `0f32b507`).
- An image prepare failed with a bare `helper_failed` from one of about twenty
  sites, and the trace that should explain it refused `trace_contract_invalid`
  (feedback `a0db2173`).

Each of these was closed with a one-off fix or a workaround (push a new
revision, retire by hand, find the original worktree in `job-status`). The
pattern behind them is one gap: the remote holds the truth about what
happened, the local record is what the caller sees, and nothing reconciles the
two unless a person does. That costs an operator a retire-and-retry cycle per
interrupted deploy today, and will cost more as feature 060 lets several
targets apply at once.

## Users and Desired Outcomes

- **Agent deploying a hosted target**: after a lost client, a timeout, or a
  failed attempt whose rollback completed, the next apply proceeds without a
  manual retire, and the exit code of the deploy reflects what landed on the
  remote, not whether the local supervisor survived.
- **Agent on a different checkout of the same project**: can inspect, retire
  and recover any delivery of the project from any clean checkout, not only
  the worktree that submitted it.
- **Operator reading an apply log**: can find the lines that belong to one
  request, see where that request started and ended, and tie them to the
  source revision and the job, without timestamp arithmetic.
- **Reviewer of a failed image or source delivery**: always gets a phase and
  step with the failure, and a trace that either explains it or says exactly
  which part of it is invalid.
- **Owner of a hosted production site**: nothing in this feature loosens a
  fence while the remote's state is genuinely unknown; reconciliation only
  ever adopts evidence the remote retained about the operation itself.

## Goals

- Delivery identity is scoped to the project, not to the checkout path that
  ran the command. Inspect, retire, recover and reconcile for a delivery work
  from any clean checkout of the project, and refusals never name a command
  that cannot succeed from the caller's location.
- A read-only reconciliation exists: given a retained local outcome that is
  uncertain (interrupted, lost client, observation timeout, unknown effect),
  Sandbox compares it with the remote's retained receipt for that same
  operation and, when the remote evidence is complete and bound to the
  operation, adopts the remote outcome into the local record as a
  reconciliation, not as a new delivery.
- Fences release themselves when they no longer protect anything: a terminal
  attempt whose rollback completed, or whose remote receipt proves it had no
  effect, or whose reconciliation adopted a complete success, no longer blocks
  the next apply. Only genuinely unknown states keep the fence, and for those
  `retire-delivery` stays the explicit exit.
- A hosted apply that loses its local client continues on the remote as the
  durable operation it already is, and the local job record converges to the
  remote terminal outcome on reconnect or on the next command for that target.
  `supervisor_lost` is a transport fact and never by itself a delivery failure.
- Each apply writes a request-scoped log with a header (request id, operation
  id, job id, source revision, target) and a terminal marker, and the bounded
  log read can return phase markers alone. The shared environment log remains
  as an index of requests.
- Every delivery and image-staging failure carries a phase and a step. A trace
  that fails its contract reports which field failed, and the retained outcome
  is still inspectable without the trace.
- Retrying the same source revision after a failed attempt is an ordinary
  apply with a new attempt identity; no new commit is needed to get a new
  request id.

## Non-Goals

- Changing what spec 054 retains or how it classifies outcomes. This feature
  consumes 054's retained outcomes and adds reconciliation outcomes beside
  them; it amends 054's identity scope and nothing else of its model.
- Mutating recovery. `host recover` stays observation-only; reconciliation
  writes only the local record and only from remote evidence about the same
  operation. Repairing a diverged remote (containers on revision B, record on
  A) by changing the remote is spec 051's settlement work.
- Per-target locking, concurrent applies, teardown (feature 060).
- Compatibility between controller and runtime revisions (feature 061).
- Edge and DNS change correctness (feature 064); this feature only makes an
  edge failure's phase and rollback status visible and unfences on a complete
  rollback.
- Changing the launcher contract of project-side wrappers such as Lenzora's
  `./deploy`; this feature changes what Sandbox reports so the wrappers can
  trust it.

## Product Scenarios

### Scenario 1 — Client lost, remote succeeded

- **Starting state**: A hosted apply is past the build phase when the local
  supervisor dies (process killed, terminal closed, laptop asleep).
- **User action**: The remote delivery finishes. Later the agent runs
  `delivery inspect` or the next `host apply` for the target.
- **Expected outcome**: The local record shows the attempt as `succeeded` by
  reconciliation, with the remote receipt identity it adopted and the time of
  adoption. The job record's terminal state matches the delivery outcome. The
  next apply is admitted without a retire. The original `supervisor_lost`
  fact is retained alongside, not erased.

### Scenario 2 — Failed attempt, rollback complete, no fence

- **Starting state**: An apply failed at edge verification; the edge rollback
  and Compose rollback both recorded `rollback_complete`.
- **User action**: The agent runs `host apply` again for the same target and
  revision.
- **Expected outcome**: The apply is admitted directly. Its output includes a
  one-line notice naming the previous terminal attempt and why no retire was
  required. The new attempt has a distinct attempt identity under the same
  source revision.

### Scenario 3 — Failed attempt, rollback incomplete, fence stays (negative)

- **Starting state**: An apply failed and its rollback recorded
  `rollback_incomplete`.
- **User action**: The agent runs `host apply` again.
- **Expected outcome**: Refused with a typed result naming the incomplete
  rollback item and the two supported exits: an observation-only recover to
  gather evidence, or an explicit retire. Nothing is loosened.

### Scenario 4 — Reconciliation with incomplete remote evidence (negative)

- **Starting state**: A local attempt is `interrupted` and the remote retains
  only a partial receipt for that operation (no runtime observation).
- **User action**: The agent asks to reconcile.
- **Expected outcome**: The reconciliation result is `insufficient_evidence`,
  lists the missing fields, and makes no change to the local outcome or the
  fence. The command is read-only on the remote.

### Scenario 5 — Inspect and retire from another checkout

- **Starting state**: A deploy was submitted from a throwaway source worktree
  that has since been deleted.
- **User action**: From the project's main checkout, the agent runs
  `delivery inspect` with the request id, then `retire-delivery`.
- **Expected outcome**: Both find the record by project identity. The retire
  succeeds if the attempt is retirable; if not, the refusal names the reason
  and a command that works from this checkout.

### Scenario 6 — Request-scoped apply log

- **Starting state**: Three applies have run on one environment today, one of
  them with a long build.
- **User action**: The operator reads the apply log for one request with
  phase markers only.
- **Expected outcome**: The read returns that request's header (request,
  operation, job, revision, target), its phase markers with exit status and
  timing, and its terminal marker, within the bounded read, regardless of how
  much build output the apply produced.

### Scenario 7 — Failure always has a step

- **Starting state**: An image prepare fails inside the remote helper.
- **User action**: The agent reads the job result and inspects the trace.
- **Expected outcome**: The result names the phase and step (value-free). The
  trace is inspectable; if its contract is violated, the response names the
  violated field and still returns the retained outcome.

### Scenario 8 — Record and remote disagree after reconciliation finds success elsewhere (negative)

- **Starting state**: The remote receipt for the operation proves success at
  revision B, but `host status` observes the environment's recorded revision
  as A because the record was never updated.
- **User action**: The agent reconciles.
- **Expected outcome**: The local delivery outcome becomes `succeeded` by
  reconciliation and the recorded environment revision is updated from the
  same receipt, with both changes attributed to the reconciliation. If the
  observed runtime does not match the receipt, the result is `diverged`, the
  record is not changed, and the output names spec 051's settlement path.

### Scenario 9 — Old controller or old runtime (negative)

- **Starting state**: A controller that predates this feature inspects a
  record written with project-scoped identity, or a new controller targets a
  remote whose runtime cannot serve operation receipts for reconciliation.
- **User action**: Any delivery command.
- **Expected outcome**: A typed limitation naming the missing capability.
  Old records remain readable by new controllers; new controllers never write
  checkout-scoped records.

## Proposed Product Behavior

- Delivery records are keyed by project identity (the declared project and
  its hosting declaration), environment, remote and request, with the
  submitting checkout path retained as evidence only. Existing checkout-scoped
  records are readable through a one-way conversion that preserves every
  retained outcome; the conversion is reported and resumable, as feature 060's
  state conversion is.
- Reconciliation is a named read-only query against the remote's retained
  receipt for one operation. It produces a reconciliation outcome with one of
  a small set of results (adopted success, adopted failure, no effect proven,
  insufficient evidence, diverged, unavailable) and writes the local record
  only for the adopted and no-effect cases. It runs on request and, bounded,
  before admission of the next apply for a target whose last outcome is
  uncertain.
- Fence release is a product rule, not an operator action: complete rollback,
  proven no effect, or adopted success releases the fence; everything else
  keeps it. The rule and the evidence it used are shown in the next apply's
  output and retained with the attempt.
- The local job supervisor's loss is recorded as a transport event; the job's
  terminal state is set from the delivery outcome when it becomes known, and
  wrappers can rely on exit status reflecting delivery, with the transport
  event available for diagnosis.
- Apply logs are per request with a header and terminal marker; the
  environment log indexes requests; log reads accept a request selector and a
  phase-only mode and stay bounded.
- Failures from remote helpers carry phase and step; trace validation reports
  the failing field; CLI and MCP share the shapes.
- Retry of the same revision gets a new attempt identity derived from the
  request and an attempt counter; retained history keeps every attempt.

## Constraints and Dependencies

- Spec 054 is the owner of retained delivery outcomes and admission; its
  ledger still has its live acceptance (T040–T047) and regression tasks open.
  This feature amends 054's identity scope and adds reconciliation outcomes;
  specification should state the amendment explicitly and must not fork 054's
  model. Implementation should follow 054's acceptance, not interleave with it.
- Spec 051 owns immutable activation, recovery and settlement of a diverged
  remote; reconciliation stops at `diverged` and names 051's path.
- Feature 060 partitions delivery state per target and converts existing
  records; the identity change here must be part of the same conversion, so
  060 and 062 are sequenced together (identity rule first, partition second,
  or one conversion that does both).
- Commits `543f179` (receipt refresh on readiness timeout), `d2d123c` (sleep
  assertion and budget renewal), `ed3cf56` and `66d35ee` (retire releases an
  unproven staged revision) are baseline behavior this feature builds on, not
  work it repeats.
- The remote's retained operation receipt is the evidence source; if a runtime
  predates receipts for an operation class, reconciliation reports
  `unavailable` for it rather than guessing.
- Durable job records (`docs/remote-job-runtime.md`) own job terminal state;
  the delivery-to-job convergence must go through the job owner's supported
  path, not a direct write.
- Constitution and module boundaries: reconciliation is a new service with an
  explicit contract; no consumer reads delivery or job state files directly.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Delivery identity scope | Project identity, with checkout path retained as evidence | A deploy belongs to the project; the worktree that ran it is incidental and may be gone | Fable decision (delegated by user), 2026-10-09 |
| Source of truth for reconciliation | The remote's retained receipt for the same operation only | Adopting anything else (container labels, guesses) would turn reconciliation into inference | Fable decision (delegated by user), 2026-10-09 |
| Fence release | Automatic on complete rollback, proven no effect, or adopted success; otherwise explicit retire | A fence that protects nothing is pure cost; one that protects an unknown state must stay | Fable decision (delegated by user), 2026-10-09 |
| Reconciliation trigger | On request, and bounded pre-admission when the last outcome is uncertain | The next apply is where the cost lands; a bounded check there removes the manual cycle | Fable decision (delegated by user), 2026-10-09 |
| Lost client semantics | `supervisor_lost` is a transport event; delivery outcome sets job terminal state | A landed deploy reported as failed is worse than a slow answer | Fable decision (delegated by user), 2026-10-09 |
| Apply log shape | Per-request log with header and terminal marker; environment log indexes | Shared logs without identity cannot be tied to a request by any reader | Fable decision (delegated by user), 2026-10-09 |
| Diverged remote | Report `diverged`, change nothing, name spec 051 | Reconciliation never mutates the remote | Fable decision (delegated by user), 2026-10-09 |
| Same-revision retry | New attempt identity under the same request lineage | Pushing an empty commit to get a request id is a workaround, not a product path | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None blocking. The independent readiness review should confirm that
  pre-admission reconciliation is acceptable as a default rather than opt-in,
  given it adds one bounded remote read to an apply whose previous outcome was
  uncertain.

## Acceptance Outcomes

- In ten runs where the local client is killed after the build phase and the
  remote delivery succeeds, ten local records converge to `succeeded` by
  reconciliation on the next command for the target, and ten job records end
  with a terminal state matching the delivery; zero manual retires.
- A failed attempt with `rollback_complete` is followed by an admitted apply
  with zero retire commands in between; a failed attempt with
  `rollback_incomplete` is followed by a refusal naming the incomplete item.
- `delivery inspect` and `retire-delivery` succeed from a second clean
  checkout of the project for 100% of records written after the feature, and
  for 100% of converted older records.
- For an apply producing more than the bounded line count of build output,
  the phase-only read for that request returns its header, every phase
  marker, and the terminal marker.
- Every `helper_failed` and delivery failure in a run of the supported fixtures
  includes a phase and a step; every trace-contract failure names a field.
- Reconciliation makes zero remote writes in every result class, verified by
  a remote-side write audit over the acceptance run.
- With insufficient or diverged remote evidence, zero local outcomes change
  and the fence is unchanged.

## Risks and Assumptions

- **Risk**: Automatic fence release misjudges a rollback as complete.
  Mitigation: release only on the retained `rollback_complete` fact recorded by
  the rollback itself, never on absence of a failure record.
- **Risk**: Project identity collides across two projects with the same
  declaration name on one remote. Mitigation: identity includes the hosting
  declaration digest and the remote; specification defines the exact tuple
  against 054's model.
- **Risk**: Pre-admission reconciliation adds latency to every apply after an
  uncertain outcome. Mitigation: bounded, single remote read, skipped when the
  last outcome is terminal and certain.
- **Risk**: Converting checkout-scoped records while 060 converts state layout
  doubles conversion risk. Mitigation: one conversion owner and one fixture
  set, decided at specification.
- **Assumption**: The remote retains an operation receipt complete enough to
  adopt (phase receipts, observed runtime revision, rollback status) for
  source and image deliveries; `543f179` moved readiness-timeout receipts in
  that direction.
- **Assumption**: Project-side wrappers want delivery-truth exit codes and will
  consume the transport event separately.

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
