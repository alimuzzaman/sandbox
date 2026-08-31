# Product Requirements Draft: Observation-Only Hosting Recovery

**Status**: Validated

**Created**: 2026-08-31

**Last Refined**: 2026-08-31

**Input**: "Add a first-class observation-only hosting recovery command that binds a failed durable job and exact deployment evidence, reconciles receipts safely, and permits only proven edge-only continuation."

**Drafting Model**: `gpt-5.6-sol` High (root configuration; preferred Terra Medium unavailable for the root)

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A durable hosting apply can fail after some hosting phases have completed. Today an
operator can inspect the job and host, but the ordinary apply command may reserve disk,
create or resolve a remote source directory, and push source before deciding whether a
same-target replay is safe. This makes ordinary apply unsuitable for incident recovery
when the required first step is proof that no source, runtime, initializer, image, or edge
mutation is needed.

Lenzora development recovery is currently blocked by this gap. Its older failed job may
come from a legacy contract and must not gain authority merely because the runtime looks
healthy. Operators need a first-class, fail-closed path that ties a terminal failed job
and its original request to fresh, exact, bounded hosting evidence before doing anything
more than reconciling Sandbox-owned receipts.

## Users and Desired Outcomes

- **Hosting operator**: Determine whether one failed deployment already left the exact
  intended runtime healthy, without risking another source or runtime mutation.
- **Incident owner**: Receive a stable success or refusal class that can be reviewed and
  replayed without treating partial or legacy evidence as permission.
- **Deployment automation**: Bind a recovery attempt to one terminal job, original
  request, exact target, and exact evidence generation instead of inventing a new apply.
- **Release reviewer**: Distinguish observation and receipt reconciliation from an
  explicitly authorized edge-only continuation and from a full deployment.

## Goals

- Provide a supported public hosting recovery action for a failed durable apply.
- Prove the original job, request, clean source, manifest configuration, built images,
  declared topology, per-service state, and phase receipts all describe one exact target.
- Permit observation and Sandbox-owned state reconciliation when the evidence is complete
  and exact, without source, Compose, initializer, migration, image, DNS, or Caddy mutation.
- Permit a separately confirmed edge-only continuation only when the same exact runtime is
  fully proven and the supported edge phase is the only incomplete phase.
- Serialize apply and recovery for one remote/project/environment and reject stale recovery
  generations before any protected action.
- Persist bounded immutable recovery attempts with stable success and refusal classes.

## Non-Goals

- Replaying a legacy job that lacks the current canonical submission and phase evidence.
- Repairing dirty or divergent source, changed manifests, changed images, missing services,
  unhealthy services, failed initializers or migrations, or an incomplete runtime.
- Resetting source, building or recreating containers, rerunning initializers or migrations,
  changing secrets, repairing databases or queues, or selecting a different target.
- Automatically changing DNS, certificates, zone SSL policy, or Caddy configuration.
- Converting a recovery result into deployment, production readiness, or public health proof.
- Replacing ordinary `host apply` for a genuinely changed target.

## Product Scenarios

### Scenario 1 — Reconcile an exact failed apply without mutation

- **Starting state**: A current-contract durable host-apply job is terminal failed, its
  original request and clean source are intact, and fresh bounded evidence proves the exact
  declared images, topology, services, health, source revision, and completed runtime phases.
- **User action**: The operator requests hosting recovery for that exact job, request, target,
  and observed state generation.
- **Expected outcome**: Sandbox records an immutable recovery attempt, reconciles only its
  own hosting receipt to the proven runtime, returns a stable observation-reconciled success,
  and performs no source, runtime, initializer, image, DNS, or Caddy mutation.

### Scenario 2 — Resume only the incomplete edge phase

- **Starting state**: Scenario 1 evidence is complete, the runtime phase is proven ready,
  the edge phase alone is pending, and the current target and desired edge intent exactly
  match the failed attempt.
- **User action**: The operator creates and explicitly confirms a distinct edge-continuation
  attempt that references the successful observation generation.
- **Expected outcome**: Sandbox revalidates every binding under the shared single-flight,
  performs only the already supported edge continuation, observes its result, and records a
  terminal edge-only success or stable failure. No source or runtime phase is entered.

### Scenario 3 — Refuse legacy or incomplete evidence

- **Starting state**: The job has no canonical current submission snapshot, lacks its
  original request identity, has partial output or phase evidence, or predates the recovery
  evidence contract.
- **User action**: The operator requests recovery.
- **Expected outcome**: Sandbox returns a stable legacy-or-incomplete refusal before remote
  source access or any mutation. Runtime health alone never grandfathers the job.

The same refusal applies when a generic durable-job snapshot and a hosting state record
exist but no hosting-specific acceptance record bidirectionally binds them. A generic
command snapshot or an unbound host receipt is evidence, not hosting recovery authority.

