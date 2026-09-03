# Feature Specification: Secure Private Image Staging

**Feature Branch**: `codex/feature-047-immutable-oci-clean`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Ready PRD at `specs/050-secure-image-staging/prd.md`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stage the Exact Authorized Image (Priority: P1)

A release operator submits one plan-authorized stage request and receives a proof
that the exact private image is present on the exact target.

**Why this priority**: Activation needs observed local image identity, not registry
intent or cache assumptions.

**Independent Test**: With a fake broker/helper/daemon, stage one exact plan and prove
one pull, one coherent observation, one ledger result, and zero activation effects.

**Acceptance Scenarios**:

1. **Given** staging policy authorizes the exact plan, target, helper, broker binding,
   credential-reference revision, and capability, **When** an operator submits a new
   request, **Then** the exact digest is pulled and one matching proof is recorded.
2. **Given** any authority differs, **When** staging is requested, **Then** it refuses
   before credential resolution or helper launch.
3. **Given** pull completes but one local identity fact differs, **When** proof is
   evaluated, **Then** staging is non-success and emits no proof.

---

### User Story 2 - Keep Credentials Inside the Fixed Staging Boundary (Priority: P1)

A security reviewer can show that the machine credential reaches only the fixed
broker recipient and measured helper and is absent after every terminal path.

**Why this priority**: Private image support is unsafe if activation, logs, state, or
untrusted commands can see the credential.

**Independent Test**: Use unique credential canaries across success/failure/signal/
timeout/cancellation/crash cases and scan every forbidden surface plus owned children.

**Acceptance Scenarios**:

1. **Given** a valid request, **When** credential handling begins, **Then** bytes travel
   only through the dedicated broker-to-helper channel and temporary volatile material.
2. **Given** any terminal or interrupted path, **When** the operation can be reported
   safely complete, **Then** no temporary credential material or owned descendant remains.
3. **Given** caller-controlled recipient/helper/auth input, **When** staging is requested,
   **Then** it refuses before credential resolution.

---

### User Story 3 - Replay and Reconcile Without Duplicate Helpers (Priority: P2)

An incident operator can resolve lost output or process interruption through one
durable stage identity.

**Why this priority**: Retrying a credential-bearing process without ownership proof
can duplicate work or hide leaked descendants.

**Independent Test**: Interrupt every ledger/process boundary and prove exact replay,
conflict refusal, single ownership, termination fencing, and stable uncertainty.

**Acceptance Scenarios**:

1. **Given** a terminal request, **When** exactly replayed, **Then** the identical result
   and proof are returned without a new helper or pull.
2. **Given** changed intent reuses a request ID, **When** submitted, **Then** it refuses.
3. **Given** process ownership, cleanup, or effect outcome is unproven, **When** replayed,
   **Then** the target stays uncertain and no different request proceeds.

---

### User Story 4 - Hand Off a Closed Staged Image Proof (Priority: P2)

Feature 051 can validate one canonical proof without credentials, staging policy, helper,
pull, or activation access; its only stage-repository capability is the authenticated
proof-custody port defined by FR-035–FR-037.

**Why this priority**: The activation boundary must consume evidence, not staging
implementation or secret authority.

**Independent Test**: Exact proof validates; mutation, partial fields, changed target/
daemon context, stale generation, and legacy receipts refuse without broker/helper calls.

**Acceptance Scenarios**:

1. **Given** a terminal successful stage, **When** its proof is validated, **Then** the
   complete canonical proof and digest match the ledger.
2. **Given** any proof field changes, **When** validated, **Then** it refuses.
3. **Given** old Feature 047/048 evidence, **When** presented as staging proof, **Then**
   it remains untouched and non-authorizing.
4. **Given** Feature 051 prepares an exact activation handoff, **When** validation,
   host-state acceptance, process death, replay, or terminal release occurs, **Then** the
   full proof stays pinned until the exact durable activation owner safely releases it.

### Edge Cases

