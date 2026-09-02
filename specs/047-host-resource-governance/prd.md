# Product Requirements Draft: Host Resource Governance

**Status**: Validated

**Created**: 2026-08-30

**Last Refined**: 2026-08-31

**Input**: "Provide generic host-wide resource governance so production and host control remain protected while development, tests, CI, previews, builds, and browser workloads safely borrow unused resources through hard boundaries, flexible priority, pressure-aware admission, and bounded preemption."

**Drafting Model**: `gpt-5.6-terra` Medium

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A shared host may run production beside development, tests, CI, previews,
browser work, and builds. Individually valid workloads can collectively exhaust
CPU, memory, processes, I/O, disk, inodes, logs, or build cache. Static limits
alone waste idle resources and do not protect aggregate pools. Permissive
borrowing can cause a production incident and starve the control path needed to
recover it.

Sandbox needs one generic host policy that reserves and proves protected
capacity, lends verified surplus fairly, and reclaims only eligible work before
protected production is materially affected. Today its partial limit,
scheduling, storage, hosting, and diagnostic products do not provide that
host-wide promise.

## Users and Desired Outcomes

- **Host operator**: Defines and inspects a policy that protects host control
  and production through pressure, controller restart, and host reboot.
- **Production owner**: Relies on an approved protected envelope and priority
  over opportunistic contention.
- **Workload owner**: Receives a clear admitted, queued, constrained, paused,
  preempted, rejected, resumed, or terminal result.
- **Release/incident operator**: Places recovery, rollback, migration, backup
  finalization, or production deployment ahead of opportunistic work.
- **Platform maintainer**: Knows whether a host can enforce the requested policy
  before enabling it.

## Goals

- Protect host control, recovery, and approved production from aggregate
  non-production exhaustion.
- Allow eligible opportunistic work to use genuinely spare capacity without
  reducing protected guarantees.
- Combine non-negotiable workload/pool limits with flexible contention,
  admission, throttling, and reclamation behavior.
- Govern CPU, memory, PIDs, I/O, disk, inodes, builds/caches, logs, and runtime
  as one multi-resource policy.
- Make ownership, priority, fairness, decisions, pressure, recovery, and
  terminal outcomes predictable and auditable.
- Fail closed when required capability, ownership, authority, enforcement, or
  fresh observation cannot be proven.
- Recover host control and production before reopening opportunistic work.
- Provide bounded, secret-free reports to operators and workload owners.

## Non-Goals

- Replacing the host OS, container runtime, provider, or deployment product.
- Multi-host availability or adoption of a cluster orchestrator.
- Guaranteeing immediate or uninterrupted execution for opportunistic work.
- Broad automatic deletion of containers, volumes, artifacts, logs, or caches.
- Treating swap, load average, a lease expiry, a stale reading, or one PID as
  sufficient proof of safety or termination.
- Letting repository input grant itself production/control priority or
  unrestricted capacity.
- Defining implementation architecture, settings, protocols, contracts,
  migrations, source changes, or task breakdowns.

## Product Scenarios

### Scenario 1 — Safe borrowing

- **Starting state**: Production is healthy, reserves are intact, and evidence
  shows safely lendable capacity.
- **User action**: An owner submits declared development, test, CI, preview,
  browser, or build work.
- **Expected outcome**: Work runs within its pool and fair owner share, may use
  spare capacity, and remains below protected production in contention.

### Scenario 2 — Production demand rises

- **Starting state**: Opportunistic workloads are active.
- **User action**: Production demand rises or higher-priority protected work is
  admitted.
- **Expected outcome**: Conflicting admission stops, flexible share reduces,
  and only eligible capacity is reclaimed. Each affected workload receives a
  durable, distinct explanation.

### Scenario 3 — Warning and critical pressure

- **Starting state**: A reserve approaches or crosses its protective threshold.
- **User action**: Heavy work requests admission or continues consuming.
- **Expected outcome**: Warning pressure restricts heavy admission and
  concurrency without reacting to one transient sample. Critical pressure
  closes borrowing and uses the published victim policy, grace, and escalation.
  If eligible capacity is insufficient, protected work is not silently selected
  as a victim; recovery remains incomplete and the operator receives bounded
  escalation evidence.

### Scenario 4 — Work violates its envelope

- **Starting state**: A governed workload is active.
- **User action**: It crosses an enforceable resource/lifetime boundary or
  ignores graceful stop.
- **Expected outcome**: The violation remains in the responsible workload or
  pool, production stays protected, and the result is specific and truthful.

### Scenario 5 — Multiple owners compete

- **Starting state**: Demand in one opportunistic class exceeds capacity.
- **User action**: One owner submits enough work to dominate the queue.
- **Expected outcome**: Owner limits, multi-resource fairness, aging, and safe
  backfill prevent starvation and burst gaming.

### Scenario 6 — Build or storage pressure

