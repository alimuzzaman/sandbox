# Independent Planning Analysis: Owned Storage Authority

**Date**: 2026-09-02

**Verdict**: **NOT READY — PUBLIC PORT BLOCKED**

**Implementation authorization**: **NONE**

## What the repaired design resolved

The smaller conditional design resolves the three original semantic planning
blockers on paper:

1. the protected lifecycle is the sole owner of review, promotion,
   finalization, revocation, and capability truth; the storage authority owns
   only storage operations and a non-authorizing prepared binding;
2. fixture validation remains implemented-unproven until an ordinary
   `future + qualification:null` sync, CI, cleanup, replay, ancestry, rollback,
   and unrelated-state journey is derived by a protected finalizer; and
3. durable `ci run --request-id` is the parent replay identity, with exact
   derived per-cell materialization identities and changed-binding conflict.

The design also keeps Features 048–051 and `sandbox/hosting/**` immutable and
keeps Feature 051 live, edge, deployment, and production gates separate.

## Blocking implementation fact

Fresh independent post-edit analysis inspected the actual Feature 051 public
interfaces and found that the lifecycle persistence design is not executable:

- `RecoveryRepository.target_mutation_port(capability)` validates against the
  fixed `TARGET_MUTATION_CAPABILITIES` registry in `sandbox/core/_hosting.py`.
  That registry has no owned-storage or lifecycle capability, and unknown
  capability names fail closed.
- `RecoveryRepository.activation_host_state_port()` returns the activation-only
  public port. Its public methods read or commit only `image_activation`.
- Activation commits pass through the closed Feature 051 activation encoder,
  which rejects unknown keys. It cannot carry the proposed Feature 052 nested
  lifecycle value.

Therefore the retained FR-058 design cannot both persist lifecycle truth and
obey the immutable boundary. It would require at least one forbidden action:

- add a new target-mutation capability or generic nested-state CAS port;
- extend the accepted Feature 051 host or activation schema;
- import private `_record` or `_write` helpers; or
- reinterpret an existing `sync` or `activate` capability as lifecycle
  authority.

The first two change accepted infrastructure. The last two violate the public
contract and fail-closed authority boundary. None is authorized.

## Independent review record

- The initial independent review reopened planning on the three semantic
  blockers listed above.
- A later independent design review passed the repaired semantic model.
- The first post-task analysis reopened the artifacts because tasks placed new
  lifecycle code below immutable `sandbox/hosting/**`, lacked complete RED and
  documentation gates, and omitted executable SC-014 proof.
- Those task-level issues were repaired and lifecycle source was moved in the
  design to `sandbox/owned_storage_lifecycle/**`.
- A fresh independent analysis then inspected the real public ports and
  returned **REOPEN** on the blocking interface contradiction above. It also
  found broader RED-before-source ordering gaps in the draft task graph.

The draft `tasks.md` was removed after that verdict. Tasks are not ready and
must not be regenerated from this checkpoint.

## Smallest decision needed to resume

A human must choose one of these mutually exclusive changes before planning can
become ready:

1. explicitly authorize a separate, bounded extension that gives an external
   Feature 052 lifecycle module a truthful public target-mutation capability
   and a closed generic nested-value read/CAS/commit port while preserving
   Feature 051 behavior; or
2. amend FR-058 and the lifecycle ownership model to select another durable
   owner, then repeat specification, planning, task generation, and independent
   analysis.

Neither choice is inferred here. Any approved extension must be designed and
reviewed separately before Feature 052 tasks are generated. It must not rewrite
Features 048–051, claim Feature 051 T060 or live gates, or use private
repository helpers.

## Preserved pointers and authorization gates

The active pointers intentionally remain on
`specs/051-immutable-activation-recovery` as required. Feature 052 planning used
an explicit feature-directory override; no implementation command should run
for Feature 052 while this verdict is open.

Still requires separate explicit authorization:

- any public hosting transaction-port or capability extension;
- Feature 052 implementation;
- service installation, update, or privilege changes;
- disposable live qualification, mutation, or cleanup;
- protected review, validation promotion, finalization, or revocation;
- remote update, release, rollout, deployment, or production adoption;
- Feature 051 T060 and its registered-host, edge, deployment, and production
  proof.