- Structurally valid plan digest is not the exact digest authorized by staging policy.
- Registered target name is unchanged but stable machine or daemon identity changed.
- Helper path/name matches but installed artifact digest or runtime revision changed.
- Credential is revoked before resolution, during pull, or after local proof.
- Temporary area is persistent, symlinked, wrong owner/mode, full, or cannot be cleaned.
- Helper forks, double-forks, changes session, closes pipes, hangs, or emits oversize output.
- Pull reports success while RepoDigest/config/platform/image ID is absent or duplicated.
- Observation begins and ends across a daemon restart or image replacement.
- Ledger commit fails before/after helper effect or proof construction.
- Exact terminal replay arrives after evidence compaction.
- Compaction races activation proof validation or host-state durable acceptance.
- A controller crashes after the proof lease is durable but before/after host acceptance
  or accepted-pin promotion.
- Tombstone capacity is full while a new unique stage request arrives; retained replay is
  still resolved from existing proof/tombstone authority.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Staging MUST accept only a complete valid Feature 049
  `VerifiedImagePlan` and MUST validate its closed schema and digest without
  reinterpreting trust, provenance, signature, platform, topology, or image semantics.
- **FR-002**: Machine-owned staging policy MUST authorize the exact `plan_digest`,
  stable target/machine/daemon identity, helper identity, broker recipient/binding/
  version, opaque credential-reference revision, repository-read operation, and
  staging capability revision.
- **FR-003**: A changed or missing FR-002 value MUST refuse before credential resolution
  or helper launch.
- **FR-004**: Project/caller input MUST NOT supply or widen recipient, registry host,
  repository, digest, method, auth form, helper command, credential reference, or target.
- **FR-005**: Every stage operation MUST have a non-empty replay-safe request ID and
  immutable request digest binding FR-001/FR-002 values and starting generation.
- **FR-006**: Acceptance MUST be durable before credential resolution; acceptance-
  unknown MUST use read-only ledger lookup before replay with the same identity.
- **FR-007**: Request IDs MUST be single-use: exact terminal replay returns the recorded
  result; changed intent refuses.
- **FR-008**: The broker recipient MUST be fixed to repository-read for the exact
  canonical GHCR repository/digest in the plan.
- **FR-009**: Sandbox MUST guarantee operation-bound credential resolution/handling but
  MUST NOT claim an upstream credential is one-use or short-lived.
- **FR-010**: Credential bytes MUST reach only one measured immutable helper through a
  dedicated bounded channel after request acceptance and capability validation.
- **FR-011**: Credentials MUST NOT enter argv, inherited environment, project files,
  persistent managed state, durable job payload/results, logs, diagnostics, proof,
  public output, activation, containers, or Compose.
- **FR-012**: Temporary credential material MUST be volatile, service-user-owned,
  bounded, helper-owned, derived below `/run/user/<effective-uid>` rather than from
  caller input or environment, and removed before safe completion.
- **FR-013**: Helper cleanup MUST run for success, failure, cancellation, signal,
  timeout, and recoverable crash paths; unproven cleanup MUST be non-success.
- **FR-014**: Helper identity MUST bind canonical installed artifact, fixed entry,
  runtime revision, and closed invocation contract before credentials are resolved.
  Its digest-and-runtime-revision directory MUST be immutable so migration cannot
  rewrite authority held open by an active staging unit. The measured wrapper MUST
  traverse from an accessible absolute top-level component without following links;
  user-manager UID mapping at that component MUST NOT weaken exact service-user
  ownership below it.
- **FR-015**: Before credential resolution, staging MUST launch the measured helper in
  one uniquely named transient systemd user service backed by cgroup v2, with
  `KillMode=control-group`, no delegation, and no capability to move processes out of
  the unit cgroup; the exact unit/cgroup identity MUST be ledger-bound. Before READY,
  the wrapper and helper MUST return only a closed, bounded, phase/code failure frame;
  unknown output MUST become `bootstrap_unavailable` and MUST NOT open credential custody.
- **FR-016**: Kernel cgroup membership MUST be the descendant ownership authority.
  Cancellation/timeout MUST stop the whole unit, and safe termination requires the unit
  inactive plus `cgroup.events populated=0` (or removed cgroup) from the exact unit.
  PID, process group, elapsed time, lock expiry, or parent exit alone MUST NOT suffice.
  A retained exact failed/dead attempt is safe only after successful reset and a closed
  absent-unit recheck. Description drift MUST never authorize kill, stop, or reset.
- **FR-017**: Unproven process termination or cleanup MUST durably fence the target and
  refuse a different request.
- **FR-018**: Pull MUST use only the exact repository-qualified target-platform
  manifest digest from the plan, with no tag, build, retag, index/platform resolution,
  alternate registry, or implicit fallback.
