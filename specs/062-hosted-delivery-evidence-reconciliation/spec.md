# Feature Specification: Hosted Delivery Evidence Reconciliation

**Feature Branch**: `062-hosted-delivery-evidence-reconciliation`

**Created**: 2026-10-09

**Status**: Draft

**Input**: `specs/062-hosted-delivery-evidence-reconciliation/prd.md` (READY FOR SPECKIT; independent GPT-6.1-Sol PASS, round 2, 2026-10-09; feedback 48c3e007, bc28549b, 346491a7, bae5cd4e, 2361e669, 1e3fa2f9, e4333c7b, f3329d32)

## Context

Spec 054 retains an inspectable outcome for every hosted apply and fences a
target after any attempt whose effect is uncertain. The fence is released only
by a person running `retire-delivery`, even when the live runtime already
shows exactly what the attempt deployed. Delivery records are also scoped to
the checkout that submitted them, so another checkout of the same project
cannot inspect or retire them. This feature makes the local record converge to
what the remote actually did, read-only, and lets fences release themselves
when they no longer protect anything. It amends spec 054's identity scope and
adds reconciliation outcomes beside 054's retained outcomes; it changes
nothing else of 054's model.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Lost client, landed deploy converges without a retire (Priority: P1)

An agent's hosted apply loses its local supervisor (process killed, terminal
closed, laptop asleep) while the last remote phase is in flight; the phase
completes on the remote. On the next reconcile or `host apply` for the target,
Sandbox observes the live runtime once and, when it exactly matches what the
attempt asked for, adopts the outcome into the local record. The next apply is
admitted without a manual retire, and a surviving `job-start --wait` waiter
exits by what landed.

**Why this priority**: This is the cycle that cost the most operator time on
2026-10-07/08 (Lenzora, xspeed-hub, alimuzzaman.me): a landed deploy reported
as failed, then a retire-and-retry per interruption.

**Independent Test**: Kill the local client while the last remote phase runs
against a fixture target whose phase then succeeds; run reconcile; verify the
record shows `succeeded` with `adopted_by_observation`, the job stays
`interrupted` with a `succeeded` delivery outcome, and the next apply is
admitted.

**Acceptance Scenarios**:

1. **Given** an attempt whose supervisor was lost after its last remote phase
   completed, **When** the agent runs `delivery inspect`, **Then** the attempt
   is reported as reconcilable and no remote read is made.
2. **Given** the same attempt, **When** the agent reconciles or runs the next
   `host apply`, **Then** the observed source revision and configuration equal
   the attempt's own, every applicable requested-outcome proof (edge route and
   certificate, DNS, initializers) is present, no edge or DNS journal for the
   target is unresolved, and no later attempt exists, so the record shows
   `succeeded` with reconciliation result `adopted_by_observation`, the
   observation time and the evidence used; the original `supervisor_lost`
   fact is retained alongside.
3. **Given** the same attempt, **When** reconciliation adopts it, **Then** the
   job record stays `interrupted` and carries a `succeeded` delivery outcome
   shown in the job view, and the next apply is admitted without a retire.
4. **Given** a `job-start --wait` waiter that survived its supervisor, **When**
   the job ends `interrupted` with an uncertain delivery outcome, **Then** the
   waiter runs one bounded reconciliation (one remote read, at most 15
   seconds) and exits 0 only when the delivery outcome is terminal
   `succeeded`, otherwise 1 with the reconciliation result printed.
5. **Given** the client was lost mid-sequence (an earlier phase ran, later
   phases never started), **When** the agent reconciles, **Then** the result
   is `insufficient_evidence` or `diverged`, the local outcome and the fence
   are unchanged, and the output names the supported exits (observation-only
   recover, or explicit retire).
6. **Given** runtime revision and configuration match but an edge or
   initializer proof is missing or an edge or DNS journal is unresolved,
   **When** the agent reconciles, **Then** the result is
   `insufficient_evidence` and the fence stays.

---

### User Story 2 - Fences release themselves when nothing is left half-applied (Priority: P1)

After a failed attempt whose rollback facts prove nothing is left half-applied,
or whose lack of effect is proven, the next apply is admitted directly with a
one-line notice naming the previous attempt and why no retire was needed.
Genuinely unknown states keep the fence.

