# Feature Specification: Immutable Activation and Recovery

**Feature Branch**: `codex/feature-047-immutable-oci-clean`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Ready PRD at `specs/051-immutable-activation-recovery/prd.md`

**Deployment amendment**: 2026-09-07, following the user-requested Astra XHigh
end-to-end deployment audit and instruction for current-agent implementation.
The original PRD/review remains historical. The requirements below extend the
accepted execution contract; they do not authorize live incident settlement,
credential access, data changes, or deployment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Activate the Exact Staged Generation (Priority: P1)

A release operator activates only the exact image already approved by Feature 049 and
proved local by Feature 050, then receives exact running and durable-state evidence.

**Why this priority**: This is the minimum useful runtime outcome and the first phase
allowed to mutate services.

**Independent Test**: With fake runtime/edge adapters, activate one matching plan/proof
and prove exact init, selected-service replacement, health, edge, and atomic generation
commit with zero trust, credential, broker, pull, or build calls.

**Acceptance Scenarios**:

1. **Given** exact current artifacts, machine activation-authority binding, authenticated
   stage-proof custody lease/pin that is unexpired for new acceptance, local image,
   topology, grant, and starting
   generation, **When** activation is confirmed, **Then** the selected services run the
   exact local image and one terminal generation is recorded.
2. **Given** any artifact, target, daemon, image, topology, policy, grant, or generation
   differs, **When** activation is requested, **Then** it refuses before runtime effects.
3. **Given** runtime replacement is exact but required health or edge is incomplete,
   **When** observed, **Then** the transaction remains non-success and inspectable.

---

### User Story 2 - Execute Declared Init Without Unsafe Replay (Priority: P1)

An operator can see that each one-shot init container was exact before it started and
that possible execution is never guessed or silently repeated.

**Why this priority**: An ambiguous init may mutate durable data and makes optimistic
retry or rollback unsafe.

**Independent Test**: Interrupt every create/inspect/effect-entry/start/exit/receipt
boundary and prove exact pre-start inspection, one start at most, complete termination,
and durable uncertainty whenever execution cannot be proven terminal.

**Acceptance Scenarios**:

1. **Given** an ordered init declaration, **When** activation reaches it, **Then** each
   container is created without start, inspected exactly, effect entry is recorded, and
   bounded successful exit is recorded before the next step.
2. **Given** pre-start inspection differs, **When** evaluated, **Then** the container is
   removed and activation refuses without starting it.
3. **Given** start may have occurred but no exact terminal receipt exists, **When** the
   request is replayed or recovery observes it, **Then** init is not repeated and the
   target remains fenced.

---

### User Story 3 - Reconcile Interrupted Activation by Observation (Priority: P2)

An incident operator uses the distinct replay-safe `sb host image recover` entrypoint,
which obtains a Feature 048 read-only observation and atomically reconciles an interrupted
activation or rollback without letting recovery perform protected effects.

**Why this priority**: Crash safety requires truthful reconciliation while preserving
Feature 048's observation-only guarantee.

**Independent Test**: Crash at every transaction/edge boundary, observe exact new,
exact prior, neither, ambiguous/mixed, and unavailable states, and prove only phase-legal
`exact_new` evidence can promote a pending 051 transition; `exact_prior` may only close a
proven pre-effect transaction without generation advance, and `neither`/`ambiguous` never
promote while Feature 048 performs zero protected effects.

**Acceptance Scenarios**:

1. **Given** an active transaction and two fresh coherent observations exactly match its
   new generation and each other in a phase whose complete receipts allow promotion,
   **When** the distinct 051 recovery request runs, **Then** 051 first durably stores a non-
   authorizing provisional marker and later commits its immutable recovery result plus the
   matrix-allowed promotion in one atomic shared-lock/CAS write.
2. **Given** evidence is partial, stale, contradictory, mixed-epoch, or neither exact
   generation, **When** reconciled, **Then** the target remains uncertain and fenced.
3. **Given** recovery is requested, **When** it runs, **Then** it does not repurpose
   failed-apply `sb host recover` and performs no init, service, pull, build, edge,
   activation, adoption, rollback, trust, or credential work.

---

### User Story 4 - Adopt an Exact Zero-Init Generation (Priority: P2)

A recovery operator explicitly records an already exact running generation only when
the current plan declares no init work.

**Why this priority**: Narrow adoption can recover state, but no runtime fact can prove
that an out-of-band data-mutating init ran correctly.

