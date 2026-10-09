# Product Requirements Draft: Hosted Delivery Evidence Reconciliation

**Status**: Refined

**Created**: 2026-10-08

**Last Refined**: 2026-10-09

**Input**: "After a hosted apply loses its client, times out, or fails, the local record should converge to what the remote actually did: project-scoped delivery identity, read-only reconciliation from remote evidence, automatic release of fences that no longer protect anything, request-scoped apply logs, and typed phase on every failure"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); revised 2026-10-09 by a Claude Opus 5.5 root applying one independent Opus readiness review (verdict `REOPEN`) and the Fable product decisions delegated by the user. Evidence: the feedback backlog, spec 054 ledger, `docs/remote-hosting.md`, `docs/delivery-outcomes.md`, `docs/remote-job-runtime.md`, `docs/roadmap/2026-10-08-next-features.md`, and `origin/latest` commits `543f179`, `d2d123c`, `ed3cf56`, `66d35ee`.

**Final Validation**: `PENDING` — fresh independent readiness review of this revision

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
`sb host retire-delivery` with the previous request id, even when the live
runtime already shows exactly what the attempt deployed. A hosted apply is a
local controller job that drives the remote phases one by one over SSH; when
the controller is lost, only the phase already in flight on the remote can
finish, and nothing afterwards checks whether the remote ended in the state
the attempt asked for. On 2026-10-07 and 2026-10-08 the Lenzora, xspeed-hub
and alimuzzaman.me deploys produced this sequence repeatedly:

- A local apply job ended `interrupted` (`supervisor_lost`) while its last
  remote phase completed; the launcher exited 1 with "Deploy Failure", so a
  landed deploy was reported as failed (feedback `48c3e007`).
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
- `host apply` refused `unproven_staged_revision` after a cutover had stopped
  the host's containers (feedback `2361e669`), and `retire-delivery` could not
  clear that state when the staged and recorded revisions were equal and the
  runtime was unverified (feedback `1e3fa2f9`); `ed3cf56` and `66d35ee` made
  retire release an unproven staged revision, but the cycle still needs a
  person.
- A retire for a stale attempt from another worktree could not succeed,
  because the delivery record lives under the submitting checkout's project
  scope and the retire looked under the current one; the refusal told the
  caller to run exactly the command that cannot work (feedback `e4333c7b`).
  `delivery inspect` has the same scope: a succeeded operation is `missing`
  from any other clean checkout of the same project (feedback `f3329d32`).
- The apply log carries no request identity (feedback `0f32b507`) and an
  image prepare failed with a bare `helper_failed` whose trace refused
  `trace_contract_invalid` (feedback `a0db2173`). Both are real, and both are
  scoped to the follow-up "apply log identity and failure steps" (see
  Non-Goals).

Each of these was closed with a one-off fix or a workaround (push a new
revision, retire by hand, find the original worktree in `job-status`). The
pattern behind them is one gap: the live runtime shows what happened, the
local record is what the caller sees, and nothing reconciles the two unless a
person does. Sandbox already repairs the local receipt when exact runtime
evidence is complete, but only inside the apply that produced it, never after
the client is gone. That costs an operator a retire-and-retry cycle per
interrupted deploy today, and will cost more as feature 060 lets several
targets apply at once.

## Users and Desired Outcomes

- **Agent deploying a hosted target**: after a lost client, a timeout, or a
  failed attempt whose rollback completed, the next apply proceeds without a
  manual retire, and the exit status of the deploy command reflects what
  landed on the remote, not whether the local supervisor survived.
- **Agent on a different checkout of the same project on the same
  controller**: can inspect, retire and reconcile any delivery of the project
  from any clean checkout on that controller, not only the worktree that
  submitted it.
- **Owner of a hosted production site**: nothing in this feature loosens a
  fence while the remote's state is genuinely unknown; reconciliation adopts
  an outcome only when the live runtime exactly matches what the attempt
  itself asked for and no later attempt exists.

## Goals

- Delivery identity is the tuple (remote, declared project name,
  environment), matching how hosted state is already keyed. Inspect, retire
  and reconcile for a delivery work from any clean checkout on the same
  controller, and refusals never name a command that cannot succeed from the
  caller's location.
