# Product Requirements Draft: Immutable Activation and Recovery

**Status**: Validated

**Created**: 2026-08-31

**Last Refined**: 2026-09-01

**Input**: "Consume an exact Feature 049 VerifiedImagePlan and Feature 050 StagedImageProof to perform target-wide single-flight immutable activation and rollback as one fenced transaction/state machine, with inspectable one-shot init, exact running proof, state recording, Feature 048 observation-only recovery integration, explicit adoption, and one-generation credential-free rollback; never reinterpret trust/signatures or receive raw registry credentials."

**Drafting Model**: `gpt-5.6-sol` High (configured planning worker; Terra Medium was not active)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Features 049 and 050 can approve and locally stage an exact private image without
changing a running service. Sandbox still needs a narrow activation boundary that can
replace selected services with that exact local image, run declared one-shot init work,
prove what is running, record a recoverable state transition, and restore only the one
previous proven generation when safe.

Activation is the first phase allowed to mutate Compose/runtime and edge state. A crash
between init, replacement, health, edge, or durable commit can make optimistic retry
unsafe. A separate activation service must therefore own one target-wide fence and one
transaction/state machine across activation and rollback. Feature 048 may observe and
reconcile that state after interruption, but must remain unable to deploy, pull, decide
trust, or obtain credentials.

## Users and Desired Outcomes

- **Machine owner**: Enables immutable activation for an exact target and selected
  service topology while retaining existing hosting behavior for non-opt-in projects.
- **Release operator**: Activates one plan/proof pair and receives exact running and
  durable-state evidence, never a best-effort success claim.
- **Incident operator**: Uses Feature 048 observation to determine whether interrupted
  activation or rollback reached the exact new or previous generation.
- **Recovery operator**: Explicitly adopts an already exact running generation or
  rolls back to the one retained locally proven prior generation without registry use.
- **Security reviewer**: Can prove activation does not reinterpret trust/signatures,
  invoke a broker, accept raw registry credentials, pull, build, or tag images.

## Goals

- Consume exactly one complete valid `VerifiedImagePlan` and matching retained
  `StagedImageProof`, but authorize them only through a machine-owned exact activation
  binding plus an authenticated crash-safe Feature 050 proof-custody lease/pin held from
  before proof verification through durable activation acceptance.
- Fence activation, adoption, rollback, and Feature 048 recovery reconciliation with
  target-wide single-flight ownership and generation compare-and-set.
- Render and validate a closed exact service topology before effects, with selected
  services forced to local repository-qualified digest references.
- Run declared one-shot init work through an inspectable create-before-start lifecycle
  and record effect boundaries so unknown execution is never silently repeated.
- Replace services without build or pull, then prove exact container/image identity,
  topology, health, and one coherent target/runtime observation.
- Record current and one previous verified activation generation without secrets.
- Let Feature 048 provide read-only observations for Feature 051-owned reconciliation.
- Support explicit adoption only when the running target already exactly satisfies the
  current plan/proof and the plan declares zero init steps.
- Provide one-generation rollback using only the retained locally proven prior image
  and a machine-owned pre-forward grant over a deterministic current-target/forward-
  candidate subject that the eventual terminal generation references.
- Preserve existing non-opt-in hosting, Feature 048, CLI, state, and remote contracts.

## Non-Goals

- Receipt, provenance, signature, digest, repository, platform, topology-policy, or
  trust decisions owned by Feature 049.
- Registry authentication, broker access, credential handling, pull, RepoDigest proof,
  helper lifecycle, or staging ledger work owned by Feature 050.
- Accepting raw registry credentials, credential references, registry tokens, tags,
  indexes, alternate images, or caller-provided trust exceptions.
- Building, publishing, signing, scanning, promoting, retagging, pruning, or deleting
  images.
- Database backup/restore, schema downgrade, data rollback, multi-generation history,
  or automatic rollback.
- Treating Feature 048 as an activation executor or allowing it to infer success from
  time, lock expiry, process absence, health alone, or stale state.
- Claiming local/unit acceptance as live GHCR, remote-host, edge, deployment, or
  production proof.

## Product Scenarios

### Scenario 1 — Activate one exact staged generation

- **Starting state**: The target is idle, the plan and proof exactly match, the staged
  local image and service topology are unchanged, and the starting generation matches.
- **User action**: An operator submits and confirms one replay-safe activation request.
- **Expected outcome**: Sandbox durably accepts the transaction, completes required
  init once, replaces only selected services with no build/pull, proves exact running
  identity and health, completes existing edge readiness, and commits one generation.

### Scenario 2 — Run inspectable one-shot init safely