**Independent Test**: Adopt one exact zero-init target and reject every init-bearing,
health-only, legacy-receipt, caller-attested, stale, or effect-requiring case.

**Acceptance Scenarios**:

1. **Given** a zero-init plan and exact fresh plan/proof/local/running/health/edge facts,
   **When** adoption is confirmed, **Then** one generation is recorded with no effects.
2. **Given** the plan declares any init step, **When** adoption is requested, **Then** it
   refuses regardless of caller, project, legacy, health, or out-of-band evidence.
3. **Given** adoption would need a pull, service change, init, or edge update, **When**
   evaluated, **Then** it refuses instead of performing that effect.

---

### User Story 5 - Roll Back One Proven Generation (Priority: P2)

A recovery operator explicitly switches to the one retained previous generation using
only local proof and a machine-approved pre-activation compatibility grant.

**Why this priority**: A bounded local rollback reduces outage time without reopening
registry credentials or permitting an unsafe data/schema downgrade.

**Independent Test**: Roll back one exact eligible generation and reject first deploy,
missing local image, post-hoc/stale/caller grant, changed config/topology, uncertain init/
data, second-oldest selection, and every registry/pull path.

**Acceptance Scenarios**:

1. **Given** current and previous exact generations plus their current pre-activation
   machine grant, **When** rollback is confirmed, **Then** the same state machine runs
   the previous local image, proves health/edge, and atomically records the reversal.
2. **Given** any grant binding or compatibility fact differs, **When** rollback is
   requested, **Then** it refuses before runtime effects.
3. **Given** no previous generation or its local proof/image is unavailable, **When**
   rollback is requested, **Then** it refuses without registry, pull, or fallback.

### User Story 6 - Complete a Cold Deployment Once (Priority: P1)

A release operator deploys to a registered target without relying on stale source,
private settings, existing queue processes, or already-configured routes. Each
required startup job runs once, after its prerequisites and before its consumers.

**Independent Test**: Start a synthetic Lenzora-shaped target with no running
services, private file-backed credentials and delayed readiness. Prove prerequisite,
initializer, consumer and edge ordering through one durable receipt chain. Repeat
with failures at each boundary and prove no automatic duplicate effects.

**Acceptance Scenarios**:

1. **Given** an exact signed application and complete private candidate, **When**
   activation starts, **Then** prerequisites become ready before dependent startup
   jobs, jobs finish before consumers, and a bounded readiness wait precedes commit.
2. **Given** two environments for one project, **When** one prepares source or
   configuration, **Then** the other's running source and retained inputs remain intact.
3. **Given** unavailable private credentials or known provider capability failure,
   **When** preflight runs, **Then** no initializer, runtime or provider write starts.

### User Story 7 - Settle an Uncertain Incident Without Inventing Success (Priority: P1)

A production owner can explicitly settle a quiescent, preserved incident whose
historical effects cannot be proven absent. Settlement preserves the uncertainty
and does not claim successful deployment or reversal of data changes.

**Independent Test**: Bind a synthetic uncertain transaction to exact containment,
preservation, data-assessment and operator-approval evidence. Prove that settlement
changes only terminal ownership, keeps generation/current unchanged, and requires
explicit predecessor and data-compatibility authority for a new forward attempt.

**Acceptance Scenarios**:

1. **Given** missing quiescence, preservation, data assessment or approval evidence,
   **When** settlement is requested, **Then** ownership remains fenced.
2. **Given** complete unchanged evidence and exact approval, **When** settlement
   commits, **Then** it records `abandoned_with_effects`, preserves the original
   uncertainty, and performs no deployment, deletion, migration or provider effect.
3. **Given** a settled incident, **When** a new activation omits its predecessor or
   reviewed data-compatibility decision, **Then** it refuses before effects.

### Edge Cases

- Plan/proof schemas are valid separately but their digests, target, daemon, image, or
  staging generation do not match each other.
- Local image existed at staging but is missing or changed before activation/rollback.
- Rendered topology includes image aliases, tags, builds, pulls, platform override,
  duplicate services, unsafe dependencies, or unexpected orphans.
- Init create succeeds but inspection, durable effect entry, start, wait, cleanup, or
  receipt persistence is interrupted.
- Runtime changes during selected-container observation or between observation and commit.
- Runtime is exact but health or edge remains pending, failed, or acceptance-unknown.
- Feature 048 observation arrives while another legacy host mutation owns the target.
- A request is exactly replayed after terminal result compaction or changed intent reuses
  an existing request ID.
