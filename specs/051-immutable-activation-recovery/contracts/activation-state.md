# Contract: Activation State and Shared Ownership

The existing owner-only hosting state remains authoritative. The existing shared
`RecoveryRepository` is the sole outer `hosts.json` parser, writer, transaction locker,
atomic replacer, and fsync owner. Feature 051 adds one optional closed nested record per
target through a narrow shared transaction port and preserves unknown/legacy fields byte-
semantically on no-op reads/writes. The activation repository only validates/serializes its
nested value and proposes candidate transitions; it never writes or locks outer state and
does not create a competing state file.

All target mutations acquire the per-target owner and shared transaction lock, reload state,
compare the expected generation, validate the complete candidate, atomically replace/fsync
state, and release only after terminal ownership is durable. A Feature 050 proof handoff then
acquires the stage-ledger target lock, giving the exact cross-store order: target owner,
host-state transaction, stage ledger; release is reverse. Unknown mutation types refuse.

Activation and rollback are operation values in one transaction schema. Each effect has
a durable pre-effect/effect-entered/terminal boundary. Time, PID, process absence, or lock
expiry alone never authorizes takeover. Changed request reuse refuses. Exact terminal
replay returns its recorded result. Bounded tombstones keep compacted IDs non-reusable.

The record retains exactly current and nullable previous verified generations. A commit
moves current to previous and discards the older previous. No secret value, credential,
credential reference, arbitrary output, environment body, or temporary path is serializable.

Before proof validation, Feature 050 durably prepares a holder/deadline-bound proof lease;
it immediately pins the full proof. The stage lock/pin stays held across forward acceptance.
Forward acceptance atomically stores the activation-authority digest, accepted-proof-pin
binding, deterministic rollback-subject digest, and grant digest. The prepared lease then
promotes to an accepted pin. Crash/expiry keeps it pinned until exact replay proves acceptance
present (promote) or absent (cancel). Only the exact terminal activation owner can release.
The terminal forward generation references the same subject/grant; no future-result digest
is required before it exists.

Edge is a transaction sub-request. Proven-not-entered may exact-resume. Acceptance-unknown
first queries existing replay authority. Exact terminal receipt may promote only after
fresh unchanged runtime observation. Possible delivery without receipt remains fenced.

Activation recovery is a separate request type dispatched by `sb host image recover`.
Under the same owner/CAS, 051 validates the first read-only Feature 048 observer value and
durably writes only its bounded `authorizing: false` recovery provisional with the exact
evidence identity/epoch. It immediately re-observes. Only byte-exact pre/post evidence plus
unchanged epoch/generation/transaction permit the recovery contract's exhaustive operation/
phase/class matrix to produce a candidate containing both immutable recovery result and only
the matrix-legal transaction promotion; one atomic outer replacement by `RecoveryRepository` commits
both or neither and clears the provisional. Exact crash replay resumes only post-observation
from the matching provisional. Existing failed-apply recovery state and request/result
identities are not read as 051 authority or rewritten.
