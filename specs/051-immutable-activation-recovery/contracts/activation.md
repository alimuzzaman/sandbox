# Contract: Activation, Adoption, and Rollback

## Input

One closed request contains operation, request identity/digest, expected generation,
confirmation, exact activation-policy digest, exact `VerifiedImagePlan`, exact matching
`StagedImageProof`, and rollback-grant digest only for rollback. Unknown fields refuse.

Artifacts never self-authorize. Machine-owned `ActivationAuthorityBinding` pins exact
plan/proof/stage-request/staging-policy/staging-generation/stage-ledger identities and
the unchanged delivery projection. Before proof validation, an authenticated Feature 050
operation must durably prepare a proof-custody lease whose holder is the durable activation-
owner/request identity and which binds exact request/proof/target/stage identities plus a
finite canonical UTC whole-second admission deadline ending in `Z`; preparation immediately pins that retained
byte-identical terminal proof. Feature 051 holds the stage lock/pin through durable host-
state acceptance, then promotes it to an accepted pin bound to an exact
`host-acceptance/<64 lowercase hex>` receipt. Missing,
compacted, tombstoned, mismatched, capacity-exhausted, or expired-before-acceptance custody
refuses.
No init, runtime, or edge effect may start until accepted-pin promotion is durable.

Lock order is target-wide mutation, shared host-state transaction, then stage-ledger target;
release is reverse. Crash/deadline never auto-unpins. Expiry forbids new host acceptance.
Exact replay by the same durable holder promotes an already committed atomic host acceptance
even after the deadline, or cancels an expired preparation only after proving acceptance
absent. Process identity and unrelated recovery identity have no holder rights. Only the
exact accepted activation owner may release after its terminal authority is durable. This custody lease is not a broker
credential lease or target effect lease.

No credential, credential reference, token, auth form, registry command, tag, index,
alternate image, trust override, helper, pull, build, or prune field exists.

## Activate

Validate all identity/local/topology facts before effects. Execute ordered inspected init,
replace selected services from the exact local digest with pull/build disabled, prove
running identity/health, complete the immutable edge sub-request, and atomically commit.

## Adopt

Require zero declared init steps and exact fresh local/running/health/edge proof. Perform
no init, service, edge, pull, or build effect. External/legacy receipts never authorize.

## Rollback

Require the single previous generation, its exact local image, and current machine grant
over the pre-forward deterministic `ForwardRollbackSubject`. Forward acceptance and the
terminal generation must reference the exact subject/grant digests. Before effects,
re-observe the previous config/local image/platform plus unchanged target/daemon epochs,
and re-render the supplied Compose files against the retained exact previous projection.
Use the same runtime/edge/proof/commit state machine as activation. A rollback generation
retains that exact Compose projection but records the fresh post-replacement runtime-owned
container identities. Never pull or select an older/fallback generation.

## Result

Closed bounded results expose operation, request identity, starting/result generation,
stable class, transaction/generation/observation digests, and safe phase summary.
Exact terminal replay is immutable; uncertain results never claim success.
