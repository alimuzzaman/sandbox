# Product Requirements Draft: Secure Private Image Staging

**Status**: Validated

**Created**: 2026-08-31

**Last Refined**: 2026-09-01

**Input**: "Consume one validated Feature 049 VerifiedImagePlan and securely stage its exact private GHCR target-platform image through a fixed broker recipient and immutable trusted helper, using temporary credential handling, exact pull and local RepoDigest/config/platform proof, and an idempotent stage ledger that emits StagedImageProof without Compose, edge, init, runtime activation, or trust reinterpretation."

**Drafting Model**: `gpt-5.6-sol` High (configured planning worker; Terra Medium was not active)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-31

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Feature 049 can decide which exact image is trusted but deliberately cannot contact
a registry or host. Activation must not receive raw registry credentials or discover
whether a pull really produced the approved local image. Without a separate staging
boundary, authentication, helper execution, process interruption, local image proof,
and replay can leak into activation and weaken both trust and recovery.

Sandbox needs one staging operation that consumes a closed verified plan, acquires
only machine-owned read access for its exact private repository, pulls through one
trusted helper, proves the exact local image identity, and records one replay-safe
result. It stops before Compose, init, runtime, edge, or deployment state.

## Users and Desired Outcomes

- **Machine owner**: Chooses the fixed broker binding, read-only credential reference,
  helper identity, target host, and staging capability without exposing values.
- **Release operator**: Submits one replay-safe stage request and receives a truthful
  `StagedImageProof` or bounded refusal/uncertainty.
- **Incident operator**: Can replay after disconnect or process loss without starting
  a second untracked helper or mistaking ambiguity for success.
- **Security reviewer**: Can prove credentials reach only the fixed recipient/helper,
  are temporary, and never cross into activation or public/persistent evidence.

## Goals

- Consume a complete valid Feature 049 `VerifiedImagePlan` without reinterpreting
  trust, provenance, signature mode, platform, topology, or digest semantics.
- Bind one stage request to the exact plan digest authorized by machine-owned staging
  policy, target identity, helper identity, broker policy/binding revisions, and
  starting ledger generation.
- Resolve a machine-owned repository-read credential only after durable acceptance.
- Deliver credential bytes only to one immutable trusted helper for the exact approved
  GHCR repository/digest and remove all temporary credential material on every exit.
- Own and bound the full helper/Docker descendant lifecycle; uncertain termination
  remains fenced.
- Pull the exact digest and prove local repository digest, image configuration digest,
  platform, and local image identity before success.
- Make exact replay idempotent and changed reuse refuse.
- Emit one bounded secret-free `StagedImageProof` that Feature 051 can validate.
- Give Feature 051 a crash-safe proof lease/pin handoff that prevents proof compaction
  from racing validation or durable activation acceptance.
- Bound full-proof and tombstone retention; refuse new staging acceptance when identity
  retention is saturated instead of deleting request history.
- Preserve existing non-opt-in hosting and Feature 048 recovery behavior.

## Non-Goals

- Trust, provenance, signature, digest, platform, or topology policy decisions.
- Compose, init/migration execution, service replacement, health checks, edge work,
  activation, adoption, rollback, or production proof.
- Building, signing, publishing, promoting, retagging, copying, deleting, pruning, or
  scanning images.
- Passing credentials to Feature 051, a project process, Compose, a container, or any
  public result.
- Claiming an upstream PAT is short-lived or one-use when Sandbox cannot enforce it.
- Supporting registries other than GHCR, mutable tags, index resolution, or foreign
  platforms in v1.
- Automatically adopting an image that lacks a current Feature 049 plan and a current
  Feature 050 stage proof.

## Product Scenarios

### Scenario 1 — Stage one exact private image

- **Starting state**: A current validated plan, registered target, broker binding,
  immutable helper, and compatible staging capability are exact.
- **User action**: An operator submits and confirms one replay-safe stage request.
- **Expected outcome**: Sandbox durably accepts the request, handles the credential
  only inside the fixed staging boundary, pulls the exact digest, removes temporary
  credential material, proves the exact local identity, and returns one proof.

### Scenario 2 — Refuse authority or capability drift

- **Starting state**: The plan, target, broker binding, helper identity, credential
  reference revision, capability, or requested repository/digest differs or is stale.
- **User action**: An operator stages the image.
- **Expected outcome**: Sandbox refuses before credential resolution or helper launch.

### Scenario 3 — Recover after output or process loss

- **Starting state**: Acceptance output is lost, the caller disconnects, or the helper
  stops during credential setup, pull, cleanup, proof, or ledger commit.
- **User action**: The same request is replayed.
- **Expected outcome**: Sandbox returns its recorded result, resumes only after exact
  preconditions and complete process termination are proven, or returns a durable
  uncertainty. No different request can bypass the fence.

