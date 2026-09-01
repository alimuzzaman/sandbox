# Data Model: Immutable Activation and Recovery

## ActivationPolicy

- authority identity/digest/revision
- exact target/machine/daemon and accepted 049/050 schema revisions
- selected service topology and ordered init declarations
- runtime/Compose/edge capability revisions
- shared mutation-owner/state revisions

## ActivationAuthorityBinding

- machine authority/digest/revision
- exact plan/proof/staging-policy/stage-request/staging-generation identities
- authenticated Feature 050 stage-ledger authority/revision
- exact target/machine/daemon and unchanged `DeliveryIdentityProjection`
- accepted runtime/edge/shared-owner/state revisions

## StageProofCustodyBinding

- Feature 050 prepared lease/accepted pin identity; distinct from broker/effect leases
- durable activation-owner/request holder identity, activation request/digest, stage
  request/digest, proof digest, target, stage generation/revision, finite admission
  deadline, and host acceptance receipt when accepted
- expiry forbids new host acceptance; same-holder replay promotes an already committed exact
  acceptance after expiry or cancels only proven absence; no process/recovery identity adopts
- preparing pins before proof validation; expiry never auto-unpins and forbids new acceptance
- same-holder crash replay promotes already committed host acceptance after expiry or cancels
  only proven absence
- only the exact accepted activation owner releases after terminal authority is durable

## ActivationRequest

- schema version, request ID/digest, operation (`activate|adopt|rollback`)
- expected starting generation, policy digest, confirmation
- exact plan/proof digests; rollback grant digest when applicable

## ActivationTransaction

- request/policy/target/plan/proof identities and starting generation
- operation, shared owner, phase, effect-entry boundary
- init receipts, running observation, edge sub-request/result
- terminal result/uncertainty or non-reusable tombstone
- no credential/reference/token, secret value, raw environment/output, or private path

## InitReceipt

- declaration/configuration digest; target/runtime epoch; local image identity
- create/inspection identity and effect-entered marker
- bounded exit/termination/cleanup result
- terminal only when exact inspection preceded start and ownership is complete

## RunningObservation

- unchanged target/machine/daemon/runtime epoch
- each selected container/service/topology identity
- exact declared ref, local ID, repository/config digest, platform, health
- edge-relevant endpoint identity and canonical observation digest

## VerifiedActivationGeneration

- generation number; plan/proof/policy/request/transaction digests
- target/daemon, exact image, topology, non-secret configuration identity
- init receipt identities, running observation, edge terminal receipt
- committed time/identity and canonical digest
- forward `rollback_subject_digest` and `rollback_grant_digest`

## RollbackCompatibilityGrant

- machine authority/revision and issuance before forward acceptance
- deterministic `ForwardRollbackSubject`: current rollback-target generation plus
  forward candidate plan/proof/activation-authority/config/topology/init-data identities
- expiry/revocation/policy revision and canonical digest

It never contains the future terminal generation digest. Forward acceptance stores both
subject/grant digests; the resulting terminal generation references them.

## RecoveryObservation

- active transaction/request/generation identity
- coherent exact-new, exact-prior, neither, or ambiguous classification
- bounded exact evidence identity plus target/runtime start/end epoch
- Feature 048 returns this value twice; it remains non-authorizing

## ActivationRecoveryProvisional

- 051-owned recovery request/digest, active transaction, starting generation, owner
- exact first-observation identity and target/runtime epoch boundaries
- explicit `authorizing: false`; bounded and durable
- no success, receipt, promotion, generation advance, terminal attempt, or effect authority
- only the exact request/digest may resume post-write observation

## ActivationRecoveryRequest

- distinct `sb host image recover` action, request ID/digest, activation transaction,
  expected generation, confirmation
- immutable terminal result or conflict/tombstone
- Feature 048 observer returns values only; 051 stores provisional, re-observes, then
  atomically stores result plus promotion when evidence identities match

## Host Activation State

- optional closed 051 record inside existing per-target hosting state
- current generation; nullable previous generation; nullable active transaction
- bounded immutable terminal results/tombstones, nullable recovery provisional, and latest
  recovery observation
- one shared host generation CAS; legacy/unknown fields preserved
- existing shared `RecoveryRepository` alone parses/locks/replaces/fsyncs outer state;
  activation repository validates/serializes only this nested value and candidate transitions

## State Transitions

Activation/rollback share:

`accepted -> preflight -> init_pending* -> runtime_pending -> runtime_proven -> edge_pending -> committed`

Adoption uses:

`accepted -> preflight -> running_proven -> committed`

Any phase may terminate `refused`, `failed`, `cancelled`, or `uncertain`. Only exact
pre-effect ownership may resume. Possible init/runtime/edge effect without authoritative
terminal evidence remains fenced. Activation recovery uses:

`accepted -> pre_observing -> recovery_provisional -> post_observing -> matrix_result`

Feature 048 returns both observation values without writing. Only exact pre/post evidence
and unchanged epoch/generation/transaction enter the closed recovery matrix. For both
activation and rollback: `neither`/`ambiguous` never promote; `exact_prior` never advances
generation and only closes proven pre-effect work as no-effect non-success; `exact_new`
promotes only from `runtime_proven` or `edge_pending` when every required terminal receipt
is already authoritative. Adoption is ineligible. All other cells record non-success or
uncertainty without promotion.
Changed/unavailable post-evidence atomically records non-success, clears provisional, and
leaves the activation transaction fenced; persistence uncertainty retains provisional.