- **Starting state**: The plan declares ordered init work for the staged image.
- **User action**: Activation reaches an init step.
- **Expected outcome**: Sandbox creates but does not start the exact container, inspects
  its image/config/topology, durably records the effect boundary, then starts and
  observes a bounded successful exit. Possible execution without a terminal receipt
  remains uncertain and cannot be repeated automatically.

### Scenario 3 — Reconcile an interrupted transaction

- **Starting state**: Caller output is lost or the controller stops before activation
  or rollback commits.
- **User action**: Feature 051 invokes the Feature 048 observer before and after its own
  non-authorizing provisional write.
- **Expected outcome**: Feature 048 returns exact evidence values without writing. Only
  matching pre/post `exact_new` evidence may perform a phase-legal promotion. Matching
  `exact_prior` may close a proven pre-effect transaction without generation advance;
  `neither` and `ambiguous` always remain non-promoting and fenced.

### Scenario 4 — Explicitly adopt an exact running generation

- **Starting state**: No active transaction exists and out-of-band work left the exact
  planned/staged generation running and healthy.
- **User action**: An operator requests and confirms adoption with a fresh generation.
- **Expected outcome**: Sandbox proves all activation facts for a plan that declares
  zero init steps and records the generation without pull, init, or service change.
  Any declared init work or missing fact refuses adoption.

### Scenario 5 — Roll back one proven generation

- **Starting state**: Current and previous generations are terminally recorded; the
  previous exact image remains locally proven; and the current terminal generation
  references a machine grant/subject created before forward acceptance that bound its
  forward candidate and rollback-target generation, configurations, topology, init/data
  contract, and still-current policy revision.
- **User action**: An operator explicitly confirms rollback.
- **Expected outcome**: The same activation transaction/state machine switches to the
  retained prior generation with no credential or registry access, proves it running,
  completes edge readiness, and atomically records the reversal. Any missing or
  changed prerequisite refuses before runtime effects.

## Proposed Product Behavior

- Activation treats caller 049/050 artifacts as claims, not authority. A machine-owned
  activation binding binds exact plan/proof/stage request/staging-policy/staging-generation/
  ledger-authority identities and canonical delivery projection. Before verifying the proof,
  051 must acquire a Feature 050 prepared proof-custody lease that durably pins the same
  retained terminal proof. It holds that pin through atomic durable host-state acceptance
  and then promotes it to an accepted pin before any activation effect. Missing, compacted,
  tombstoned, lease-capacity-exhausted, or expired-before-acceptance proof authority refuses.
- Proof custody uses the fixed lock order target-wide mutation lock, shared host-state
  transaction lock, then stage-ledger target lock, releasing in reverse. The prepared lease
  binds the durable activation-owner/request holder and a finite admission deadline, but
  crash or expiry never auto-unpins. Expiry forbids new acceptance. Replay by that same
  holder promotes an already committed exact acceptance even after the deadline or cancels
  only after proving acceptance absent. Process and unrelated recovery identities gain no
  holder rights. Only the exact accepted activation owner can release the pin after terminal
  authority is durable.
- Activation accepts only closed, digest-valid 049/050 artifacts whose plan, target,
  repository-qualified manifest digest, config digest, platform, topology, daemon
  context, and staging generation match exactly. It cannot import or call trust,
  receipt, broker, credential, helper, or pull mechanisms.
- Machine-owned activation policy enables an exact target, selected services, init
  declarations, runtime/Compose capability, edge policy, and state revision. Project
  input may narrow but cannot widen the policy or replace any image identity.
- One target-wide single-flight fence serializes activation, adoption, rollback, and
  Feature 048 reconciliation with all other hosting mutations for that target. Lock
  expiry or process absence alone never transfers ownership.
- Every request has one immutable digest and expected starting generation. Durable
  acceptance precedes effects. Exact terminal replay returns the recorded result;
  changed reuse refuses; acceptance-unknown uses ledger lookup before replay.
- Activation and rollback are operation types in one closed transaction state machine,
  share the same ownership/generation/state records, and cannot be executed through
  separate unfenced paths.
- Pre-effect checks render the exact selected service topology and reject aliases,
  tags, builds, pulls, platform overrides, duplicate/missing services, mutable image
  substitutions, unsafe dependencies, unexpected orphans, and conflicting runtime
  mutations.
- Each declared init step is created without start, then inspected for exact planned
  image/config/platform, command, mounts, networks, environment-key names, privilege,
  and dependency scope. Secret values are neither persisted nor exposed. The state
  machine records `effect_entered` durably before start and a bounded exit receipt
  after complete termination.
- A missing terminal init receipt after possible start is durable uncertainty. Feature
  048 may observe/reconcile exact evidence but neither activation nor rollback may
  guess or automatically repeat the init effect.
