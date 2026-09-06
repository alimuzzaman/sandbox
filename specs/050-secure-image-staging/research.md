# Research: Secure Private Image Staging

## Decision 1 — Staging policy pins one plan digest

- **Decision**: Machine staging policy authorizes the exact Feature 049 `plan_digest`.
- **Rationale**: Freshness is authority, not trust reinterpretation.
- **Alternatives considered**: Any valid plan; re-run trust. Rejected.

## Decision 2 — Fixed broker recipient and measured helper

- **Decision**: Bind GHCR repository-read recipient, auth form, helper artifact digest,
  fixed entry, installed runtime revision, target, and capability before credential resolution.
- **Rationale**: No caller-controlled credential destination or executable.
- **Alternatives considered**: `docker login` from caller shell; dynamic helper. Rejected.

## Decision 3 — Operation-bound handling, not PAT properties

- **Decision**: Sandbox makes one broker resolution available to one accepted operation
  and makes no upstream expiry/one-use claim.
- **Rationale**: GitHub controls PAT lifetime/scope.
- **Alternatives considered**: Claim short-lived PAT. Rejected as unenforceable.

## Decision 4 — Temporary credential state must be volatile

- **Decision**: Helper derives its workspace below the effective service user's
  `/run/user/<uid>` tmpfs, verifies owner-only objects without following links, and
  proves cleanup before success.
- **Rationale**: Docker authentication normally needs a client credential context.
- **Alternatives considered**: Persistent Docker config; environment/argv. Rejected.

## Decision 5 — Kernel-enforced descendant scope

- **Decision**: Require a uniquely named transient systemd user service on cgroup v2 with
  `KillMode=control-group`, no delegation/escape, and ledger-bound unit/cgroup identity.
  Complete termination is exact unit inactive plus `populated=0` or cgroup removal.
- **Rationale**: Kernel cgroup membership is mechanically authoritative; PID/process-
  group observation cannot cover double-fork/session escape.
- **Alternatives considered**: Process group/session scans; timeout then retry. Rejected.

## Decision 6 — One coherent daemon observation

- **Decision**: Bind start/end daemon/host epochs and collect RepoDigest, config,
  platform, and image ID inside one bounded observation.
- **Rationale**: Mixed observations can falsely combine different images/daemons.
- **Alternatives considered**: Independent inspect calls without epoch. Rejected.

## Decision 7 — Ledger and proof have distinct canonical digests

- **Decision**: Ledger owns replay/lifecycle; `StagedImageProof` owns downstream evidence.
- **Rationale**: Feature 051 needs stable proof without ledger internals.
- **Alternatives considered**: Return ledger row directly. Rejected.

## Decision 8 — Bounded proof retention expires authority

- **Decision**: Retain at most 64 total complete proofs per target including pinned proofs,
  never evict a proof pinned
  by a prepared/accepted activation handoff, and compact older unpinned proofs to at most
  4096 permanent tombstones. Replay of a compacted proof returns `proof_expired`
  non-success. When `tombstone_count == 4096`, refuse every new unique stage request as
  `retention_full` before ownership/effects regardless of spare proof or byte capacity;
  never delete a tombstone or reuse its request identity.
- **Rationale**: Byte-identical replay cannot be promised after deleting proof bytes.
- **Alternatives considered**: Reconstruct from tombstone; delete old identities; unbounded
  retention. Rejected.

## Decision 9 — Visibility is observed during staging

- **Decision**: Require anonymous denial for the exact manifest followed by the fixed
  authenticated exact pull, in one bounded registry observation.
- **Rationale**: Feature 049 pure policy can declare intended-private but cannot observe it.

## Decision 10 — Crash-safe activation proof custody

- **Decision**: Feature 051 acquires target mutation, host-state transaction, then stage-
  ledger target locks. Feature 050 durably prepares a proof-custody lease that pins the full
  proof before 051 validates it; the stage lock stays held through atomic host acceptance,
  then the lease is promoted to an accepted pin. The lease holder is the durable activation-
  owner/request identity and has a finite admission deadline; expiry never auto-unpins and
  forbids new acceptance. Crash replay by that same holder promotes an already committed
  exact acceptance even after the deadline or cancels only after proving absence. Process
  identity and unrelated recovery identity have no custody authority. Only the exact
  accepted activation owner may release the pin.
- **Rationale**: A read-only lookup followed by later host acceptance has a compaction
  TOCTOU. A durable prepared lease bridges the two atomic repositories without pretending
  they share one filesystem transaction.
- **Alternatives considered**: Lookup then pin; temporary in-memory pin; expiry-based
  automatic unpin; compactor-owned release. Rejected.