### Scenario 4 — Credential revocation or pull failure

- **Starting state**: The machine credential is revoked, expired, unavailable, or
  denied before/during pull.
- **User action**: Staging runs.
- **Expected outcome**: Pull fails, all temporary material and descendants are cleaned,
  and no proof is emitted. Revocation after exact local proof does not invalidate the
  image identity; no later phase receives or needs the credential.

## Proposed Product Behavior

- The only image authority input is a complete valid Feature 049 plan. Staging checks
  its closed schema/digest and exact target scope but never re-decides trust.
- Machine-owned staging policy binds the one authorized Feature 049 `plan_digest`,
  stable target identity, exact helper artifact identity, broker recipient/binding/
  version, opaque credential reference revision, repository-read operation, and
  capability revision. Project input cannot widen it. A different plan digest is
  stale for this staging policy and refuses; staging does not reinterpret why.
- The broker recipient is fixed to GHCR repository-read for the plan's exact canonical
  repository and digest. Caller-controlled registry host, path, method, auth form, or
  helper command is forbidden.
- Sandbox guarantees one-operation credential resolution/handling, not upstream token
  expiry or uniqueness. The machine owner must supply the narrowest available
  read-only repository grant.
- Credential bytes enter only the trusted staging helper through a dedicated bounded
  channel. They never enter argv, inherited environment, project files, durable state,
  job metadata, logs, diagnostics, proof, or activation.
- Temporary credential material is helper-owned, volatile, owner-only, bounded, and
  removed before the helper can report completion. Cleanup runs on success, failure,
  cancellation, signal, timeout, and normal crash reconciliation. Unproven cleanup is
  non-success.
- The measured helper runs in one uniquely named transient systemd service backed by
  cgroup v2, with `KillMode=control-group`, no delegation, and no permission to escape
  the unit cgroup. Kernel cgroup membership is descendant authority. Timeout/cancellation
  stops the unit; safe termination requires the exact unit inactive plus an empty or
  removed cgroup. PID, process group, and time alone never prove termination.
- The pull request uses only the repository-qualified target-platform manifest digest
  from the plan. No tag, build, alternate platform, or implicit index resolution occurs.
- Local proof copies the Feature 049 `DeliveryIdentityProjection` unchanged and compares
  its exact requested repository digest, topology, image configuration digest, platform,
  and immutable local image identity from one bounded coherent
  observation on the same target and daemon context. Mixed epochs/contexts, drift
  during observation, or missing, duplicate, partial, changed, and unbounded evidence
  refuses. An anonymous exact-manifest request must be denied before the authenticated
  exact pull; this is Feature 050 registry visibility evidence, not Feature 049 trust.
- The durable stage ledger binds request ID, request digest, plan digest, target/helper/
  broker/capability identities, generation, process ownership, phase, cleanup result,
  and terminal proof/result. It contains no secret.
- Exact completed replay returns the byte-identical proof while it is retained. Bounded
  compaction replaces an unpinned full proof with a non-reusable tombstone. The store
  retains at most 64 total full proofs including pinned proofs, 4096 tombstones, 64 live
  proof leases/pins, and 16 MiB
  of total serialized authority per target. Acceptance reserves terminal identity capacity;
  at `tombstone_count == 4096`, every new unique stage request refuses as `retention_full`
  before owner, credential, or helper effects regardless of spare proof/byte capacity;
  no tombstone is deleted and no old request ID becomes reusable. Later exact replay of a
  tombstone returns `proof_expired` non-success and cannot authorize activation. Changed
  intent reusing an ID refuses. Lost acceptance output is resolved from the ledger before
  resubmission.
- Before Feature 051 validates a retained proof, it uses an authenticated Feature 050
  repository operation to durably prepare an activation lease bound to the exact activation
  request/digest and proof/request/digest. Preparing the lease immediately pins the full
  proof against compaction. Feature 051 holds that pin through its durable host-state
  acceptance, then atomically promotes the lease to an accepted activation pin. A crash
  between those writes leaves the prepared lease pinned. The holder is the durable
  activation-owner/request identity, never a process or unrelated recovery identity.
  Expiry forbids new host acceptance but same-holder replay promotes an already committed
  acceptance after the deadline or cancels only proven absence; exact replay reconciles the
  durable host acceptance and either promotes the same pin or cancels it only after proving
  that acceptance did not commit. Only the exact activation owner may release the pin after
  its terminal authority is durable; compaction never changes lease/pin ownership.
- An exact pre-effect request may resume only after durable no-effect and termination
  proof. Any possible pull/helper effect is freshly reconciled; unproven process or
  cleanup state remains uncertain.