- Stage-proof compaction races proof verification or durable host acceptance, or the
  controller crashes with only a prepared proof-custody lease.
- Activation recovery crashes after the first observation/provisional write or evidence
  changes before the second observation/promotion.
- Adoption presents a legacy Feature 047/048 init/image receipt or external attestation.
- Rollback grant was created after activation or matches names but not exact digests.
- Prior generation is exact but current data/init compatibility is uncertain.
- An unknown target mutation capability is registered without the shared owner.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Feature 051 MUST require one complete closed digest-valid Feature 049
  `VerifiedImagePlan` and one complete retained (not compacted/expired) closed digest-valid
  Feature 050 `StagedImageProof`; caller-supplied artifacts are necessary but never
  sufficient authority.
- **FR-002**: Plan and proof MUST contain a byte-identical Feature 049
  `DeliveryIdentityProjection` and match exactly on plan/proof digest,
  target/machine/daemon, helper/capability, canonical owner/repository representation,
  requested and observed manifest/config/platform/local identity, topology, registry
  access observation, observation identity, and staging generation.
- **FR-003**: Feature 051 MUST validate FR-001/FR-002 structure and equality without
  reinterpreting receipt, provenance, signature, trust, repository, platform, topology,
  broker, credential, helper, pull, or staging policy.
- **FR-004**: A machine-owned authenticated `ActivationAuthorityBinding` MUST bind the
  exact `plan_digest`, `proof_digest`, stage request/digest, staging policy digest,
  staging generation, stage-ledger authority/revision, target/machine/daemon, exact
  delivery projection, selected services, ordered init declarations, runtime/Compose
  capability, edge policy, shared mutation-owner revision, state revision, and accepted
  049/050 schema revisions. It MUST also bind the rollback-grant authority identity,
  revision, and public-verification-key digest. The signing secret is outside Feature 051.
- **FR-005**: Before proof verification, Feature 051 MUST use Feature 050's authenticated
  proof-custody operation to durably prepare a lease that immediately pins the retained
  terminal proof and binds the durable activation-owner/request holder identity, activation
  request/digest, stage request/digest, proof digest, target, stage-ledger generation/
  revision, and finite admission deadline.
  Locks MUST be acquired target-wide mutation, shared host-state transaction, then stage-
  ledger target, and released in reverse. The stage lock/pin MUST be held through proof
  verification and atomic durable host-state acceptance, then promoted to an accepted pin
  bound to that acceptance. No init/runtime/edge effect may begin until accepted-pin
  promotion is durable. Crash/expiry MUST NOT auto-unpin. Before host acceptance, an expired
  preparation MUST refuse new acceptance and the same durable holder MAY cancel only after
  proving acceptance absent. When exact host acceptance already exists, replay by that same
  holder MUST promote even after the deadline. No process or unrelated recovery identity
  may adopt, cancel, promote, or release custody. Only the exact accepted activation owner
  MAY release the pin after terminal authority is durable. Missing, compacted, tombstoned,
  mismatched, capacity-exhausted, or expired-before-acceptance custody MUST refuse.
  Caller/project input MAY narrow but MUST NOT authorize, widen, substitute identity, add a
  trust exception, or supply a credential/reference/token.
- **FR-006**: Activation, adoption, rollback, Feature 048 reconciliation, normal apply,
  sync, login/setup, edge continuation, and every registered target mutation MUST share
  one target-wide single-flight owner and generation compare-and-set.
- **FR-007**: An unknown, unregistered, malformed, expired, or bypassing mutation owner
  MUST fail closed; time/PID/process absence alone MUST NOT transfer ownership.
- **FR-008**: Every 051 request MUST have a non-empty replay-safe request ID, immutable
  request digest, operation type, expected starting generation, and explicit confirmation.
- **FR-009**: Durable acceptance MUST precede effects; acceptance-unknown MUST use
  read-only ledger lookup before exact replay.
- **FR-010**: Exact terminal replay MUST return the recorded result with zero new
  effects; changed reuse MUST refuse; possible effect without proof MUST remain fenced.
- **FR-011**: Activation and rollback MUST be operation types in one closed transaction
  state machine and MUST NOT have separate state, ownership, or unfenced execution paths.
- **FR-012**: Before effects, rendered topology MUST prove exact selected services and
  reject tags, aliases, indexes, build, pull, platform resolution/override, alternate
  images, duplicates, missing services, unsafe dependencies, and unexpected orphans.