- **Starting state**: Builds, browsers, artifacts, caches, writable data, or
  logs approach production reserves.
- **User action**: More work or data growth arrives.
- **Expected outcome**: Admission stops before the reserve. Only data with
  proven ownership and non-use may be reclaimed; unknown data is not deleted.

### Scenario 7 — Controller interruption or reboot

- **Starting state**: Protected and opportunistic work may exist.
- **User action**: Governance loses authority, restarts, or the host reboots.
- **Expected outcome**: Hard protection remains. New borrowing stays closed.
  Existing eligible loans lose their flexible share and are constrained,
  drained, or reclaimed through their declared policy. Desired state, live work,
  leases, and ownership reconcile before production recovers and before
  opportunistic work reopens. Unproven stop state remains ambiguous and does not
  authorize replacement work.

### Scenario 8 — Required protection cannot be proved

- **Starting state**: A capability or evidence source is missing, stale,
  contradictory, incomplete, drifted, or unsafe.
- **User action**: An operator enables protection or work requests admission.
- **Expected outcome**: The unsafe apply/admission is refused with the missing
  proof and allowed fallback. Unmanaged or escaped consumption counts as
  unavailable capacity, closes dependent borrowing, and marks certification as
  drifted. Governance reports it but does not adopt or terminate it without
  operator authority. Protection is never silently weakened.

## Proposed Product Behavior

- All managed host workloads resolve to one versioned resource intent covering
  owner, environment class, requests, maxima, lifecycle, priority,
  preemptibility, evidence class, and required host capability.
- Repository and workload declarations are untrusted. Only operator policy can
  grant host-control, protected-production, recovery, or non-preemptible class.
- Host control and production reserves are allocated before opportunistic
  capacity. Non-production borrows surplus and yields under pressure.
- Every workload and aggregate pool has a hard envelope plus flexible
  contention behavior. Hard protection survives controller loss.
- Admission considers current use, commitments, reserves, pressure, production
  health, owner share, priority, and every governed resource. Missing evidence
  means unavailable capacity, not zero use.
- When required evidence is lost after admission, new borrowing closes and the
  borrowed share of eligible non-production work is withdrawn through its
  declared constraint, drain, checkpoint, restart, or cancellation policy.
  Unreconciled work keeps dependent admission closed.
- Normal, caution, critical, and recovery states use sustained thresholds,
  hysteresis, and gradual reopening.
- Priority order is host control and authorized incident recovery, including an
  emergency rollback; production data, ingress, web, queues, and storage;
  production workers; routine production deploy, rollback, migration, and
  backup finalization; interactive development; standard CI; exhaustive CI,
  browser, and build work; previews and cache warming.
- Only explicitly eligible non-production work may be preempted. The least
  disruptive declared drain, checkpoint, restart, or cancellation occurs
  before forced termination unless a hard-safety condition requires it.
- Terminal reports distinguish success, failure, user cancellation,
  supersession, production preemption, pressure eviction, enforced resource
  limit, storage rejection, deadline, and interruption/ambiguity.
- Builds, artifacts, caches, writable data, logs, bytes, and inodes are shared
  host capacity. Cleanup stays scoped, ownership-proven, non-use-proven, and
  independently auditable.
- Operators receive narrow pause, drain, resume, and explanation actions with
  projected impact, reason, scope, and durable receipt.
- Reports expose policy version, capability, pool state, current/committed/
  borrowed resources, pressure, freshness, queues, fairness, decisions,
  terminal reasons, recovery, and evidence limits without secrets.
- The shared-host certification profile supplies minimum protection outcomes.
  Operators may tighten but not silently relax them. A weaker profile is
  explicitly non-certified, cannot admit protected production, and cannot
  claim equivalent protection.
- The certification profile defines the production workload, contention shape,
  duration, sample floor, attribution rules, baseline revision, and authorized
  control action used for latency measurement. It also defines an owner-share
  tolerance and maximum eligible wait; operator profiles may tighten but not
  omit or silently weaken those bounds.

## Constraints and Dependencies

- Protection applies only where required controls and observations are
  continuously verified.
- Missing mandatory capacity, containment, pool, process, memory, storage,
  authority, or freshness proof blocks protected use.
- An optional-control fallback is allowed only when operator-approved and at
  least as protective; otherwise apply/admission fails closed.
- Every governed resource passes either a direct-control test or a documented
  fallback test that proves the same protection outcome. Unsupported controls
  are never silently omitted from certification.
- Storage reclamation, pressure, swap, hosting, job lifecycle, and secret
  authorities stay separate. Governance does not authorize deletion, secret
  access, unbounded diagnostics, or unsafe replay.
- Stateful release, migration, backup, and similar work needs durable ownership
  and fencing before retry. Expiry alone is insufficient.
- Unsafe policy reduction for active work is refused or deferred.
- Host-wide policy preserves project ownership and cannot let one owner operate
  another's work outside operator-owned policy.