- **FR-019**: Success MUST require one bounded coherent observation on one unchanged
  target/daemon context proving the exact unchanged Feature 049
  `DeliveryIdentityProjection`, anonymous denial for the exact manifest, authenticated
  exact RepoDigest, configuration digest, platform, topology, and immutable local image
  identity. Anonymous denial is registry visibility evidence, not Feature 049 authority.
- **FR-020**: Mixed epochs/contexts, drift, restart, duplicate, missing, partial,
  malformed, oversized, timed-out, or contradictory local evidence MUST refuse.
- **FR-021**: The stage ledger MUST be closed, versioned, bounded, secret-free, and
  bind request/plan/target/helper/broker/capability/generation/process/phase/cleanup/
  observation/terminal identities. Each target's complete serialized authority record,
  including full proofs, tombstones, and proof-custody leases/pins, MUST NOT exceed 16 MiB.
- **FR-022**: Ledger writes MUST use single-flight ownership, generation compare-and-
  set, atomic durable replacement, and immutable terminal results/tombstones.
- **FR-023**: An exact pre-effect request MAY resume only with durable no-effect plus
  complete process-termination/cleanup proof.
- **FR-024**: Possible pull/helper effect MUST be freshly reconciled; unproven outcome
  MUST return durable uncertainty, never optimistic replay.
- **FR-025**: Success MUST emit one closed, versioned, canonical, secret-free,
  tamper-evident `StagedImageProof`.
- **FR-026**: Proof MUST bind plan digest, staging-policy digest, request/digest, exact
  machine/target/daemon, helper artifact/runtime/capability, unchanged
  `DeliveryIdentityProjection` including topology, requested identity, observed
  RepoDigest/config/platform/local image identity, anonymous/authenticated registry
  access observation, observation identity, resulting staging generation, and a digest
  over all other fields.
- **FR-027**: Exact terminal replay during bounded full-proof retention MUST return a
  byte-identical proof. Each target MUST retain at most 64 total full proofs, including
  every leased or pinned proof, and at most 4096
  non-reusable tombstones. Compaction MUST replace only an unleased/unpinned proof with a
  tombstone, and exact replay MUST return stable `proof_expired` non-success; it MUST NOT
  reconstruct or authorize from the tombstone. Tombstones MUST NOT be deleted or recycled.
  Before creating an owner, acceptance MUST reserve terminal-identity capacity. A new
  unique request MUST return `retention_full` before ownership or effects whenever
  `tombstone_count == 4096`. Otherwise it may atomically compact enough unleased/unpinned
  full proofs to admit the request only when the post-reservation state has at most 64 total
  full proofs, fewer than 4096 tombstones, at most 64 live leases/pins, and at most 16 MiB.
  No admission may evict a leased/pinned proof or delete an identity. Altered, partial,
  unknown-field, stale-generation, or digest-mismatched proof
  MUST fail validation.
- **FR-028**: Credential revocation/unavailability before or during pull MUST fail and
  clean up. Revocation after exact proof MUST NOT give later phases credential access.
- **FR-029**: Staging MUST expose no Compose, init/migration, service replacement,
  health, edge, activation, adoption, rollback, prune, or production-effect capability.
- **FR-030**: Old Feature 047/048 state and receipts MUST remain untouched and MUST NOT
  authorize staging or proof.
- **FR-031**: Existing non-opt-in hosting, secret broker, durable-job, remote, and
  Feature 048 interfaces MUST remain compatible.
- **FR-032**: Public results MUST use stable bounded success/refusal/failure/cancelled/
  uncertain classes and MUST not include private paths or arbitrary helper output.
- **FR-033**: Documentation MUST distinguish Feature 049 trust, Feature 050 staging,
  Feature 051 activation, Feature 048 observation recovery, and production proof.
- **FR-034**: Local implementation validation MUST use synthetic credentials/fakes;
  live secrets, registry, remote mutation, deployment, and production need separate
  authorization. A credential-free measured-helper self-check MAY prove wrapper,
  transient-user-unit, hardening, cgroup, and volatile-workspace prerequisites, but
  MUST NOT read a broker source or contact a registry or container daemon.