- **FR-013**: Runtime replacement MUST use only the already-local repository-qualified
  exact manifest digest with build and pull disabled and no registry fallback.
  The full private render MUST remain inside the remote helper, be passed unchanged on stdin
  to the no-build/no-pull Compose effect, and be re-rendered afterward. Its raw hash MUST NOT
  be returned or persisted. Machine/public state MUST bind the complete raw render with a
  machine-keyed, target-scoped, domain-separated opaque HMAC. The target key MUST be
  derived locally from the owner-only machine master and exact machine/target identities;
  the master MUST NOT cross SSH. SSH output MUST contain only a closed
  allowlisted projection with exact image/platform/topology identities, service/dependency/
  environment-key names, and refusal flags; arbitrary commands, entrypoints, labels,
  annotations, health checks, URLs, logging values, extensions, environment values, and
  inline content MUST remain private. The selected Compose project, opaque render identity,
  and each resulting container's Compose configuration hash MUST match through a
  target-scoped protected identity. Every fresh running, post-edge, and recovery observation
  MUST compare that protected identity under unchanged target/runtime epochs. Raw `ps`
  labels, raw inspect environment/labels, and raw Compose hashes MUST remain remote. All
  top-level Compose configs/secrets and external networks MUST refuse
  unless a future contract snapshots their exact private bytes or immutable engine identity.
- **FR-014**: Every init step MUST be ordered, bounded, and created without start before
  its exact container configuration is inspected.
- **FR-015**: Pre-start init inspection MUST prove exact image reference/local identity,
  config/platform, command, mounts, networks, environment-key names, privilege,
  dependency scope, target, and runtime epoch; any mismatch MUST remove without start.
  Cleanup MUST remove only a container whose deterministic name and owner label both bind
  the exact target, image, and admitted initializer declaration. A foreign or unproved
  name collision MUST remain fenced and MUST NOT be removed.
- **FR-016**: Init environment secret values MUST NOT enter persisted state, receipts,
  logs, diagnostics, public output, inspection evidence, or an unkeyed digest that acts as
  an offline verifier. Only the target-scoped derived binding key MUST travel in private
  stdin; the machine master MUST remain local. The derived key MUST be removed before any
  child process and never enter state, commands, output, or receipts. Helper redaction
  MUST cover every observed rendered scalar key and value even when services
  reuse the same environment key, and MUST remove top-level inline content.
- **FR-017**: The state machine MUST durably record an init `effect_entered` boundary
  immediately before start and one bounded terminal exit/termination receipt afterward.
- **FR-018**: Possible init start without an exact terminal receipt MUST be durable
  uncertainty and MUST NOT be automatically repeated, adopted, committed, or rolled back.
- **FR-019**: Init execution MUST have finite deadlines, bounded streams, explicit
  ownership/cancellation, and complete container/process termination evidence.
- **FR-020**: Activation/rollback success MUST require one coherent fresh observation
  proving every selected container's exact declared image reference, local image identity,
  repository/config digest, platform, topology, Compose project/configuration, registered
  target identity, runtime generation, and required health.
- **FR-021**: Missing, partial, duplicate, contradictory, stale, changing, mixed-epoch,
  oversized, or timed-out running evidence MUST be non-success.
- **FR-022**: Edge readiness MUST be an immutable sub-request of the same 051 transaction
  and MUST complete before activation/rollback success. A receipt MUST bind request,
  route, target, prospective generation, runtime observation, and application deployment
  identity. HTTP reachability alone is diagnostic and MUST return `edge_incomplete`.
- **FR-023**: A proven-not-entered edge phase MAY resume only the exact request.
  Acceptance-unknown or interrupted edge delivery MUST first query existing replay
  authority; an exact terminal receipt MAY promote only after fresh unchanged runtime proof.
- **FR-024**: Possible edge delivery without an authoritative terminal receipt MUST
  remain fenced and MUST NOT be retried, committed, adopted, or rolled back through 051.
- **FR-025**: Durable state MUST be closed, versioned, bounded, atomic, owner-only, and
  store current generation, at most one previous generation, active/terminal transaction,
  artifact/proof/observation/config/topology digests, init receipts, edge result, and CAS.
  Forward acceptance MUST also persist the activation-authority digest, accepted Feature
  050 proof-pin/host-acceptance binding, rollback-grant digest, and deterministic rollback
  subject. Recovery state MAY contain one bounded 051-owned non-authorizing provisional
  marker with exact request/transaction/generation and pre-observation identity/epoch.
  Every accepted transaction MUST retain a closed recovery context containing the exact
  target, Compose project, and selected services so recovery does not need a candidate.
