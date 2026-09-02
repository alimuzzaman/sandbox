# Feature Specification: Observation-Only Hosting Recovery

**Feature Branch**: `codex/host-observation-recovery`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Ready PRD at `specs/048-host-observation-recovery/prd.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reconcile an Exact Failed Apply Without Mutation (Priority: P1)

A hosting operator names one failed durable apply and asks Sandbox to determine whether the
exact intended runtime is already complete. Sandbox binds the failed job and request to one
fresh coherent evidence set, then either reconciles only its own receipt or refuses before
any protected hosting effect.

**Why this priority**: This is the minimum useful recovery. It lets an incident owner recover
truthful state without turning observation into another deployment.

**Independent Test**: Present an eligible failed apply whose clean source, configuration,
host, runtime location, images, topology, persistent services, one-shot phases, and hosting
generation all match. Verify that recovery records one immutable result, advances the
generation once, changes only Sandbox-owned receipts, and performs zero protected effects.

**Acceptance Scenarios**:

1. **Given** a terminal failed current-contract apply with exact complete evidence, **When**
   an operator supplies a distinct recovery request and expected generation, **Then** Sandbox
   returns a successful observation reconciliation with the expected and resulting
   generations and no source, runtime, image, initializer, migration, DNS, or Caddy effect.
2. **Given** the same immutable recovery request completed earlier, **When** any caller
   repeats it after disconnect or process exit, **Then** Sandbox returns the same recorded
   result without another observation authority decision or generation advance.
3. **Given** the named job is legacy, unbound, non-terminal, succeeded, timed out, cancelled,
   interrupted, or missing, **When** recovery is requested, **Then** Sandbox returns the
   applicable stable refusal before remote source access or any protected effect.

---

### User Story 2 - Refuse Drift, Partial Evidence, and Changed Targets (Priority: P1)

An operator receives a precise, safe refusal when any part of the original apply, current
source, target, runtime, or evidence set cannot prove the same complete deployment.

**Why this priority**: A recovery command is safe only if every negative case fails before it
can silently become source staging, runtime convergence, or edge repair.

**Independent Test**: Exercise the complete refusal matrix with protected-effect witnesses.
Every case returns a stable class and every witness remains untouched.

**Acceptance Scenarios**:

1. **Given** dirty current or original source, changed branch, source, configuration,
   secret-reference version, registered host, runtime location, or exact image identity,
   **When** recovery is requested, **Then** Sandbox refuses as changed or ineligible evidence.
2. **Given** missing, duplicate, malformed, truncated, stale, contradictory, or partial
   topology, service, image, source-revision, phase, generation, or governance evidence,
   **When** recovery is requested, **Then** Sandbox refuses as incomplete, conflicting, or
   non-authorizing evidence.
3. **Given** recovery would require source push/reset, Compose convergence, build, image
   change, initializer, migration, secret change, database/queue work, or any broader repair,
   **When** recovery is requested, **Then** Sandbox refuses with normal reviewed apply as the
   next applicable workflow and performs none of those effects.

---

### User Story 3 - Continue Only a Proven Pending Edge (Priority: P2)

After a successful observation reconciliation proves the runtime exact, an operator may make
a separate explicitly confirmed request to continue only the already-declared pending edge.

**Why this priority**: Some failed applies complete source and runtime work but leave only the
edge pending. This is useful, but it is still externally visible mutation and must stay
separate from the observation-only MVP.

**Independent Test**: Start from a successful immutable observation attempt and its resulting
generation. Verify confirmation and exact revalidation are mandatory, only the supported edge
mechanism is reachable, and no source/runtime witness is called.

**Acceptance Scenarios**:

1. **Given** an exact successful observation with edge pending, **When** an operator supplies a
   distinct edge request referencing that attempt and generation but omits confirmation,
   **Then** Sandbox refuses before edge effects.
2. **Given** the same state and explicit confirmation, **When** the unchanged evidence epoch is
   re-proven immediately before effect authority, **Then** Sandbox performs only the supported
   edge continuation, observes the result, and records a bounded terminal outcome.
3. **Given** the edge is not the sole incomplete phase, the generation changed, governance is
   unavailable, or fresh evidence differs, **When** continuation is requested, **Then**
   Sandbox refuses without any edge or runtime effect.

---

### User Story 4 - Recover From Concurrency and Uncertain Effects (Priority: P2)

Operators can safely retry after a competing apply, caller disconnect, owner death, receipt
failure, stale-looking lock, timeout, or uncertain edge effect without duplicating authority.

**Why this priority**: Recovery itself must survive interruption. Otherwise the new command
recreates the same ambiguity it is intended to resolve.

**Independent Test**: Interrupt each durable phase, race apply and recovery, and compact old
evidence. Verify one owner/generation, no time-based takeover, no automatic edge repetition,
and stable tombstone outcomes.

**Acceptance Scenarios**:

1. **Given** apply or recovery owns the target single-flight, **When** a different request
   races it, **Then** the new request receives a stable busy or generation refusal and cannot
   observe or commit an interleaved generation.
2. **Given** observation or durable commit sees an external runtime restart, replacement,
   image/configuration change, or torn evidence epoch, **When** reconciliation is attempted,
   **Then** Sandbox records uncertainty or changed evidence and does not reconcile.
3. **Given** edge outcome is uncertain or its evidence has expired, **When** the request is
   replayed, **Then** Sandbox returns the retained non-reusable uncertainty or expiry outcome
   and never repeats the edge effect.

### Edge Cases

- The failed job exists but its canonical submission, hosting authority evidence, or original
  request identity is absent, corrupt, oversized, or only partly linked.
- The job's command resembles hosting apply but resolves a different project, environment,
  registered remote, source root, or allowed branch.
- A mutable image tag is unchanged while its immutable image identity changed.
- A registered remote name is unchanged while its stable host identity or runtime location
  changed.
- The current manifest matches locally but the original non-secret configuration or opaque
  secret-reference/version identity differs.
- A one-shot initializer or migration image exists but its completed phase receipt is absent.
- Persistent services are healthy but one declared service is missing, duplicated, replaced,
  unhealthy, or running the wrong source revision.
- Observation reaches its byte, row, service, phase, or time bound.
- An out-of-band runtime change occurs during observation or between observation and commit.
- Receipt reconciliation persists one side of the intended atomic result but not the other.
- Identical replay arrives after payload compaction; changed intent reuses an old request ID.
- Feature 047 governance evidence is required but missing, stale, adverse, or incomplete.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST provide a public hosting recovery action for one explicit
  registered remote, project, and environment; target inference MUST be forbidden.
- **FR-002**: Every observation/reconciliation request MUST supply its own replay-safe request
  identity, the original durable job ID and request ID, and the expected hosting generation.
- **FR-003**: The recovery request identity MUST immutably bind the action, failed job,
  original request, explicit target, expected generation, and permitted effect scope.
- **FR-004**: Eligibility MUST require a terminal `failed` durable job. Missing, succeeded,
  non-terminal, timed-out, cancelled, and interrupted jobs MUST remain visible but ineligible.
- **FR-005**: Eligibility MUST require durable pre-effect hosting authority evidence proving
  that the job, request, canonical hosting intent, target, clean source, non-secret
  configuration, and starting generation belong to one operation.
- **FR-006**: Generic durable-job snapshots, logs, current manifests, local host receipts,
  mutable names or tags, and service health alone MUST NOT establish recovery authority.
- **FR-007**: Hosting applies created before FR-005 evidence exists, including the historical
  Lenzora failure, MUST be classified as legacy and MUST NOT be grandfathered.
- **FR-008**: Recovery MUST require the original captured source and current source evidence
  to be clean, exact, and allowed by the environment's branch policy even when ordinary apply
  permits dirty source.
- **FR-009**: Fresh recovery evidence MUST bind the stable registered-host identity,
  project/environment/runtime location, clean source identity, complete non-secret
  configuration identity, opaque secret-reference/version identities, exact image identities,
  declared and configured topology, persistent service state/health/source revision, one-shot
  service image identities and completion receipts, and bounded phase results.
- **FR-010**: Exact image identity MUST be proven for every long-lived and one-shot service;
  image names and tags MUST NOT substitute for immutable identity.
- **FR-011**: The complete evidence set MUST prove one unchanged host/runtime observation
  epoch through durable commit. A restart, replacement, configuration/image change, host
  change, or torn observation MUST be non-authorizing. Recovery MUST first durably store only
  a bounded, explicitly non-authorizing provisional marker, immediately re-observe under the
  same ownership, and atomically promote to success only when pre/post evidence identities
  match. Provisional state MUST expose no reconciled receipt, generation advance, terminal
  success attempt, or edge authority.
- **FR-012**: Any missing, duplicated, unknown, stale, partial, malformed, truncated,
  contradictory, changed, or unobservable required field MUST produce a stable refusal or
  uncertainty result before protected effects.
- **FR-013**: The default recovery scope MUST be limited to observation and atomic
  reconciliation of Sandbox-owned immutable attempt and hosting receipt state.
- **FR-014**: Successful observation reconciliation MUST atomically advance the hosting
  generation exactly once and return the expected and resulting generations.
- **FR-015**: Exact replay of a completed request MUST return its recorded result without
  another generation advance or protected effect; changed intent using the same identity MUST
  refuse.
- **FR-016**: Apply and recovery MUST share one single-flight owner and generation
  compare-and-set for the same remote/project/environment target.
- **FR-017**: A stale generation, live incompatible owner, lost ownership proof, or persistence
  uncertainty MUST refuse or return uncertainty; elapsed time alone MUST NOT grant takeover.
  A persisted pre-effect observation owner MAY resume only for the exact same request identity
  and digest while its explicit phase proves no effect was entered. An exact provisional owner
  MAY resume only its post-write observation/promotion protocol. Every different owner and
  every effect-entered or malformed owner/uncertainty/provisional state MUST remain fenced.
- **FR-018**: Observation reconciliation MUST NOT push or reset source, run Compose, build or
  change images, run initializers or migrations, read or change secret values, mutate
  databases or queues, or change DNS, certificates, zone policy, or Caddy.
- **FR-019**: A mutation-requiring or genuinely changed deployment MUST be refused and directed
  to the normal reviewed hosting apply workflow rather than being repaired by recovery.
- **FR-020**: Edge continuation MUST use a distinct replay-safe request identity and reference
  the successful observation attempt, evidence identity, and resulting generation. Exact
  replay of a terminal edge request MUST return its recorded edge result without re-entering
  the adapter; `already_reconciled` is reserved for observation-reconciliation replay.
- **FR-021**: Edge continuation MUST require explicit confirmation and MUST re-prove the
  unchanged observation epoch, generation, target, and governance eligibility immediately
  before gaining effect authority.
- **FR-022**: Edge continuation MUST be eligible only when the exact runtime is complete and
  ready and the supported edge phase is the sole incomplete phase.
- **FR-023**: Edge continuation MUST reach only the existing declared edge mechanism and MUST
  never enter source, Compose, build, image, initializer, migration, secret, database, or
  queue paths.
- **FR-024**: An uncertain edge delivery or result MUST record a stable non-success outcome and
  MUST NOT be repeated automatically.
- **FR-025**: Every attempt MUST persist bounded immutable input identity, phase summary,
  evidence identity, generation transition, effect scope, terminal class, and safe timestamps.
- **FR-026**: Retention MAY compact bounded evidence payloads but MUST preserve a non-reusable
  request/effect tombstone for the authority lifetime. Expired replay MUST NOT start effects.
- **FR-027**: Public text and machine-readable results MUST use versioned stable success,
  refusal, uncertainty, and failure classes and MUST distinguish observation reconciliation,
  identical replay, edge-only completion, legacy evidence, changed target, partial evidence,
  generation conflict, busy ownership, mutation required, expired evidence, and unknown effect.
- **FR-028**: Public and persisted evidence MUST exclude secret values, source contents, raw
  command arguments, environment values, protected values, and private paths.
- **FR-029**: Observation MUST have finite time, service, image, row, phase, byte, and receipt
  bounds; reaching a bound MUST be visible and non-authorizing.
- **FR-030**: Feature 047 MUST remain the authority for host-wide resource admission,
  protection, and priority. Recovery MUST NOT grant itself incident priority or capacity and
  MUST fail closed when required governance evidence is not authorizing.
- **FR-031**: Existing hosting apply, status, diagnose, logs, secrets, sync, login, default
  Docker/Caddy, durable-job, and remote behaviors MUST remain available through compatible
  interfaces unless an explicit new recovery eligibility requirement applies.
- **FR-032**: Documentation MUST distinguish observation reconciliation, edge-only
  continuation, full apply, terminal durable-job evidence, remote/runtime activation, and
  public production proof.
- **FR-033**: Read-only hosting status MUST expose the current target generation and bounded
  latest recovery summary so an operator can construct an explicit fenced request without
  reading internal state.
- **FR-034**: Recoverable edge intent MUST contain at most 64 routes, 128 DNS records, and
  64 unique certificate hostnames, serialize to at most 64 KiB, and keep the complete
  persisted hosting operation at or below 128 KiB. Exceeding any bound MUST mint no
  recovery authority.

### Key Entities

- **Hosting Operation Identity**: The immutable association among a durable job/request,
  canonical hosting intent, explicit target, clean source, configuration, and starting
  generation established before hosting effects.
- **Recovery Attempt**: One replay-safe observation/reconciliation or edge-continuation intent
  with its own identity, expected generation, permitted effect scope, phases, and terminal
  result.
- **Hosting Generation**: The monotonically fenced version of one explicit
  remote/project/environment target used to prevent stale observation or mutation.
- **Evidence Set**: One bounded coherent observation epoch containing the exact source,
  configuration, secret-reference, image, topology, service, one-shot, and phase identities
  needed for an authority decision.
- **Effect Tombstone**: The retained non-reusable identity and outcome class that prevents a
  compacted, expired, or uncertain attempt from becoming new authority.
- **Recovery Result Class**: A stable versioned semantic outcome in the success, refusal,
  uncertainty, or failure family.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In the complete negative-case matrix, 100% of legacy, dirty, divergent,
  partial, changed-target, stale-generation, changed-image, torn-epoch, governance, and
  mutation-requiring requests terminate with a stable non-success class and zero protected
  effects.
- **SC-002**: In 100 exact eligible recovery runs, 100 reconcile only Sandbox-owned receipts,
  advance the target generation exactly once, and report matching expected/resulting
  generations.
- **SC-003**: In 100 replay tests after caller disconnect, process exit, or payload
  compaction, 100 return the original immutable result or tombstone and cause no second
  generation advance or edge effect.
- **SC-004**: In 100 apply/recovery race tests, no target observes two owners or an interleaved
  committed generation; every loser receives a stable busy, conflict, or replay result.
- **SC-005**: In every confirmed eligible edge test, only the declared edge effect is reached;
  in every unconfirmed, stale, changed, or uncertain test, no edge effect is started or
  repeated.
- **SC-006**: Every recovery observation completes within its declared finite deadline and
  bounded output limits; any exceeded bound is reported as non-authorizing.
- **SC-007**: Automated secret/privacy inspection finds zero secret values, source contents,
  raw command arguments, environment values, protected values, or private paths in persisted
  and public recovery evidence.
- **SC-008**: A new operator can identify whether the outcome is receipt-only reconciliation,
  edge-only continuation, refusal, uncertainty, or full-apply-required from one result without
  reading logs or inspecting implementation details.

## Assumptions

- The first version accepts only terminal `failed` jobs; other terminal classes may be added
  later only through an explicit contract change and equivalent evidence.
- Eligible hosting applies are launched through the current durable local job runtime and can
  establish a pre-effect hosting operation identity; older applies remain legacy.
- Registered remotes expose a stable non-secret host identity and runtime location suitable
  for exact comparison.
- Hosting manifests can identify every long-lived and one-shot service relevant to apply.
- Opaque secret-reference/version identities can be compared without reading or persisting
  secret values.
- Edge-only continuation reuses only existing declared hosting edge authority; it adds no new
  DNS, certificate, zone-policy, or Caddy scope.
- Live disposable-host acceptance, remote runtime activation, Lenzora deployment, production
  deployment, and public proof remain separately authorized work after source integration.