- A read-only reconciliation exists: given a retained local outcome that is
  uncertain (interrupted, lost client, observation timeout, unknown effect),
  Sandbox observes the live runtime once and, when the observed source
  revision and configuration exactly equal the attempt's own retained ones and
  no later attempt exists for the target, adopts the outcome into the local
  record as `adopted_by_observation`, not as a new delivery.
- Fences release themselves when they no longer protect anything: a terminal
  attempt whose edge rollback and Compose rollback both completed (or whose
  Compose step never started), or whose outcome was adopted by observation, no
  longer blocks the next apply. Only genuinely unknown states keep the fence,
  and for those `retire-delivery` stays the explicit exit.
- Compose rollback retains its own complete-or-incomplete fact, as edge
  rollback already does, so automatic fence release never rests on the edge
  alone.
- A lost local supervisor never by itself makes a delivery a failure. The job
  record stays `interrupted`, gains a delivery outcome, and the deploy
  command's exit status follows the delivery outcome once that outcome is
  terminal.
- Retrying the same source revision after a failed attempt is an ordinary
  apply with a new request id linked to the failed one; no new commit is
  needed. Replay of an existing request id behaves as today.

## Non-Goals

- **Follow-up "apply log identity and failure steps"**: request-scoped apply
  logs with a header and terminal marker, phase-only log reads, an environment
  log index, and a phase and step on every delivery and image-staging failure
  with field-level trace contract errors (feedback `0f32b507`, `a0db2173`),
  including the `helper_failed` acceptance fixtures.
- **Follow-up "remote operation receipts"**: a remote-retained, operation-bound
  receipt for source deliveries that reconciliation can adopt directly. It
  needs feature 061's runtime compatibility and a runtime migration; until
  then adoption is by exact live observation only.
- Reconciliation from a different controller machine than the one that
  submitted the delivery. Delivery records stay under the submitting
  controller's home in this feature.
- Changing what spec 054 retains or how it classifies outcomes. This feature
  consumes 054's retained outcomes and adds reconciliation outcomes beside
  them; it amends 054's identity scope and nothing else of its model.
- Mutating recovery. `host recover` stays observation-only; reconciliation
  writes only the local record. Repairing a diverged remote (containers on
  revision B, record on A) by changing the remote is spec 051's settlement
  work.
- Per-target locking, concurrent applies, teardown (feature 060).
- Compatibility between controller and runtime revisions (feature 061).
- Edge and DNS change correctness (feature 064); this feature consumes the
  edge rollback result and unfences only when every rollback fact is complete.
- Changing the launcher contract of project-side wrappers such as Lenzora's
  `./deploy`; this feature changes what Sandbox reports so the wrappers can
  trust it.

## Product Scenarios

### Scenario 1 — Client lost after the last remote phase, remote succeeded

- **Starting state**: A hosted apply's last remote phase is in flight or
  finished when the local supervisor dies (process killed, terminal closed,
  laptop asleep). The phase completes on the remote.
- **User action**: The agent runs `delivery inspect`, an explicit reconcile,
  or the next `host apply` for the target.
- **Expected outcome**: The live runtime's source revision and configuration
  equal the attempt's own, and no later attempt exists, so the local record
  shows the attempt as `succeeded` with reconciliation result
  `adopted_by_observation`, the observation time, and the evidence used. The
  job stays `interrupted` with a `succeeded` delivery outcome. The next apply
  is admitted without a retire. The original `supervisor_lost` fact is
  retained alongside, not erased.

### Scenario 2 — Client lost mid-sequence (negative)

- **Starting state**: The local supervisor dies while an earlier remote phase
  is running, before the controller wrote a terminal outcome; the later
  phases were never started.
- **User action**: The agent reconciles, or runs the next `host apply`.
- **Expected outcome**: The observation does not match the attempt's revision
  and configuration, so the result is `insufficient_evidence` or `diverged`.
  The local outcome and the fence are unchanged, and the output names the
  supported exits (observation-only recover, or explicit retire).

### Scenario 3 — Failed attempt, rollback complete, no fence