- **FR-026**: Durable state/public results MUST NOT store credentials, credential
  references, secret values, arbitrary child output, private temporary paths, or raw env.
- **FR-027**: The existing shared `RecoveryRepository` MUST remain the sole outer
  `hosts.json` parser/writer/transaction-locker/fsync owner. Feature 051 MUST submit only
  closed nested candidate transitions through its narrow port. State writes MUST use the
  existing shared transaction/per-target locks, generation CAS, atomic durable replacement,
  immutable terminal results, legacy/unknown-field preservation, and bounded non-reusable
  tombstones; no activation module or `_hosting.py` path may become a second writer.
- **FR-028**: Feature 051 MUST expose a distinct replay-safe `sb host image recover`
  entrypoint bound to activation transaction, recovery request/digest, expected generation,
  and confirmation. It MUST call a new Feature 048 read-only activation-observer API and
  MUST NOT repurpose or alter existing failed-apply `sb host recover` request/result authority.
- **FR-029**: Feature 048 MUST NOT execute/resume init, service replacement, pull, build,
  edge, activation, adoption, rollback, trust, broker, helper, or credential work.
- **FR-030**: Under one shared target owner/CAS, Feature 051 MUST obtain and validate a
  fresh Feature 048 exact-new/exact-prior/neither/ambiguous observation, then durably write
  only a bounded 051-owned provisional marker with `authorizing: false`, exact recovery/
  transaction/generation identity, and the complete pre-observation identity/epoch. The
  provisional write MUST expose no recovery success, transaction promotion, receipt,
  generation advance, or effect authority. While the exact owner remains fenced, Feature
  051 MUST immediately re-observe through Feature 048 and require exact pre/post evidence
  identity plus unchanged target/runtime epoch, transaction, and generation. A separate
  atomic host-state replacement MAY then write the immutable recovery result plus legal
  transaction promotion and clear the marker. Crash replay MAY resume only the post-write
  observation for the exact provisional request/digest; changed, malformed, effect-entered,
  stale, partial, unavailable, or mismatched post-evidence MUST atomically record a stable
  non-success recovery result and clear the provisional while leaving the activation
  transaction fenced and unpromoted. Persistence uncertainty leaves the provisional fenced.
  Accepted, preflight, init-pending, and runtime-pending phases MAY have no candidate
  generation. Recovery MUST then use only the persisted recovery context and prior
  generation: exact prior or an empty generation-zero runtime closes as no-effect, while
  other observations remain unpromoted uncertainty.
  Recovery classification MUST use the exhaustive matrix in
  `contracts/recovery-integration.md`: for both `activate` and `rollback`, `neither` and
  `ambiguous` MUST never promote; `exact_prior` MUST never advance generation and MAY only
  close a proven pre-effect transaction as stable no-effect non-success; `exact_new` MAY
  promote only from a matrix-listed phase with every required init/runtime/edge receipt
  already authoritative. Adoption is ineligible for this recovery protocol.
  Feature 048 MUST perform no state/provisional write, and existing failed-apply recovery
  behavior MUST remain unchanged.
- **FR-031**: Adoption MUST use a new confirmed replay-safe request, require an exact
  plan declaring zero init steps plus current plan/proof/local/running/health/edge proof,
  and perform zero init/runtime/edge effects.
- **FR-032**: Caller/project/external/legacy receipts, attestations, or health MUST NOT
  substitute for the zero-init adoption condition.
- **FR-033**: Rollback MUST select only the single retained previous generation and its
  still-present exact locally proven image; first-generation or older selection refuses.
- **FR-034**: Before forward acceptance, rollback MUST require a machine-owned
  `RollbackCompatibilityGrant` over deterministic `ForwardRollbackSubject` containing
  target/daemon, current rollback-target generation digest, forward candidate plan/proof/
  authority-binding digests, forward non-secret config/topology/init-data contract, and
  policy revision. It MUST NOT bind the not-yet-existing future generation digest.
  Forward acceptance persists grant/subject digests and the resulting terminal forward
  generation references them for later exact rollback validation.
  The grant MUST carry a valid Ed25519 SSH signature from the public key whose digest and
  authority identity/revision are in the binding. Policy bundles MUST be owner-only,
  regular, single-link, no-follow, bounded, and stable across their read.
- **FR-035**: Caller/project claims, post-hoc grants, stale/mismatched bindings, uncertain
  init/data state, missing local proof, or changed target/daemon MUST refuse rollback
  before effects.
