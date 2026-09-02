# Research: Observation-Only Hosting Recovery

## R1 - Durable job binding before effects

**Decision**: Add fixed authoritative job ID, request ID, and source identity fields to the
durable descriptor. The single-purpose supervisor process injects those fixed values into the
child context before launch and restores its own context immediately. `host apply` refuses
recovery eligibility when that context is absent and records the binding before its first
remote or edge effect.

**Rationale**: The child cannot know its generated job ID when argv is submitted. A generic
snapshot alone cannot bind later hosting phases. Fixed supervisor context is smaller than a
hosting-specific job scheduler and does not enumerate, copy, log, or persist inherited env.

**Alternatives considered**: parse hosting argv in JobService (cross-module coupling);
two-stage deferred jobs (new public job lifecycle); let recovery bind legacy jobs (unsafe).

## R2 - Single-flight and atomic CAS

**Decision**: Use a target-hash lock file with bounded `flock`, then reload and atomically
replace the existing managed-host state while holding the lock. Every operation records
expected/resulting generation and immutable request digest.

**Rationale**: The state is already owner-only JSON with atomic replacement. A target lock
avoids a second database and permits unrelated targets to proceed. Reload-under-lock prevents
stale caller state.

**Alternatives considered**: global lock (unnecessary cross-target blocking); SQLite plus
JSON projection (cannot atomically commit two authorities); remote lock (recovery must refuse
before remote mutation and controller state is authoritative).

## R3 - Exact secret configuration without disclosure

**Decision**: Bind secret reference names and HMAC-SHA256 digests of resolved values with a
machine-local 256-bit owner-only key. Persist only key version, reference names, and digests.
Key absence/change makes old evidence non-authorizing.

**Rationale**: The current secret source has no version metadata. Plain hashes permit offline
guessing. HMAC gives exact comparison without persisted values or disclosure.

**Alternatives considered**: file mtime/inode (misses environment and can false-match);
plain hash (weak disclosure boundary); skip secret identity (changed config could reconcile).

## R4 - One coherent read-only epoch

**Decision**: Extend the existing single bounded observer to return remote config-file
digests, exact Compose image IDs for persistent and one-shot services, configured/running
topology, source revision checks, and start/end runtime markers. Classify any changed marker,
partial phase, duplicate, bound overflow, or mismatch as non-authorizing.

**Rationale**: Multiple unrelated SSH calls permit torn evidence. One deadline and one remote
program minimize that window and expose uncertainty honestly.

**Alternatives considered**: reuse diagnose calls (separate epochs and mutable image names);
health only (insufficient identity); restart containers to prove state (forbidden mutation).

## R5 - Attempt retention and tombstones

**Decision**: Retain at most 64 bounded full attempts per target. Compaction keeps a small
permanent tombstone keyed by request ID/digest with action, effect scope, and terminal class.

**Rationale**: Evidence stays bounded while an expired or uncertain edge identity can never
become new authority.

**Alternatives considered**: delete old identities (unsafe replay); unbounded receipts
(storage abuse); time-limited tombstones (authority can reappear after expiry).

## R6 - Edge-only continuation

**Decision**: Recovery service accepts a separate confirmed edge request only after a
successful observation attempt and resulting generation. It re-observes under the same lock,
then delegates to the existing Caddy/DNS/certificate helpers without calling source, Compose,
build, initializer, migration, or secret-write paths.

**Rationale**: Reusing `_apply_host` would enter source staging before decision. A narrow
adapter gives enforceable effect reachability and preserves existing edge behavior.

**Alternatives considered**: call ordinary apply (known mutation gap); make observation
repair edge automatically (violates confirmation); implement new edge authority (scope gain).

## R7 - Host identity and governance

**Decision**: Require the Feature 046 immutable host status projection for stable target
identity and remote runtime parity. Feature 047 governance is optional until implemented;
when an authorizing governance projection is configured or required, missing/adverse evidence
blocks edge authority but never blocks read-only observation/refusal.

**Rationale**: Remote name/SSH address can be repointed. Feature 046 already owns the narrow
read-only host identity. Feature 048 must not import Feature 047 mutation or priority policy.

**Alternatives considered**: hash remote config (not host identity); run raw machine-id SSH
(new sensitive host probe); make unfinished Feature 047 a hard implementation dependency.