- **Starting state**: An apply failed at edge verification; the edge rollback
  and the Compose rollback both recorded `rollback_complete`, or the edge
  rollback completed and the Compose step had not started.
- **User action**: The agent runs `host apply` again for the same target and
  revision.
- **Expected outcome**: The apply is admitted directly. Its output includes a
  one-line notice naming the previous terminal attempt and why no retire was
  required. The new attempt has a new request id linked to the failed one.

### Scenario 4 — Failed attempt, rollback incomplete, fence stays (negative)

- **Starting state**: An apply failed and its edge or Compose rollback
  recorded `rollback_incomplete`, or the Compose rollback recorded no fact.
- **User action**: The agent runs `host apply` again.
- **Expected outcome**: Refused with a typed result naming the incomplete
  rollback item and the two supported exits: an observation-only recover to
  gather evidence, or an explicit retire. Nothing is loosened.

### Scenario 5 — Reconciliation with incomplete observation (negative)

- **Starting state**: A local attempt is `interrupted`, and the live
  observation is incomplete (for example, health or topology not ready).
- **User action**: The agent asks to reconcile.
- **Expected outcome**: The result is `insufficient_evidence`, lists the
  missing items, and makes no change to the local outcome or the fence. The
  command is read-only on the remote.

### Scenario 6 — Reconcile while the job is still running (negative)

- **Starting state**: The apply job for the attempt is still running under a
  live supervisor.
- **User action**: The agent asks to reconcile the attempt.
- **Expected outcome**: Refused with `authority_pending`; the running job
  remains the authority for its own outcome, and nothing changes.

### Scenario 7 — Inspect, retire and reconcile from another checkout

- **Starting state**: A deploy was submitted from a throwaway source worktree
  on this controller that has since been deleted.
- **User action**: From the project's main checkout, the agent runs
  `delivery inspect` with the request id, then reconcile or
  `retire-delivery`.
- **Expected outcome**: All find the record by (remote, declared project
  name, environment). The retire succeeds if the attempt is retirable; if not,
  the refusal names the reason and a command that works from this checkout.

### Scenario 8 — Two checkouts reconcile the same attempt (negative)

- **Starting state**: Two clean checkouts of the project on one controller
  reconcile the same uncertain attempt at the same time.
- **User action**: Both run reconcile.
- **Expected outcome**: Exactly one adoption is recorded. The other caller
  sees the attempt as already reconciled, with the same result, and writes
  nothing.

### Scenario 9 — Observation contradicts the attempt (negative)

- **Starting state**: The live runtime runs a different source revision or
  configuration than the attempt asked for, or a later attempt exists for the
  target.
- **User action**: The agent reconciles.
- **Expected outcome**: The result is `diverged` (or, for a later attempt,
  a refusal naming that attempt). No local record changes, the fence stays,
  and the output names spec 051's settlement path. When adoption does
  succeed, the environment's recorded revision is updated only through spec
  051's state path, attributed to the reconciliation.

### Scenario 10 — Pre-admission reconciliation cannot reach the remote (negative)

- **Starting state**: The target's last outcome is uncertain and the remote
  is unreachable or slow.
- **User action**: The agent runs `host apply`.
- **Expected outcome**: The bounded pre-admission read gives up within its
  bound, its result is printed, and admission behaves exactly as it does
  today for an uncertain outcome (refused with the existing guidance).

### Scenario 11 — Old controller or old records (negative)

- **Starting state**: A controller that predates this feature inspects a
  record written with project-scoped identity, or a new controller reads a
  checkout-scoped record written before the feature.
- **User action**: Any delivery command.
- **Expected outcome**: The old controller gets a typed limitation naming the
  missing capability. Old records remain readable by new controllers through
  the conversion; new controllers never write checkout-scoped records.

## Proposed Product Behavior

- Delivery records are keyed by (remote, declared project name,
  environment) and request, with the submitting checkout path and the hosting
  declaration's identity retained as evidence. The declaration identity is
  used only to warn when two projects with the same declared name collide on
  one remote. Existing checkout-scoped records become readable through a
  one-way conversion that preserves every retained outcome; the conversion is
  reported and resumable, and is shared with feature 060's state conversion.
