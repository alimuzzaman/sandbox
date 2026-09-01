# Contract: Stage Request and Helper Protocol

## Public request

Requires explicit project, environment, registered remote, request ID, expected stage
generation, complete `VerifiedImagePlan`, and confirmation. Acceptance returns a durable
operation identity. Exact terminal replay returns the recorded result; conflict refuses.

## Helper frames

1. closed non-secret plan/request frame;
2. separately delivered bounded credential frame on the fixed private channel;
3. closed secret-free result frame.

The helper entry/argv/environment are fixed. No shell interpolation or caller command is
accepted. Before credential resolution it is launched as one uniquely named transient
systemd service on cgroup v2 with `KillMode=control-group`, no cgroup delegation, and no
escape capability. The ledger binds the exact unit/cgroup. Safe termination requires
the unit inactive and its exact cgroup removed or reporting `populated=0`.
Credential material exists only after durable acceptance and before mandatory cleanup.
Output is bounded and redacted before any outer layer can retain it.

## Stable result families

`success`, `refused`, `failed`, `cancelled`, `uncertain`; with bounded classes for policy,
plan, capability, broker, helper, pull, cleanup, observation, conflict, unknown effect,
proof-pin capacity, and `retention_full`.

Before a new stage owner exists, the repository reserves one terminal identity within the
64 total full-proof (including leased/pinned), 4096 tombstone, 64 live proof-lease/pin, and
16 MiB per-target bounds. Existing replay reads retained authority. A new unique request
always returns `retention_full` when `tombstone_count == 4096`. Otherwise the repository may
atomically compact unleased/unpinned proofs only when the post-reservation state stays within
all four limits. Refusal occurs before credential resolution, helper launch, or pull;
identities and leased/pinned proofs are never deleted.

## Feature 051 proof-custody handoff

This lease is not the broker credential lease or target effect lease. The caller acquires
locks only in this order: target-wide mutation lock, shared host-state transaction lock,
stage-ledger target lock. Under the stage lock, an authenticated request atomically creates
or replays a `prepared` lease whose holder is the durable activation-owner/request identity
and whose finite admission deadline immediately pins
the exact full proof. The stage lock remains held while Feature 051 verifies the proof and
durably accepts host state. The lease then atomically becomes `accepted`, binding the host
acceptance receipt.

If the controller crashes or the deadline passes, the prepared lease remains pinned. The
deadline forbids new host acceptance after expiry. Exact replay by the same durable holder
reacquires the same locks and reads atomic host state: an exact acceptance already committed
promotes the same pin even after the deadline; proven absence permits that same holder to
cancel the expired preparation. Ambiguous/malformed state remains pinned and fenced.
Process identity and unrelated recovery identity confer no holder rights. No different
holder/request/digest may adopt, cancel, promote, or release it. Only the exact accepted
activation owner may release after terminal authority is durable. Compaction never changes
lease/pin state.