### Scenario 4 — Refuse drift or a changed target

- **Starting state**: The checkout is dirty, the source or manifest changed, image identities
  differ, a service is missing or unhealthy, topology diverges, the target changed, or the
  supplied recovery generation is stale.
- **User action**: The operator requests observation recovery or edge continuation.
- **Expected outcome**: Sandbox records a bounded refusal with the decisive safe reason code
  and performs no source push/reset, Compose, initializer, migration, image, DNS, or Caddy
  mutation. It directs a genuinely changed deployment back to the normal reviewed apply path.

### Scenario 5 — Concurrent apply or recovery

- **Starting state**: An apply or recovery already owns the target's hosting operation.
- **User action**: Another caller requests apply or recovery for the same target.
- **Expected outcome**: The second caller cannot observe or write an interleaved generation.
  It receives the existing exact result when its request is an identical replay, otherwise a
  stable busy or generation-conflict refusal.

### Scenario 6 — Ownership, persistence, or edge effect becomes uncertain

- **Starting state**: A caller disconnects, the operation owner dies, a receipt write fails,
  a lock appears stale, or edge work times out with DNS, certificate, or Caddy effects not
  fully known.
- **User action**: The same or another operator asks for recovery.
- **Expected outcome**: Sandbox reports an explicit non-success uncertainty result. Elapsed
  time alone never grants ownership and edge work is never repeated automatically. Recovery
  re-observes the immutable attempt and unchanged hosting generation; reconciliation can
  succeed only after both the attempt and hosting receipt are durably committed.

## Proposed Product Behavior

- The public recovery action requires the failed durable job ID, its original request ID,
  the explicit remote/project/environment target, the expected hosting generation, and a
  caller-supplied replay-safe recovery request ID distinct from the original apply request.
  The recovery identity immutably binds its action, failed job, original request, explicit
  target, expected generation, and permitted effect scope.
- The original job must be terminal failed and must carry a current canonical submission
  snapshot whose command, project identity, target, clean source revision, and request ID
  match the recovery request. Timed-out, cancelled, interrupted, or non-terminal jobs are
  visible but not eligible in the first version.
- Eligibility requires durable evidence created before the first hosting effect that
  immutably proves the original job/request, canonical hosting intent, target, source,
  configuration, and starting generation belong to one operation. Generic job snapshots,
  logs, or unbound host receipts are insufficient. Every earlier or partially linked job is
  legacy and non-authorizing. The formal specification owns the storage and binding mechanism.
- Fresh observation must bind one digestible evidence set: source identity, configuration,
  image identities for every service relevant to the original apply, including declared
  one-shot initializer and migration services, declared and configured topology, per-service
  running and health state where persistent service health applies, source-revision checks,
  and completed phase results for every one-shot service.
- Recovery must compare the original pre-effect intent and phase receipts with fresh evidence
  for the registered host's stable identity, project/environment/runtime location, complete
  non-secret configuration identity, opaque secret-reference/version identities, and exact
  image identity for every long-lived and one-shot service. Mutable names or tags, current
  manifest state, local receipts, and service health alone are non-authorizing. Any missing,
  changed, or unobservable identity refuses as incomplete or changed-target evidence.
- A missing, duplicated, unknown, partial, truncated, stale, malformed, or contradictory
  field is non-authorizing. The response names a stable class without exposing secrets,
  raw command arguments, source contents, environment values, or private paths.
- Observation and local Sandbox receipt reconciliation are the default and do not require
  mutation confirmation. Edge-only continuation is a separate explicit option and requires
  confirmation after an exact recovery observation.
- Observation/reconciliation and edge continuation are distinct immutable attempts. Edge
  continuation uses another distinct request identity that references the successful
  observation attempt, evidence identity, and resulting generation. Replaying an identity
  with identical intent returns its recorded result; reusing it with changed intent refuses.
- Ordinary apply and recovery share one target single-flight and generation compare-and-set.
  Apply cannot race past a recovery observation, and recovery cannot reconcile evidence from
  a generation changed by apply.
- Successful reconciliation atomically commits the immutable recovery attempt and hosting
  receipt, advances the hosting generation exactly once, and reports both expected and
  resulting generations. Exact replay returns that recorded result without another advance.
  Edge continuation references the successful observation attempt and resulting generation,
  then revalidates both immediately before gaining effect authority.
- Reconciliation also requires the complete evidence set to prove one unchanged host/runtime
  observation epoch through durable commit. Any restart, replacement, image or configuration
  change, host-identity change, or other torn observation during collection or commit returns
  a stable changed-evidence or uncertainty refusal. Edge continuation repeats this unchanged-
  epoch proof immediately before gaining effect authority.
- Retention may compact bounded evidence payloads, but it preserves a non-reusable request
  and effect tombstone for the authority lifetime. After evidence expiry, replay returns a
  stable expired or outcome-unknown class and never starts reconciliation or edge effects
  under that identity. Retention cannot turn an old attempt into a new request.