**Why this priority**: Every failed attempt fenced the target, including ones
whose rollback completed; one probe deployment took four retire cycles
(feedback bae5cd4e).

**Independent Test**: Produce a source attempt that failed at edge
verification with Compose never started and edge rollback complete; run
`host apply` again and verify admission with the notice. Produce the negative
cases and verify each refuses with its reason.

**Acceptance Scenarios**:

1. **Given** a failed source attempt whose retained phase evidence shows the
   Compose step never started and whose edge rollback recorded complete,
   **When** the agent runs `host apply` for the same target, **Then** it is
   admitted with a notice naming the previous attempt and the release rule
   used, and the release rule and evidence are retained with the attempt.
2. **Given** an image activation whose retained record is terminal `refused`
   with no runtime effect entered and whose edge rollback, if any ran, is
   complete, **When** the agent applies again, **Then** it is admitted with
   the same notice.
3. **Given** a failed attempt with any of: edge rollback `rollback_incomplete`
   or no rollback fact; a source attempt whose Compose step started; an image
   activation that ended `uncertain` or entered runtime effect; a record that
   predates this feature with no phase evidence, **When** the agent applies
   again, **Then** it is refused with a typed result naming the incomplete
   item and the two exits (observation-only recover, explicit retire), and
   nothing is loosened.
4. **Given** the local supervisor died during the build before any remote
   phase started and the pre-attempt revision and configuration were recorded
   at admission, **When** the agent reconciles or applies, **Then** the live
   runtime equals the pre-attempt state, the result is `no_effect_proven`, the
   fence releases, and the next apply is admitted.
5. **Given** a source attempt with any started remote phase (transfer, edge,
   DNS or Compose), **When** reconciled, **Then** it is never
   `no_effect_proven`.

---

### User Story 3 - Any clean checkout on the controller can inspect, retire and reconcile (Priority: P2)

A deploy submitted from a throwaway worktree that has since been deleted can
be inspected, reconciled and retired from the project's main checkout,
because records are keyed by (remote, declared project name, environment).
Refusals never name a command that cannot succeed from the caller's location.

**Why this priority**: Retire from another worktree was impossible and the
refusal told the caller to run the command that could not work (feedback
e4333c7b, f3329d32). It also unblocks feature 060's per-target state.

**Independent Test**: Submit an attempt from worktree A, delete A, then from
checkout B run `delivery inspect`, reconcile and `retire-delivery` for the
request id; verify each finds the record.

**Acceptance Scenarios**:

1. **Given** a delivery submitted from a since-deleted worktree on this
   controller, **When** the agent runs `delivery inspect`, reconcile or
   `retire-delivery` from another clean checkout of the project, **Then** each
   finds the record by (remote, declared project name, environment) and
   request id.
2. **Given** an attempt that is not retirable, **When** the agent retires it
   from another checkout, **Then** the refusal names the reason and a command
   that works from that checkout.
3. **Given** two records with the same declared project name on one remote but
   different hosting declaration identities, **When** either is read, **Then**
   a collision warning is reported.
4. **Given** checkout-scoped records written before this feature, **When** a
   new controller reads them, **Then** they are readable through a one-way,
   reported, resumable conversion that preserves request ids and outcome
   meaning; records without phase evidence stay fenced; new controllers never
   write checkout-scoped records.
5. **Given** an interrupted conversion, **When** it resumes, **Then** it
   completes with no retained outcome lost or duplicated.
6. **Given** a controller that predates this feature, **When** it reads a
   converted record, **Then** it reports the record missing, never as
   success, and its mutating hosting commands against a converted target are
   refused by feature 060's `protocol_too_old` verdict.

---

### User Story 4 - Image activation reconciles from its request-bound record (Priority: P2)

An image activation whose controller was lost is reconciled from the remote's
request-bound terminal record for the attempt's request id, corroborated by
one complete live observation.

**Why this priority**: Image activation already retains request-bound
evidence on the remote; using it removes the manual cycle for image targets
without waiting for remote receipts for source deliveries.

**Independent Test**: For a fixture activation with a `committed` record and a
matching observation, verify adoption; with `recovery_no_effect` after
preflight, verify `no_effect_proven`; with a contradicting observation, verify
`diverged`.

**Acceptance Scenarios**:

1. **Given** a remote record `committed` and one complete corroborating
   observation, **When** reconciled, **Then** the outcome is adopted.
2. **Given** a remote record `recovery_no_effect`, including after the remote
   preflight ran with no effect entered, and one complete corroborating
   observation, **When** reconciled, **Then** the result is
   `no_effect_proven`.
3. **Given** either record and a contradicting observation, **When**
   reconciled, **Then** the result is `diverged` and nothing changes.
4. **Given** either record and an unavailable or incomplete observation,
   **When** reconciled, **Then** the result is `insufficient_evidence` or
   `unavailable` and nothing changes.

---

### User Story 5 - Retry the same revision without a new commit (Priority: P3)

After a failed attempt, applying the same source revision again is an
ordinary apply with a new request id linked to the failed one.

**Why this priority**: Pushing an empty commit to get a fresh request id is a
workaround; it is low cost once fences release correctly.

**Independent Test**: After a failed, released attempt, apply the same
revision; verify a new request id linked to the failed one and that replay of
the old request id behaves as before.

**Acceptance Scenarios**:

1. **Given** a failed attempt whose fence is released, **When** the agent
   applies the same revision, **Then** a new request id is issued, linked to
   the failed attempt, and retained history keeps both attempts.
2. **Given** an existing request id, **When** it is replayed, **Then**
   behavior is unchanged from today.

---

### Edge Cases

- Reconcile while the attempt's job is still running under a live supervisor:
  refused `authority_pending`; nothing changes.
- Two clean checkouts reconcile the same attempt at once: exactly one adoption
  is recorded; the other caller sees the same result and writes nothing.
- Live runtime runs a different revision or configuration, or a later attempt
  exists: `diverged` (reason `later_attempt` naming that attempt when
  applicable); no local change; fence stays; output names spec 051's
  settlement path.
- Live observation incomplete (health or topology not ready):
  `insufficient_evidence` listing the missing items; no change.
- Pre-admission reconciliation cannot reach the remote or is slow: gives up
  within 15 seconds, prints its result, and admission behaves exactly as today
  for an uncertain outcome.
- An attempt with no recorded pre-attempt state cannot be `no_effect_proven`.
- An operator redeployed the same revision by hand: adoption still requires
  the attempt's own configuration and no later attempt; otherwise `diverged`
  or `insufficient_evidence`.
- Last outcome terminal and certain: no pre-admission read is made.

## Requirements *(mandatory)*

### Functional Requirements

**Identity (amends spec 054)**

- **FR-001**: Delivery records MUST be keyed by (remote, declared project
  name, environment) and request id, retaining the submitting checkout path
  and the hosting declaration identity as evidence.
- **FR-002**: `delivery inspect`, reconcile and `retire-delivery` MUST find a
  record from any clean checkout of the project on the same controller.
- **FR-003**: Every refusal from these commands MUST name only commands that
  can succeed from the caller's location.
- **FR-004**: The system MUST report a collision warning whenever two records
  share a declared project name on one remote with different hosting
  declaration identities; the declaration identity MUST be used for nothing
  else.
- **FR-005**: Existing checkout-scoped records MUST become readable through a
  one-way, reported, resumable conversion that preserves request ids and
  outcome meaning, shared with feature 060's state conversion under one
  conversion owner; new controllers MUST NOT write checkout-scoped records.
- **FR-006**: A controller that predates this feature MUST NOT report a
  converted record as success; mutating hosting commands from it against a
  converted target MUST be refused through feature 060's `protocol_too_old`
  verdict.

**Reconciliation**

- **FR-007**: The system MUST provide a named, read-only reconciliation of one
  attempt against one exact live observation, with results
  `adopted_by_observation`, `no_effect_proven`, `insufficient_evidence`,
  `diverged`, `authority_pending` and `unavailable`.
- **FR-008**: Only `adopted_by_observation` and `no_effect_proven` MAY write
  the local record; every other result MUST leave the local outcome and the
  fence unchanged.
- **FR-009**: Reconciliation MUST make zero remote writes in every result
  class.