- **FR-036**: Feature 051 MUST expose no receipt/provenance/signature/trust decision,
  credential/broker/helper/pull/build/tag/prune capability or raw registry credential input.
- **FR-037**: Legacy Feature 047/048 state and receipts MUST remain readable/opaque and
  non-authorizing; new additive state MUST not destructively migrate or downgrade them.
- **FR-038**: Non-opt-in hosting and existing CLI, state, remote, apply, sync, login,
  edge, Feature 048, and public result contracts MUST retain compatible behavior.
- **FR-039**: Public results MUST use bounded stable activation/adoption/rollback/recovery
  success, refusal, failure, cancellation, and uncertainty classes.
- **FR-040**: Documentation MUST distinguish 049 trust, 050 staging, 051 effects/state,
  048 observation reconciliation, local acceptance, remote validation, and production proof.
- **FR-041**: Local implementation validation MUST use synthetic artifacts/fakes; live
  registry, secrets, remote mutation, edge, deployment, and production require separate
  authorization.
- **FR-042**: A hosting environment MAY declare an explicit `cloudflare.cache_purge`
  policy. When enabled it MUST use `scope: zone_all`, an explicit bounded zone allowlist,
  and a route-covered zone set. Invalid or unavailable policy MUST refuse before deployment
  effects; policy absence MUST preserve existing behavior.
- **FR-043**: A zone-wide purge MUST resolve and validate every approved Cloudflare zone
  before its first provider write, then record one bounded per-zone receipt. A provider
  acknowledgement proves API acceptance only, not global propagation or frontend-generation
  delivery.
- **FR-044**: Purge operations MUST bind target, deployment request/revision, generation
  subject when present, route and policy digests, ordered zone identities, and `zone_all`
  scope through the existing target owner. Durable state MUST record preparation before
  effect entry, `effect_entered` before each POST, and the acknowledgement immediately
  afterward. Exact terminal replay MUST not send another POST; possible submission without
  exact acknowledgement MUST remain `acceptance_unknown` and MUST not be replayed by
  observation recovery.
- **FR-045**: Feature 051 v2 activation/rollback and ordinary hosting apply MUST use the
  admitted purge policy before final edge/runtime proof and generation/edge-ready commit.
  Recovery observation and adoption MUST perform zero purge writes; rollback MUST use a new
  admitted purge operation. Lenzora production and development manifests MUST retain their
  existing v2 and ordinary-apply deployment paths respectively.

### Key Entities

- **Activation Policy**: Machine authority for target, topology, init, runtime, edge,
  shared mutation owner, state, and accepted artifact revisions.
- **ActivationAuthorityBinding**: Machine-authenticated exact plan/proof/stage-ledger
  identity plus target/projection/capability authority; caller artifacts cannot create it.
- **Activation Request**: Replay-safe confirmed immutable intent and starting generation.
- **Activation Transaction**: Single activation/rollback/adoption state machine record.
- **Init Receipt**: Exact inspected configuration, effect boundary, exit, and termination.
- **Running Observation**: Coherent exact service/image/topology/health/runtime epoch.
- **Verified Activation Generation**: Closed current or single previous generation.
- **ForwardRollbackSubject**: Pre-forward deterministic candidate plus current rollback
  target/config/topology/init-data identity, independent of future result fields.
- **RollbackCompatibilityGrant**: Machine signature/authority over that exact subject.
- **Recovery Observation**: Each of the two Feature 048 bounded non-effect observations
  carries exactly one closed classification (`exact_new`, `exact_prior`, `neither`, or
  `ambiguous`); the two identities/epochs/classifications must match around the 051
  provisional durable write.
- **ActivationRecoveryProvisional**: Feature 051-owned bounded non-authorizing pre-
  observation fence; it is never a receipt, promotion, generation, or effect authority.
- **Activation Result**: Stable bounded terminal/replay/uncertainty envelope.
- **Edge-Cache Purge Policy**: Explicit provider, `zone_all` scope, route-covered zone
  allowlist, and policy digest for one hosting environment.
- **Edge-Cache Purge Operation**: Target-bound durable per-zone state machine and immutable
  receipt for Cloudflare cache invalidation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every caller-artifact-only, missing or expired-before-acceptance stage-proof
  lease/pin, invalid/
  stale/substituted/mixed artifact, target, topology, policy, grant, local-image, or
  generation case refuses before init/runtime/edge effects.
- **SC-002**: Across all exact replay/crash schedules, each init/runtime/edge effect is
  entered at most once and every unproven effect remains durably fenced.