- Long-lived replacement uses only the already local exact digest, with build and pull
  disabled. Success requires a coherent post-change observation proving every selected
  container's declared image reference, local image identity, repository/config digest,
  platform, topology, runtime generation, and required health.
- Existing edge readiness remains part of a successful transaction. Runtime success
  without exact edge completion is recorded as incomplete, never activation success.
  The edge step uses an immutable request identity within the same Feature 051
  transaction. A proven-not-entered edge phase may resume only for that exact request.
  Acceptance-unknown or interrupted delivery first performs read-only lookup in the
  existing edge replay authority; an exact terminal receipt may be promoted after a
  fresh unchanged runtime observation. Possible delivery without an authoritative
  terminal receipt remains fenced and cannot be retried, committed, adopted, or rolled
  back until an operator resolves the external edge state through an authorized path.
- Durable state stores a closed current generation, at most one previous verified
  generation, the active/terminal transaction, exact artifact/proof/observation
  digests, non-secret configuration identity, topology, init receipts, edge result,
  and generation counters. It stores no credential, secret value, arbitrary output,
  or private temporary path.
- The existing shared recovery repository remains the sole outer `hosts.json` parser,
  writer, transaction locker, atomic replacer, and fsync owner. Feature 051 supplies only a
  closed nested candidate through its narrow port; `_hosting.py` registers capabilities but
  cannot become a second writer. Legacy and unknown sibling fields remain preserved.
- Feature 051 exposes a distinct replay-safe `sb host image recover` action. It calls a
  new Feature 048 read-only activation observer but never repurposes existing failed-apply
  `sb host recover`. Feature 048 cannot write a provisional owner or start/stop services,
  execute init, update edge, activate, roll back, pull, or decide trust.
- Under one shared lock/CAS, Feature 051 obtains a first fresh Feature 048 observer value,
  validates it, and durably stores only a 051-owned bounded non-authorizing provisional
  marker containing its exact evidence identity and unchanged-epoch boundaries. That marker
  publishes no recovery success, transaction promotion, receipt, generation advance, or
  effect authority. Feature 051 immediately asks the read-only Feature 048 observer again
  under the same durable owner, requires byte-exact pre/post evidence identity and epoch/
  transaction/generation equality, then separately atomically stores the immutable recovery
  result plus any allowed transaction promotion and clears the provisional marker. A crash
  from the exact provisional phase resumes only the post-write observation. Contradictory,
  partial, stale, mixed-epoch, changed, or unavailable post-evidence atomically records a
  stable non-success result, clears the provisional, and leaves the activation transaction
  fenced without promotion. Persistence uncertainty leaves the provisional in place. Exact
  terminal replay returns the one result.
- Adoption uses a new explicit replay-safe request and confirmation. V1 adoption is
  permitted only when the exact current plan declares zero init steps. It requires the
  exact current 049/050 artifacts, local proof, running proof, and health/edge evidence,
  and performs no runtime or init effect. Caller/project/legacy receipts, health, or
  out-of-band claims cannot substitute for the zero-init condition.
- Rollback requires the one retained prior generation, its exact local proven image,
  unchanged target/daemon identity, and an exact machine-owned
  `RollbackCompatibilityGrant` issued before forward acceptance. Its deterministic
  `ForwardRollbackSubject` binds the current rollback-target generation, forward
  candidate plan/proof/activation-authority, forward non-secret configuration/topology,
  init/data contract, target, and policy revision; it never refers to the not-yet-created
  result generation. Forward acceptance persists subject/grant digests and the terminal
  generation references them.
  Caller/project claims, post-hoc grants, stale bindings, or uncertain init/data state
  refuse before effects. Rollback never contacts a registry; first-generation rollback
  is unavailable.
- The target-wide owner is shared with activation, adoption, rollback, Feature 048
  reconciliation, normal apply, source sync, login/setup, edge continuation, and every
  registered target mutation. Unknown/unregistered mutation capability fails closed.
- Legacy state and receipts remain readable and opaque. They never authorize 049, 050,
  activation, adoption, or rollback. Non-opt-in paths retain prior behavior and public
  result shapes; additive fields are optional for old readers.

## Constraints and Dependencies

- Feature 049 and Feature 050 are strict prerequisites and their closed outputs are
  immutable inputs, not libraries of authority to reinterpret.
- Feature 048 remains the observation/reconciliation integration point and must keep
  its existing safe host-recovery guarantees.
- The existing hosting state, remote execution, Compose, edge, and durable request
  services remain authoritative for their mechanisms; this feature adds policy and
  orchestration through registered boundaries.
- Activation must prove local image presence before all effects and must configure the
  runtime to refuse build and registry pull.