- Reconciliation is a named read-only check of one attempt against one exact
  live observation. Results: `adopted_by_observation`, `no_effect_proven`,
  `insufficient_evidence`, `diverged`, `authority_pending`, `unavailable`.
  Only `adopted_by_observation` and `no_effect_proven` write the local record.
  Adoption requires the observed source revision and configuration to equal
  the attempt's own retained ones and no later attempt to exist for the
  target.
- Reconciliation runs on request, and by default once before admission of
  the next apply for a target whose last outcome is uncertain: one remote
  read, bounded at 15 seconds, its result printed. An unreachable remote
  leaves admission as it is today.
- Fence release is a product rule, not an operator action: edge and Compose
  rollback both complete (or Compose never started), proven no effect, or
  adopted outcome releases the fence; everything else keeps it. The rule and
  the evidence it used are shown in the next apply's output and retained with
  the attempt.
- A lost local supervisor is recorded as a transport event. The job stays
  `interrupted` and carries a delivery outcome, shown in the job view. The
  exit status of `job-start` and job wait follows the delivery outcome when
  it is terminal, and the job state otherwise.
- Retry of the same revision gets a new request id linked to the failed
  attempt; retained history keeps every attempt. Replay of a request id is
  unchanged.

## Constraints and Dependencies

- Spec 054 is the owner of retained delivery outcomes and admission; its
  ledger still has its live acceptance (T040–T047) and regression tasks open.
  This feature amends 054's identity scope and adds reconciliation outcomes;
  specification should state the amendment explicitly and must not fork 054's
  model. Implementation follows 054's acceptance, not interleaved with it.
- Spec 051 owns immutable activation, recovery and settlement of a diverged
  remote, and the environment's recorded revision. Reconciliation stops at
  `diverged`, names 051's path, and changes the recorded revision only through
  051's state port.
- Feature 060 partitions delivery state per target and converts existing
  records; the identity change here is part of the same conversion, with one
  conversion owner and one fixture set chosen at plan time, as the
  2026-10-08 roadmap states.
- Today's exact-runtime reconciliation, which repairs only the local receipt
  when the observed revision and configuration match and health and topology
  are ready, is the baseline this feature extends past the end of the apply.
- Commits `543f179` (receipt refresh on readiness timeout), `d2d123c` (sleep
  assertion and budget renewal), `ed3cf56` and `66d35ee` (retire releases an
  unproven staged revision) are baseline behavior, not work this feature
  repeats.
- A hosted apply is a local controller job driving remote phases over SSH;
  only the remote phase in flight when the controller is lost can complete.
  The remote keeps no per-operation receipt for source deliveries, so
  adoption uses exact live observation in this feature.
- Durable job records (`docs/remote-job-runtime.md`) own job state: a lost
  supervisor becomes `interrupted`, never `succeeded`. The delivery outcome is
  added beside the job state through the job owner's supported path, never by
  a direct write.
- Constitution and module boundaries: reconciliation has an explicit contract;
  no consumer reads delivery or job state files directly.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Scope | Request-scoped apply logs and failure phase/step reporting move to follow-up "apply log identity and failure steps" | Keeps this feature on reconciliation and fencing; the log and trace work is independent | Fable decision (delegated by user), 2026-10-09 |
