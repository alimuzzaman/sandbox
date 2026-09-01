# Feature Specification: Immutable Activation and Recovery

**Feature Branch**: `codex/feature-047-immutable-oci-clean`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Ready PRD at `specs/051-immutable-activation-recovery/prd.md`

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
  049/050 schema revisions.
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
- **FR-014**: Every init step MUST be ordered, bounded, and created without start before
  its exact container configuration is inspected.
- **FR-015**: Pre-start init inspection MUST prove exact image reference/local identity,
  config/platform, command, mounts, networks, environment-key names, privilege,
  dependency scope, target, and runtime epoch; any mismatch MUST remove without start.
- **FR-016**: Init environment secret values MUST NOT enter persisted state, receipts,
  logs, diagnostics, public output, or inspection evidence.
- **FR-017**: The state machine MUST durably record an init `effect_entered` boundary
  immediately before start and one bounded terminal exit/termination receipt afterward.
- **FR-018**: Possible init start without an exact terminal receipt MUST be durable
  uncertainty and MUST NOT be automatically repeated, adopted, committed, or rolled back.
- **FR-019**: Init execution MUST have finite deadlines, bounded streams, explicit
  ownership/cancellation, and complete container/process termination evidence.
- **FR-020**: Activation/rollback success MUST require one coherent fresh observation
  proving every selected container's exact declared image reference, local image identity,
  repository/config digest, platform, topology, runtime generation, and required health.
- **FR-021**: Missing, partial, duplicate, contradictory, stale, changing, mixed-epoch,
  oversized, or timed-out running evidence MUST be non-success.
- **FR-022**: Edge readiness MUST be an immutable sub-request of the same 051 transaction
  and MUST complete before activation/rollback success.
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
  zero witnesses in 051 state, output, logs, adapters, and process inputs.
- **SC-009**: Non-opt-in and legacy Feature 048 compatibility suites retain their
  existing results and old opaque state is byte-preserved by no-op/read paths.

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