- Success classes distinguish newly reconciled observation, identical replay of a prior
  reconciliation, and completed edge-only continuation. Refusal, uncertainty, and failure
  classes are stable and machine-readable.

## Constraints and Dependencies

- Existing durable-job canonical submission and terminal lifecycle evidence is authoritative;
  legacy or corrupt rows are visible but never eligible.
- Existing hosting manifest allowed-branch policy remains mandatory. Recovery always requires
  the original captured source and current source evidence to be clean and exact, even when
  ordinary apply policy permits dirty source.
- Recovery uses only registered explicit remotes. Target inference is forbidden.
- Hosting apply and recovery must share single-flight ownership and generation fencing.
- Read-only observation must finish within finite bounds and retain only bounded redacted
  evidence. Unknown or timed-out observation is a refusal, never success.
- Edge-only continuation may use only the existing supported hosting edge mechanism after
  exact revalidation. No new DNS, certificate, Caddy, or zone authority is introduced.
- Feature 047 owns host-wide resource admission, protection, and priority. This feature owns
  only hosting recovery eligibility, serialization, and evidence. It cannot grant itself
  incident priority or capacity; when Feature 047 governance evidence is required and is
  missing, stale, or adverse, protected continuation fails closed.
- Release requires focused source/security review and separately authorized live acceptance;
  local tests alone do not prove remote or production behavior.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Public shape | Add a first-class hosting recovery action bound to a failed durable job | Ordinary apply mutates too early for safe recovery | User request |
| Default authority | Observation plus Sandbox receipt reconciliation only | Incident recovery must start without source/runtime/edge mutation | User request |
| Optional mutation | Only separately confirmed, proven edge-only continuation | Exact runtime may be complete while the edge alone is pending | User request |
| Legacy policy | Never grandfather legacy or incomplete jobs | Runtime health cannot reconstruct missing request/phase authority | User request |
| Concurrency | Share apply/recovery single-flight and generation CAS | Prevent interleaved evidence and mutation | User request |
| Drift policy | Refuse dirty, divergent, partial, changed-target, or mutation-requiring evidence | Recovery must not silently become apply | User request |
| Evidence history | Bounded immutable attempts with stable result classes | Replay and review need durable, non-ambiguous evidence | User request |

## Open Questions

- None.

## Acceptance Outcomes

- An operator presenting legacy, unbound, dirty, divergent, partial, stale-generation,
  changed-target, changed-secret-version, repointed-host, changed-runtime-location,
  same-tag/different-image, missing-image, topology, service-health, one-shot-phase,
  governance, or mutation-requiring evidence receives a stable refusal and zero protected
  source, runtime, image, initializer, migration, DNS, or Caddy effects.
- An operator presenting one complete exact-runtime failed apply receives a bounded durable
  reconciliation result that changes only Sandbox-owned receipts. Repeating the same request
  after process exit or caller disconnect returns the same immutable result.
- Edge continuation remains unavailable until a separately identified attempt references a
  successful exact observation and receives explicit confirmation. Its result states that
  only edge authority was used and never claims a deployment or public-health success.
- Two concurrent apply/recovery callers observe one target owner and generation. A stale or
  different intent receives a stable refusal; an identical replay receives one recorded
  result rather than a second operation.
- After owner death, persistence failure, timeout, or uncertain edge effect, every caller
  receives an uncertainty result until re-observation proves the unchanged attempt and
  generation; no edge effect repeats automatically.
- An external Compose or runtime change during observation or durable commit produces a
  changed-evidence or uncertainty refusal rather than a reconciled result.
- Replaying an identity after evidence compaction or expiry, including an aged uncertain edge
  result, returns the retained non-reusable tombstone outcome and never repeats observation
  reconciliation or edge effects.
- Machine-readable and text output identify the result class, original job/request binding,
  explicit target, generation, bounded evidence identity, permitted effect scope, and
  redacted phase summary without exposing secrets or private payloads.
- Separately authorized disposable-host acceptance demonstrates these observable outcomes
  before release. Source review and local focused checks alone do not claim remote or
  production proof.

## Risks and Assumptions

- **Risk**: Existing host state lacks generation and immutable attempt history; migration
  must keep legacy records visible without treating them as eligible.
- **Risk**: Image and phase observations can be partial or misleading if not gathered and
  classified as one bounded set; any uncertainty must fail closed.
- **Risk**: Edge-only work is still production-path mutation and needs human review and a
  separate confirmation even when the runtime is exact.
- **Assumption**: Eligible new jobs have canonical durable submissions that include the
  clean source and original request identity.
- **Assumption**: The current hosting edge mechanism can be invoked without entering source,
  Compose, initializer, migration, or image paths.

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