| Delivery identity scope | (remote, declared project name, environment); checkout path and hosting declaration identity retained as evidence, the latter only for collision warnings | Matches how hosted state is already keyed; the worktree that ran a deploy is incidental and may be gone | Fable decision (delegated by user), 2026-10-09 |
| Controller scope | Any clean checkout on the same controller; other controllers out of scope | Delivery records live under the submitting controller's home | Fable decision (delegated by user), 2026-10-09 |
| Source of truth for reconciliation | Exact live observation bound to the attempt's own digests; remote operation receipts when a runtime provides them (follow-up) | The remote retains no operation receipt for source deliveries today; exact observation extends the existing receipt repair without guessing | Fable decision (delegated by user), 2026-10-09 |
| Adoption guard | `adopted_by_observation` only when observed revision and configuration equal the attempt's and no later attempt exists for the target | Prevents adopting a runtime another attempt produced | Fable decision (delegated by user), 2026-10-09 |
| Compose rollback fact | Compose rollback retains its own `rollback_complete` / `rollback_incomplete` fact (in scope) | Rollback completion is recorded for the edge only today; fence release must not rest on the edge alone | Fable decision (delegated by user), 2026-10-09 |
| Fence release | Automatic when edge and Compose rollback facts are both complete (or Compose never started), on proven no effect, or on adoption; otherwise explicit retire | A fence that protects nothing is pure cost; one that protects an unknown state must stay | Fable decision (delegated by user), 2026-10-09 |
| Reconciliation trigger | On request, and by default once pre-admission when the last outcome is uncertain: one read, 15 s bound, result printed; unreachable remote = today's behavior | The next apply is where the cost lands; a bounded check there removes the manual cycle | Fable decision (delegated by user), 2026-10-09 |
| Lost client semantics | Job stays `interrupted`; job record and view gain a delivery outcome; `job-start` and wait exit status follow the delivery outcome when terminal, else the job state | Keeps the job runtime's rule that a lost supervisor is never `succeeded` while wrappers get delivery-truth exit codes | Fable decision (delegated by user), 2026-10-09 |
| Diverged remote | Report `diverged`, change nothing, name spec 051 | Reconciliation never mutates the remote | Fable decision (delegated by user), 2026-10-09 |
| Same-revision retry | New request id linked to the failed attempt; replay unchanged | Pushing an empty commit to get a request id is a workaround, not a product path | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None. Pre-admission reconciliation is on by default (decided above).

## Acceptance Outcomes

- In ten runs where the local client is killed while the last remote phase is
  in flight and that phase succeeds, ten local records converge to
  `succeeded` with `adopted_by_observation` on the next command for the
  target; ten job records stay `interrupted` with a `succeeded` delivery
  outcome, and the deploy command exits 0; zero manual retires.
- In runs where the client is killed before the last remote phase starts,
  zero local outcomes change and every fence stays.
- A failed attempt whose edge and Compose rollbacks are both complete is
  followed by an admitted apply with zero retire commands in between; a failed
  attempt with any incomplete or missing rollback fact is followed by a
  refusal naming the incomplete item.
- `delivery inspect`, reconcile and `retire-delivery` succeed from a second
  clean checkout on the same controller for 100% of records written after the
  feature, and for 100% of converted older records; 100% of refusals name a
  command that works from the caller's location.
- Pre-admission reconciliation makes exactly one remote read and finishes
  within 15 seconds in every run, including unreachable-remote runs.
- Reconciliation makes zero remote writes in every result class, verified by
  a remote-side write audit over the acceptance run.
- With insufficient or diverged evidence, a running job, or a later attempt,
  zero local outcomes change and the fence is unchanged; two concurrent
  reconciles of one attempt record exactly one adoption.

## Risks and Assumptions

- **Risk**: Automatic fence release misjudges a rollback as complete.
  Mitigation: release only on retained `rollback_complete` facts recorded by
  the edge and Compose rollbacks themselves, never on absence of a failure
  record.
- **Risk**: Exact observation adopts a runtime that matches by coincidence
  (for example, an operator redeployed the same revision by hand).
  Mitigation: adoption requires both revision and configuration to match the
  attempt and no later attempt to exist; anything else is `diverged` or
  `insufficient_evidence`.
- **Risk**: Two projects share a declared name on one remote and collide on
  identity. Mitigation: the hosting declaration identity is retained with
  each record and a mismatch is reported as a collision warning.
- **Risk**: Pre-admission reconciliation adds latency to an apply after an
  uncertain outcome. Mitigation: one read, 15-second bound, skipped when the
  last outcome is terminal and certain.
- **Risk**: Converting checkout-scoped records while 060 converts state layout
  doubles conversion risk. Mitigation: one conversion owner and one fixture
  set, decided at plan time.
- **Assumption**: The live runtime exposes its source revision and
  configuration precisely enough for an exact match for both source and image
  deliveries, as the existing exact-runtime receipt repair already relies on.
- **Assumption**: Project-side wrappers want delivery-truth exit codes and will
  read the transport event separately.

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