- **SC-003**: Every activation/rollback success has one coherent exact running/health/
  edge observation and one atomic generation transition.
- **SC-004**: Every pairwise race among all registered target mutations yields one owner,
  one generation transition, zero interleaved effects, and compatible loser results.
- **SC-005**: Every distinct replay-safe 051 recovery case performs zero protected effects,
  writes at most one non-authorizing provisional and one later atomic recovery-result/
  promotion, and promotes only when exact pre/post evidence identities and epoch bindings
  match and the closed phase/class matrix permits it; existing failed-apply recovery results
  remain unchanged. Every non-promoting cell returns its specified stable no-effect,
  non-success, conflict/replay, or uncertainty result and never advances generation.
- **SC-006**: Every adoption success uses a zero-init plan, performs zero effects, and
  has exact current plan/proof/local/running/health/edge evidence.
- **SC-007**: Every rollback success uses only the single previous local image and an
  exact pre-forward deterministic subject/grant referenced by forward acceptance and
  terminal generation, with zero credential/broker/registry/pull/build calls.
- **SC-008**: Credential canaries, secret values, and Feature 049 trust-policy calls have
  zero witnesses in public 051 state, output, logs and process arguments. Supported
  application secret delivery uses only the private stdin/files allowed by FR-016 and
  FR-053; the machine master never leaves its owner and the derived key never reaches
  a child process. Activation after admission performs no broker or trust-policy calls.
- **SC-009**: Non-opt-in and legacy Feature 048 compatibility suites retain their
  existing results and old opaque state is byte-preserved by no-op/read paths.
- **SC-010**: A real disposable Lenzora-shaped cold deployment proves one queue
  prerequisite, all three ordered initializer exit/termination/cleanup receipts,
  all 17 exact healthy persistent services and the complete edge receipt before commit.
  Every injected uncertain boundary produces zero additional automatic starts.
- **SC-011**: Synthetic private-input tests prove exact secret-file delivery, unchanged
  retained inputs on publication failure, replay without a second broker read, and no
  cross-environment source mutation. Wrong application/control/tool identity refuses.
- **SC-012**: Every settlement success preserves original uncertainty and generation,
  requires exact approval/quiescence/preservation/data evidence, and performs zero
  runtime, data, provider or deletion effects. Every missing or changed evidence case
  stays fenced; no new forward operation omits predecessor/data authority.
- **SC-013**: Opt-in development cold-start tests prove each initializer starts once
  before its consumers, while the non-opt-in hosting compatibility suite remains green.

## Assumptions

- Feature 050's local image identity remains inspectable in the same target/daemon
  context until activation or rollback begins.
- Existing target mutation paths can adopt the shared owner without changing successful
  non-opt-in behavior.
- Existing edge replay authority can distinguish not-entered, exact terminal, and
  uncertain delivery without exposing credentials.

## Dependencies

- Feature 049 `VerifiedImagePlan` contract.
- Feature 050 `StagedImageProof` contract and local image presence.
- Feature 048 observation/reconciliation contracts and safe recovery invariants.
- Existing hosting state, target mutation owner, Compose/runtime, edge, and durable
  request mechanisms.

## First-activation provisioning requirement

- **FR-051**: After successful v2 stage custody, protected activation preparation MUST
  read exact retained proof/record/revision through the stage repository, prove current
  activation generation, identify private Compose input through target-scoped HMAC, mint
  a 60-3600 second snapshot, obtain and immediately verify a bounded rollback signature
  through ssh-agent using only command-installed public identity, and install owner-only.

## Complete deployment execution amendment

- **FR-052**: A new production candidate MUST bind the exact signed application
  revision and tracked manifest/Compose bytes from the selected clean application
  checkout. It MUST NOT rely on or alter a development checkout on the target.
  Complete candidate inputs MUST be published atomically with owner-only access,
  no replacement of retained inputs, bounded private transfer, and protected identities.
- **FR-053**: An explicit new private-input contract MAY support environment-backed
  Compose secrets only by capturing their exact registered values into candidate-owned
  files and binding the bytes, declared service mappings and mount identities with
  target-scoped HMAC. Application file-secret semantics MUST remain intact. Other
  external secrets/configs, uncaptured file dependencies and production source bind
  mounts MUST refuse. Secret material and raw verifiers MUST remain private per FR-016.
