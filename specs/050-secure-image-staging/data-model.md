# Data Model: Secure Private Image Staging

## StagingPolicy

- authority identity/digest/revision
- exact authorized `plan_digest`
- stable target/machine/daemon scope
- fixed helper artifact/entry/runtime revision
- fixed broker recipient/binding/version and opaque credential-reference revision
- exact repository-read operation and capability revision

## StageRequest

- schema version, request ID/digest, expected ledger generation
- complete `VerifiedImagePlan`
- exact staging-policy digest and target
- confirmation

## StageLedgerRecord

- request/plan/policy/target/helper/broker/capability identities
- generation and single-flight owner
- phase and effect-entry boundary
- process ownership/termination summary
- cleanup summary
- coherent observation identity
- terminal result or non-reusable tombstone
- no credential, raw frame, argv/environment, output, or private path

## OwnedStageProcess

- launch identity; helper artifact/entry/runtime identity
- exact transient systemd unit and cgroup-v2 identity; no delegation/escape capability
- unit state plus recursive cgroup `populated`/removal termination proof
- credential workspace cleanup state

## LocalImageObservation

- target/machine/daemon context and matching start/end epoch
- exact unchanged Feature 049 `DeliveryIdentityProjection` and topology
- anonymous exact-manifest denial plus authenticated access identity
- requested owner/repository/manifest/config/platform
- exact observed RepoDigest/config/platform/local image ID
- bounded phase results; `complete` only when epoch unchanged

## StagedImageProof

- closed schema and proof digest
- request ID/digest, plan digest, staging-policy digest
- exact machine/target/daemon and helper artifact/runtime/capability identities
- unchanged delivery projection/topology plus requested and observed identities
- registry-access and coherent observation identities
- resulting staging generation
- canonical, secret-free, replay-stable

## StageProofTombstone

- request ID/digest, proof digest, terminal class `proof_expired`
- retained within the fixed 4096-entry authority store; never reconstructs proof
- never deleted or recycled; saturation refuses new stage acceptance as `retention_full`
- full proof retention: at most 64 total proofs per target, including leased/pinned proofs
- full target authority, including leases/pins, is at most 16 MiB serialized; acceptance
  reserves one terminal identity before creating an owner/effect
- exact admission predicate: existing replay reads retained authority; a new unique request
  refuses when `tombstone_count == 4096`; otherwise atomic compaction may create tombstones
  only while the post-reservation state remains at most 64 total proofs, fewer than 4096
  tombstones, 64 live leases/pins, and 16 MiB

## StageProofActivationLease

- distinct from broker credential and target effect leases
- fixed maximum 64 live prepared/accepted records per target within the 16 MiB total
- lease ID, exact durable activation-owner/request holder identity, finite admission
  deadline, and phase `prepared|accepted`
- exact activation request ID/digest, stage request ID/digest, proof digest, target,
  stage generation, and ledger revision
- accepted phase binds the exact durable host acceptance receipt/generation
- preparing immediately pins the full proof; compaction cannot select it
- exact replay returns the same record; changed binding conflicts
- expiry never auto-unpins; before acceptance it forbids new host acceptance and exact-holder
  cancellation requires under-lock proof that acceptance is absent; an already committed
  exact acceptance promotes after the deadline
- process identity and unrelated recovery identity never become a holder
- release requires the exact durable terminal activation owner/receipt

## State Transitions

`accepted -> credential_pending -> helper_running -> pulling -> cleanup_pending -> observing -> succeeded`

Any phase may end `refused`, `failed`, `cancelled`, or `uncertain`. Only exact
pre-effect ownership with termination/cleanup proof may resume. Terminal identities
replay immutably; changed reuse refuses.

Activation proof custody transitions:

`unleased -> prepared_and_pinned -> accepted_and_pinned -> terminal_released`

Lock order is target-wide mutation, shared host-state transaction, stage-ledger target;
release is reverse. A crash or deadline leaves `prepared_and_pinned` durable. Replay promotes
it when exact host acceptance exists or cancels only after proving that acceptance absent.