- Process execution has finite deadlines, bounded streams, closed synthetic
  environments, explicit ownership, cancellation, and termination evidence.
- Live remote mutation, edge changes, deployment, or production use requires separate
  authorization; specification and local tests use fakes and synthetic values.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Feature identity | Feature 051 | It consumes 049/050 and owns activation/recovery | Task owner |
| Inputs | Exact closed `VerifiedImagePlan` plus `StagedImageProof` | Prevents trust/staging reinterpretation | Task owner |
| Activation authority | Machine binding plus crash-safe Feature 050 prepared lease/accepted pin | Prevents proof compaction between validation and durable host acceptance | Consolidated security repair |
| Transaction model | Activation and rollback in one target-fenced state machine | Prevents split-brain recovery paths | Task owner |
| Init model | Inspect before start; durable effect boundary; no automatic uncertain replay | Init may be non-idempotent | Task owner |
| Runtime source | Local exact digest only; no build/pull | Registry authority ends at Feature 050 | Task owner |
| Recovery | Feature 048 observation-only reconciliation | Preserves safe host recovery | Task owner |
| Adoption | Explicit, exact, no-effect, zero-init plans only | Running image alone cannot prove an out-of-band init | Task owner |
| Adoption init authority | Zero-init plans only in v1 | Out-of-band init cannot produce an authoritative Feature 051 receipt | Task owner approval |
| Rollback | One retained locally proven prior generation plus pre-forward machine grant | Binds deterministic rollback target/candidate compatibility before effects | Task owner approval |
| Rollback subject | Deterministic current-generation plus forward-candidate subject persisted at acceptance | Future terminal generation digest does not exist pre-forward | Consolidated review remediation |
| Edge interruption | Same transaction/request replay authority; uncertainty stays fenced | Feature 048 cannot perform edge effects | Task owner approval |
| Recovery entry | Distinct `sb host image recover`; Feature 048 observes twice; 051 owns non-authorizing provisional and atomic promotion | Preserves failed-apply recovery and proves unchanged epoch through durable commit | Consolidated security repair |
| Compatibility | Cross-cutting acceptance, not another feature | Existing consumers must remain stable | Task owner |

## Open Questions

- None.

## Acceptance Outcomes

- Every changed, stale, partial, malformed, mixed, or substituted plan/proof/target/
  topology/configuration/generation refuses before activation effects.
- Caller-supplied artifacts without the exact machine activation binding and prepared/
  accepted Feature 050 proof pin always refuse before activation effects; compaction cannot
  race verification or durable acceptance.
- Activation/adoption/rollback/reconciliation show at most one target owner and one
  generation transition across all crash/replay schedules.
- Every init step is inspected before start and has either a complete exact receipt or
  durable uncertainty; possible execution is never automatically repeated.
- Every activation/rollback success proves exact selected running image identity,
  topology, health, edge completion, and atomic terminal state from one coherent epoch.
- Adoption performs zero init/runtime/edge effects, is unavailable for any plan with
  init work, and succeeds only with exact current proof.
- Rollback performs zero broker/credential/registry/pull/build work and can select only
  the one retained locally proven prior generation authorized before activation by the
  exact current machine grant.
- Interruptions before edge entry resume only the exact request; interruptions after
  possible edge delivery either promote one authoritative terminal receipt after fresh
  runtime proof or stay fenced with no activation success or second edge effect.
- Every pairwise race among activation, adoption, rollback, Feature 048 reconciliation,
  apply, sync, login/setup, edge continuation, and other registered target mutations
  yields one owner/generation, zero interleaved effects, and unchanged legacy results
  for the losing/refused operation.
- Credential canaries and trust-policy calls have zero activation witnesses.
- Every activation-recovery request durably records at most one non-authorizing 051
  provisional marker, requires matching pre/post observation identities, and produces at
  most one later atomic recovery-result/transaction-promotion replacement. Feature 048
  performs no write and failed-apply recovery remains unchanged.
- Non-opt-in hosting and existing Feature 048 compatibility suites remain unchanged.

## Risks and Assumptions

- **Risk**: An init effect may run but lose its exit receipt. Fencing and human
  reconciliation are safer than automatic replay.
- **Risk**: Out-of-band Compose or daemon changes can invalidate local staging or
  running evidence. Coherent fresh observation is mandatory.
- **Risk**: A prior image may be locally removed or incompatible with current config/
  data. Rollback refuses rather than pulls or guesses.
- **Risk**: Edge completion can lag exact runtime replacement. The transaction remains
  incomplete and inspectable until reconciled.
- **Assumption**: Feature 050 proves an immutable local image ID on the same target/
  daemon context used by activation.
- **Assumption**: Existing hosting and Feature 048 can carry additive bounded state
  without breaking old readers.

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

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