- **FR-054**: Private preparation MUST resolve source and broker authority under
  documented guards and use a distinct replay-safe preparation identity. Exact replay
  MUST reuse retained inputs without resolving secrets again. Changed content under
  the same identity MUST conflict; rotation requires an explicitly new preparation
  with no conflicting active owner. Admission expiry MUST NOT erase recovery inputs.
- **FR-055**: An explicit execution revision MUST bind a closed, acyclic dependency
  graph covering prerequisite services, ordered initializers, consumer services and
  dependency conditions. The graph MUST preserve declared initializer order, exact
  image membership and bounded phase deadlines. No dependency may start implicitly.
  For Lenzora, the exact queue prerequisite MUST be healthy before its topology gate;
  migrate, storage and topology initialization MUST precede their consumers.
- **FR-056**: Every prerequisite, initializer, consumer and edge phase MUST have a
  durable preparation/effect/terminal boundary. Initializer exit and termination
  evidence MUST be persisted before owned cleanup; cleanup completion MUST then be
  persisted before dependents proceed. Possible effects MUST remain monotonic even
  when later transport or persistence fails. All generation/edge/recovery authority
  MUST bind the complete required receipt chain, never infer completion from absence.
- **FR-057**: Readiness MUST converge through bounded read-only observations after
  the admitted runtime effect. Legitimate startup may remain pending until its deadline;
  changed image/configuration/target identity or incoherent evidence MUST fence the
  operation. Waiting MUST NOT repeat runtime effects. Commit requires fresh complete
  readiness and unchanged post-edge evidence.
- **FR-058**: First-target port reservation, runtime registration and route/provider
  setup MUST use the shared owner and existing provider mechanisms with bound intent
  and terminal receipts. Known provider/zone capability failures MUST refuse before
  initializer/runtime effects. A purge MUST NOT serve as a capability probe. Recovery
  may assemble an aggregate from exact durable acknowledgements without resubmission.
- **FR-059**: Observation recovery MUST resolve retained private selectors without
  current source regeneration or new secret access. Empty runtime after any possible
  prerequisite, initializer, consumer or edge effect MUST NOT authorize `no_effect`.
  Historical records lacking execution evidence MUST remain explicitly unknown.
- **FR-060**: New v2 forward acceptance and generations MUST retain the pre-forward
  compatibility subject/grant required by FR-034. Later rollback MUST validate that
  history, the previous private candidate and local images, and MUST NOT rerun forward
  initialization. A new post-forward signature MUST NOT replace missing history.
  Admission expiry and retained rollback validity MUST have distinct explicit meaning.
- **FR-061**: A separate operator settlement MAY record `abandoned_with_effects` only
  under exact machine-installed approval of a plan binding transaction, target/daemon,
  generation, quiescent processes, preserved container/data identities, backup and
  reviewed data-assessment evidence. Fresh observations MUST match before commit.
  Settlement MUST preserve original uncertainty, keep generation/current unchanged,
  perform no deployment/data/provider/deletion effects, and release proof custody only
  after its immutable terminal ownership result is durable. Deploy MUST NOT invoke it
  automatically; ordinary recovery MUST NOT use it to manufacture missing receipts.
- **FR-062**: Forward activation after settlement MUST name that exact predecessor
  and a separately reviewed data/schema compatibility decision. Missing or changed
  evidence MUST refuse. Settlement MUST NOT authorize adoption, rollback, automatic
  migration replay, source identity changes, or removal of retained data/artifacts.
- **FR-063**: Deployment control revision, application revision and Sandbox revision
  MUST be independently exact and reported. The application revision MUST match the
  receipt, manifest/Compose bytes, images, derived environment and running evidence.
  A reviewed control-only revision MAY operate on an unchanged signed application
  checkout; it MUST NOT relabel changed application input or bypass target uncertainty.
  Existing equal control/application defaults MUST remain compatible.
- **FR-064**: The opt-in development deployment path MUST own the complete effective
  prerequisite/initializer/consumer sequence exactly once and prepare environment-scoped
  source without changing another environment's live mounts. Legacy source selectors
  MUST remain readable; source relocation MUST be explicit and preserve mounted inputs.
  The development server mode and non-opt-in hosting behavior MUST remain compatible.
- **FR-065**: Deployment diagnostics MUST distinguish bounded fixed preparation,
  source, secret, runtime, provider, schema and recovery refusal reasons without arbitrary
  error output. Acceptance MUST include real disposable topology composition, private
  secret delivery, cold prerequisites, delayed readiness and crash/no-replay evidence
  in addition to local unit gates. Local, disposable runtime and production evidence
  MUST remain separately identified.