- `StagedImageProof` is closed, versioned, canonical, secret-free, and tamper-evident.
  It binds the exact plan/projection/topology, machine/target/daemon context,
  helper/runtime/capability, requested and observed image identities, registry-access
  observation, coherent observation identity, request, resulting staging
  generation, and a digest over every other proof field. Exact completed replay
  returns the identical proof; altered or partial proof fails downstream validation.
- Feature 051 receives only the proof and plan. It cannot request or receive raw
  credentials through this contract.

## Constraints and Dependencies

- Feature 049 is a strict prerequisite and sole trust authority.
- The existing supported secret broker remains the only credential resolver; direct
  secret files, environment lookup, raw reveal, shell interpolation, or caller-provided
  tokens are forbidden.
- The helper artifact and runtime revision must be measured and exact before credential
  resolution. Missing or changed capability fails closed.
- Durable acceptance must use the existing replay-safe job/ledger rules, including
  acceptance-unknown lookup before idempotent replay.
- Cross-store proof handoff uses one lock order: target-wide mutation lock, shared host-state
  transaction lock, then Feature 050 stage-ledger target lock; release is reverse order.
  A caller holding a later lock may not acquire an earlier one.
- Process children use a closed synthetic environment and bounded input/output.
- No Compose/edge/runtime function is reachable from the staging service.
- Live GHCR, secret, remote, deployment, or production access requires separate
  authorization; specification and local tests use synthetic values and fakes.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Feature identity | Feature 050 | It consumes Feature 049 and precedes activation | Task owner |
| Trust input | Closed `VerifiedImagePlan` only | Staging must not reinterpret trust | Task owner |
| Credential promise | Sandbox operation-bound handling; no upstream lifetime claim | PAT properties are outside Sandbox control | Task owner approval |
| Broker recipient | Fixed GHCR repository-read for exact plan repository/digest | Prevents scope/recipient substitution | Task owner |
| Helper | Machine-owned immutable measured helper | Credential-bearing code must be fixed before resolution | Task owner |
| Temporary handling | Volatile owner-only material with mandatory cleanup | Enables exact pull without durable credential storage | Task owner |
| Plan freshness | Machine staging policy authorizes one exact `plan_digest` | Staging validates authority binding without reinterpreting Feature 049 trust | Task owner |
| Replay | Durable request/plan/target/helper/broker generation binding | Prevents duplicate untracked staging | Existing durable-job policy |
| Output | Closed canonical secret-free `StagedImageProof` with its own digest | Feature 051 needs stable tamper-evident evidence, never credentials | Task owner |
| Activation handoff | Durable prepared lease pins proof before validation; accepted pin survives through 051 terminal authority | Prevents lookup/compaction TOCTOU and makes cross-store crash recovery idempotent | Consolidated security repair |
| Retention saturation | 64 total full proofs including pinned proofs, 4096 tombstones, 64 live proof pins, and 16 MiB per target; tombstone-full always refuses new unique requests before ownership | Preserves request-ID non-reuse without unbounded growth | Consolidated security repair |

## Open Questions

- None.

## Acceptance Outcomes

- Every stale, changed, substituted, malformed, unsupported, or incomplete plan,
  target, broker, helper, capability, request, or proof refuses before unsafe effects.
- Exact replay never starts a duplicate helper/pull; conflicting ID reuse always refuses.
- Proof compaction cannot pass a prepared lease or accepted pin. Every crash point around
  lease preparation and host acceptance resolves to the same pin/cancel result on exact
  replay, and only an exact terminal activation owner can unpin.
- At 4096 tombstones, new unique staging requests refuse before effects while all retained request
  identities remain non-reusable.
- Credential canaries appear only in the dedicated broker/helper channel and temporary
  volatile material, and appear on zero persistent/public/activation surfaces.
- Success, failure, cancellation, timeout, signal, and crash cases leave zero live
  owned descendants and zero temporary credential artifacts before safe completion.
- Every success proves exact requested/local RepoDigest, configuration digest,
  platform, and local image identity from one unchanged target/daemon observation and
  emits one matching canonical proof whose exact replay is byte-identical.
- Compose, init, service, health, edge, adoption, rollback, and production-effect
  witnesses remain zero for every staging result.
- Non-opt-in and Feature 048 regression behavior remains unchanged.

## Risks and Assumptions

- **Risk**: A machine PAT may be broader or longer-lived than staging needs. Sandbox
  can bound handling but cannot change upstream scope/lifetime.
- **Risk**: Process or cleanup ambiguity can leave credentials or pulls uncertain.
  Target fencing and fresh reconciliation are safer than optimistic replay.
- **Risk**: Local image metadata can be partial or race with out-of-band changes.
  One coherent bounded observation is required.
- **Assumption**: The remote platform provides a verifiable volatile owner-only area
  and exact Docker image inspection.
- **Assumption**: The supported broker can bind repository-read use to the fixed helper.
- **Assumption**: Feature 051 can activate from local proof without registry access.

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