- Certification requires live adversarial evidence on a non-production host;
  static configuration is not runtime proof.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Scope | Govern hosting, jobs, development, CI, preview, build, and browser work | Contention crosses product boundaries | Product direction |
| Model | Hard aggregate/workload boundaries plus flexible borrowing | Safety and utilization are both required | User |
| Priority | Host control and authorized incident recovery, including emergency rollback, outrank routine production operations and opportunistic classes | Recovery and customer service come first without letting routine work claim incident priority | User and policy |
| Admission | Unknown/stale/unverified evidence blocks dependent work | Silent weakening invalidates protection | User and policy |
| Active evidence loss | Close new borrowing and withdraw eligible borrowed share through the workload's declared policy; ambiguity keeps dependent admission closed | Existing loans cannot continue on unproven surplus | User and policy |
| Preemption | Only eligible non-production work; protected stateful work never a victim; insufficient reclamation becomes recovery-incomplete escalation | Prevents unsafe recovery actions | User and research |
| Fairness | Owner caps, weighted multi-resource sharing, aging, safe backfill, plus required profile share-tolerance and maximum-wait bounds | Prevents starvation and gaming | Research and certification policy |
| Storage | Stop before reserve; reclaim only ownership/non-use-proven data | Broad deletion is unsafe | Policy and research |
| Recovery | Host control and production recover before opportunistic admission | Protection survives lifecycle events | User and research |
| Reporting | Durable bounded secret-free decision evidence | Outcomes must be explainable safely | Policy and research |
| Unmanaged consumption | Treat as unavailable capacity and certification drift; report but do not adopt or terminate without operator authority | Host-wide protection cannot assume foreign work is harmless or owned | Policy and research |
| Certification minimums | Zero protected-prod OOM/restart/health/auth failure; <10% relative p95 degradation; control p95 <2s; close <=10s; reclaim <=30s; detect drift <=60s; 20 concurrent admissions never oversubscribe | Provides one measurable baseline profiles may tighten | User direction |

## Open Questions

- None. Host-specific values beyond certification minimums belong to a validated
  operator policy profile.

## Acceptance Outcomes

- Under representative opportunistic contention, protected production has zero
  attributable OOM, restart, health, or authentication failures and less than
  10% relative p95 latency degradation from its uncontended baseline.
- The authorized control endpoint remains below two seconds p95 during the same
  contention.
- Sustained protective state closes new opportunistic admission within 10
  seconds. When reclamation is required, eligible borrowed capacity is
  reclaimed within 30 seconds; if the required reserve cannot be restored from
  eligible capacity, the state becomes recovery-incomplete within the same
  bound, keeps borrowing closed, preserves host control, and emits bounded
  escalation evidence. Any unproven terminal state is marked interrupted/
  ambiguous within 30 seconds, keeps dependent admission closed, and never
  authorizes duplicate execution.
- Effective-policy drift is detected within 60 seconds and dependent borrowing
  remains closed until reconciliation.
- Twenty concurrent admission attempts never exceed any declared aggregate
  budget and each queue/refusal explains its limiting resource or policy.
- Memory, PID, I/O, disk, inode, log, artifact, cache, build, controller,
  restart, and reboot pressure tests contain failure and preserve protected
  production/control. Each governed resource passes its direct-control test or
  a documented fallback test proving the same protection outcome.
- Owner competition remains within the validated profile's share tolerance and
  maximum eligible wait; backfill never delays a higher-priority reserved
  workload.
- Controller loss preserves hard limits; restart/reboot does not duplicate
  stateful work and reopens borrowing only after production verification.
- Missing mandatory capability, stale evidence, unsafe shrink, or drift refuses
  the dependent action with a clear reason.
- Every decision and terminal outcome is available to authorized users in a
  bounded, secret-free report with freshness and evidence limits.
- A stricter profile may improve certification targets; a profile below
  certification minimums cannot admit or claim protected production.

## Risks and Assumptions

- **Risk**: Providers and runtimes expose different controls; weak fallback can
  create false confidence.
- **Risk**: Poor calibration can harm production or waste capacity.
- **Risk**: Incorrect lifecycle declarations can lose disposable progress or
  make preemption unsafe.
- **Risk**: Some storage cannot be safely attributed or reclaimed.
- **Risk**: Foreign or escaped workloads may consume capacity that governance
  cannot safely adopt, constrain, or terminate.
- **Risk**: Controller, lease, and reboot ambiguity can duplicate work without
  durable reconciliation.
- **Assumption**: Certification uses representative production load and a
  revision-bound uncontended baseline.
- **Assumption**: Operators maintain ownership, objectives, profile values, and
  escalation contacts; profiles may tighten but not silently weaken minimums.
- **Assumption**: Workload requests are independently constrained and verified,
  not trusted as truthful declarations.
- **Assumption**: Unsupported hosts remain unavailable for the selected
  protected profile rather than receiving an undocumented weaker mode.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