- **FR-010**: Adoption MUST require all of: observed source revision and
  configuration equal to the attempt's own retained ones; every applicable
  requested-outcome proof (edge route and certificate, DNS, initializers;
  spec 054's success rule) present; no unresolved edge or DNS journal for the
  target (spec 064); no later attempt for the target. Revision and
  configuration equality alone MUST NOT adopt success.
- **FR-011**: A later attempt for the target MUST yield `diverged` with reason
  `later_attempt` naming it; a missing proof or unresolved journal MUST yield
  `insufficient_evidence`.
- **FR-012**: For source deliveries, `no_effect_proven` MUST require both
  retained attempt evidence that no transfer, edge, DNS or Compose phase
  started on the remote, and one live observation equal to the revision and
  configuration recorded at the attempt's admission; otherwise
  `insufficient_evidence`. An attempt without recorded pre-attempt state MUST
  NOT be proven.
- **FR-013**: For image deliveries, reconciliation MAY use the remote's
  request-bound terminal record for the attempt's request id: `committed`
  adopts, `recovery_no_effect` proves no effect (including after a preflight
  that recorded no effect entering), each only after one complete live
  observation corroborates it; a contradicting observation MUST yield
  `diverged`; an unavailable or incomplete observation MUST yield
  `insufficient_evidence` or `unavailable`.
- **FR-014**: A reconcile request while the attempt's job is running under a
  live supervisor MUST be refused `authority_pending`.
- **FR-015**: Concurrent reconciles of one attempt MUST record exactly one
  adoption; the other caller MUST report the same result and write nothing.
- **FR-016**: On `diverged`, the output MUST name spec 051's settlement path;
  on adoption, the environment's recorded revision MUST change only through
  spec 051's state path, attributed to the reconciliation.
- **FR-017**: An adopted record MUST retain the original transport fact (for
  example `supervisor_lost`), the observation time and the evidence used.
- **FR-018**: `delivery inspect` MUST stay local-only and report an uncertain
  attempt as reconcilable.

**Pre-admission and job outcome**

- **FR-019**: Before admitting an apply for a target whose last outcome is
  uncertain, the system MUST by default run one reconciliation: one remote
  read, bounded at 15 seconds, result printed. It MUST NOT run when the last
  outcome is terminal and certain.
- **FR-020**: When the pre-admission read cannot complete, admission MUST
  behave exactly as it does today for an uncertain outcome.
- **FR-021**: A lost local supervisor MUST be recorded as a transport event;
  the job MUST stay `interrupted` and carry a delivery outcome, written
  through the job owner's supported path and shown in the job view.
- **FR-022**: A surviving `job-start --wait` waiter whose job ends
  `interrupted` with an uncertain delivery outcome MUST run one bounded
  reconciliation (one read, 15 seconds) and exit 0 only when the delivery
  outcome is terminal `succeeded`, otherwise 1 with the result printed.

**Fence release**

- **FR-023**: A fence MUST release automatically on an adopted outcome or on
  proven no effect.
- **FR-024**: A source attempt's fence MUST release automatically only when
  retained phase evidence shows the Compose step never started and the edge
  rollback recorded complete.
- **FR-025**: An image attempt's fence MUST release automatically only when
  its retained activation record is terminal `refused` with no runtime effect
  entered and its edge rollback, if any ran, recorded complete, or on
  `committed` / `recovery_no_effect` per FR-013.
- **FR-026**: Every other attempt, including pre-feature records without phase
  evidence, MUST keep its fence; the refusal MUST name the incomplete or
  missing item and the exits (observation-only recover, explicit retire).
- **FR-027**: Release MUST rest only on retained facts recorded by the
  rollbacks and phases themselves, never on the absence of a failure record.
- **FR-028**: The admitted apply's output MUST include a one-line notice
  naming the previous attempt and the release rule; the rule and evidence
  MUST be retained with the attempt.

**Retry**

- **FR-029**: Applying the same revision after a failed attempt MUST issue a
  new request id linked to the failed one; retained history MUST keep every
  attempt; replay of an existing request id MUST be unchanged.

**Boundaries**

- **FR-030**: Reconciliation MUST have an explicit contract; no consumer may
  read delivery or job state files directly.
- **FR-031**: `host recover` MUST remain observation-only; this feature MUST
  NOT mutate the remote.

### Key Entities

- **Delivery identity**: (remote, declared project name, environment); with
  request id it names one attempt. Carries the submitting checkout path and
  hosting declaration identity as evidence.
- **Attempt record**: spec 054's retained outcome for one request id, plus
  phase evidence (which remote phases started), rollback facts, the
  pre-attempt revision and configuration recorded at admission, transport
  events, link to a prior failed attempt, and any reconciliation result.
- **Reconciliation result**: one of the six result classes, with observation
  time, the evidence used, missing items or divergence reason, and the
  release rule applied if any.
- **Live observation**: one read of the target's runtime: source revision,
  configuration, health, topology and requested-outcome proofs; for images,
  also the request-bound terminal record.
- **Fence**: the admission block spec 054 places on a target after an
  uncertain attempt; released only by the rules in FR-023..FR-026 or by an
  explicit retire.
- **Job delivery outcome**: the delivery outcome carried beside a durable
  job's own state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In ten runs where the client is killed while the last remote
  phase is in flight and that phase succeeds, ten records converge to
  `succeeded` with `adopted_by_observation` on the next reconcile or apply;
  ten jobs stay `interrupted` with a `succeeded` delivery outcome; a
  surviving waiter exits 0; zero manual retires.
- **SC-002**: In runs where the client is killed after an earlier phase
  started but before the last, zero local outcomes change and every fence
  stays.
- **SC-003**: 100% of source attempts with a started remote phase are never
  `no_effect_proven`; a source attempt killed during build with live state
  equal to the pre-attempt state is `no_effect_proven` and the next apply is
  admitted.
- **SC-004**: An image activation with a `recovery_no_effect` record after
  preflight and a complete corroborating observation is `no_effect_proven`;
  the same record with an unavailable observation changes nothing.
- **SC-005**: An attempt with matching revision and configuration but a
  pending edge proof or unresolved journal is `insufficient_evidence` in 100%
  of runs, and its fence stays.
- **SC-006**: Each automatically released case (source: Compose never started,
  edge rollback complete; image: refused before runtime effect, edge rollback
  complete) is followed by an admitted apply with zero retires; each fenced
  case (incomplete or missing rollback fact, Compose started, image
  `uncertain` or runtime effect entered, pre-feature record without phase
  evidence) is followed by a refusal naming the reason.
- **SC-007**: Inspect, reconcile and retire succeed from a second clean
  checkout for 100% of new records and 100% of converted older records; 100%
  of refusals name a command that works from the caller's location.
- **SC-008**: 100% of same-name, different-declaration records produce a
  collision warning.
- **SC-009**: An interrupted conversion resumes to completion with zero
  retained outcomes lost or duplicated.
- **SC-010**: An image reconciliation contradicted by the live observation
  yields `diverged` in 100% of runs.
- **SC-011**: Pre-admission reconciliation makes exactly one remote read and
  finishes within 15 seconds in every run, including unreachable-remote runs.
- **SC-012**: Reconciliation makes zero remote writes in every result class,
  verified by a remote-side write audit over the acceptance run.
- **SC-013**: With insufficient or diverged evidence, a running job, or a
  later attempt, zero local outcomes change and the fence is unchanged; two
  concurrent reconciles of one attempt record exactly one adoption.

## Assumptions

- The live runtime exposes source revision and configuration precisely
  enough for an exact match for both delivery kinds, as today's exact-runtime
  receipt repair already relies on.
- Project-side wrappers (for example Lenzora's `./deploy`) want
  delivery-truth exit codes and read the transport event separately; their
  launcher contract is unchanged.
- Delivery records stay under the submitting controller's home;
  reconciliation from a different controller is out of scope.
- Implementation follows spec 054's open live acceptance (T040-T047) and is
  not interleaved with it.
- Feature 060 owns per-target state partitioning; the identity conversion
  here is part of the same conversion, with one owner and fixture set chosen
  at plan time.
- Baseline behavior from commits `543f179`, `d2d123c`, `ed3cf56` and
  `66d35ee` is not repeated.

## Out of Scope

- Request-scoped apply logs and failure phase/step reporting (follow-up
  "apply log identity and failure steps").
- Remote operation receipts for source deliveries (follow-up; needs feature
  061 and a runtime migration).
- Automatic Compose rollback on failure for either delivery kind (follow-up).
- Mutating recovery or repair of a diverged remote (spec 051).
- Per-target locking and concurrent applies (feature 060), teardown,
  controller/runtime compatibility (feature 061), edge and DNS change
  correctness (feature 064).