- **FR-035**: Feature 050 MUST expose an authenticated activation-handoff operation that,
  before Feature 051 proof verification, durably prepares a lease bound to the exact
  activation request/digest, stage request/digest, proof digest, target, and stage-ledger
  generation, durable activation-owner/request holder identity, and finite admission
  deadline. The prepared lease MUST immediately pin
  the full proof against compaction. This proof-custody lease MUST be distinct from the
  broker credential lease and target effect lease.
- **FR-036**: The handoff MUST acquire locks in the order target-wide mutation lock,
  shared host-state transaction lock, then stage-ledger target lock, and release them in
  reverse. The stage lock and prepared pin MUST be held across proof verification and the
  atomic durable host-state acceptance. After acceptance, the lease MUST atomically promote
  to a durable accepted pin bound to the host acceptance receipt.
- **FR-037**: Exact replay MUST idempotently return/reconcile the same lease or pin. A crash
  after lease preparation MUST leave the proof pinned. Before host acceptance, deadline
  expiry MUST forbid new acceptance; the same durable holder MAY cancel only after an
  under-lock read proves acceptance absent. When exact host acceptance already exists,
  replay by that same holder MUST promote even after the deadline. Deadline expiry MUST NOT
  auto-unpin. Only the exact accepted activation owner MAY release the pin after terminal
  authority is durable. A process or unrelated recovery identity MUST NOT adopt, cancel,
  promote, or release it. At most 64 live leases/pins are permitted per target and their bytes count
  toward FR-021. Compaction MUST NOT create, promote, cancel, or release leases/pins, and capacity
  exhaustion MUST fail closed before activation acceptance.

### Key Entities

- **Staging Policy**: Machine authority for one plan, target, helper, broker binding,
  credential-reference revision, operation, and capability.
- **Stage Request**: Replay-safe immutable intent plus starting generation.
- **Owned Stage Process**: Exact helper/descendant ownership and lifecycle evidence.
- **Stage Ledger Record**: Durable single-flight phase/result authority.
- **Local Image Observation**: One coherent target/daemon epoch with exact identities.
- **StagedImageProof**: Closed canonical handoff proving exact local image staging.
- **StageProofTombstone**: Permanent request/proof digest and `proof_expired` result only;
  non-authorizing after bounded full-proof retention.
- **StageProofActivationLease**: Durable prepared or accepted pin binding one exact proof
  to one exact Feature 051 activation acceptance/terminal lifecycle.
- **Stage Result**: Stable terminal/replay/uncertainty envelope.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All invalid/stale/substituted authority cases refuse before credential
  resolution or helper launch.
- **SC-002**: Exact replay starts zero additional helpers/pulls and returns the identical
  retained proof or stable `proof_expired` after compaction; neither expiry nor conflicting
  reuse authorizes downstream work.
- **SC-003**: Credential canaries are absent from every forbidden surface and all
  terminal-safe cases leave zero temporary credential artifacts.
- **SC-004**: All cancellation/timeout/signal/crash cases either prove the exact systemd
  unit inactive plus its cgroup empty/removed and cleanup complete, or remain fenced.
- **SC-005**: Every success has one unchanged delivery projection, anonymous denial,
  authenticated exact RepoDigest/config/platform/image-ID/topology observation, and a
  matching complete canonical proof.
- **SC-006**: Every staging test reaches zero Compose/init/runtime/edge/rollback witnesses.
- **SC-007**: Non-opt-in and Feature 048 compatibility suites remain unchanged.
- **SC-008**: In every compaction/validation/acceptance/crash/replay race, a prepared or
  accepted lease prevents proof eviction; exact replay produces one pin/cancel outcome and
  no host acceptance references an expired proof.
- **SC-009**: At 4096 tombstones, 100% of new unique stage requests refuse as `retention_full`
  before effects and 100% of retained request IDs remain non-reusable.

## Assumptions

- Feature 049 plan validation is available before staging.
- Machine policy supplies an opaque repository-read credential reference.
- Supported targets expose a measurable immutable helper and coherent Docker observation.
- Feature 051 consumes plan/proof plus the narrow authenticated proof-custody repository
  port; it does not access stage policy, broker, credential, helper, pull, or effects.

## First-activation provisioning requirement

- **FR-050**: After exact v2 verification, protected stage preparation MUST authenticate
  machine/target, observe the exact daemon and measured v2 helper, derive binding ownership
  from the registered secret source, retain only its opaque revision, prove current stage
  generation/revision, and install the closed bundle owner-only.
