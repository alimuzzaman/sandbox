# State and Transaction Contract

## Repository Boundary

For one immutable instance incarnation:

```text
$SANDBOX_HOME/runtime/server-config/<incarnation>/
├── .lock
├── state.json
├── transaction.json              # absent only when no retained transaction exists
├── fragments/
│   └── <content-id>.fragment
└── generations/
    └── <generation-id>/
        ├── manifest.json
        └── adapter-owned rendered files
```

Private locators never appear in normal output. The incarnation root and directories are
owner-only and cannot be symlinks. State/journal/content/manifest files are regular,
owner-owned, mode 0600, opened relative to verified directory descriptors with
`O_NOFOLLOW`. Rendered files mounted read-only into a container use the narrowest mode
compatible with the adapter; no path is group/world writable.

Writes use same-directory temporary files, `fsync` file and parent, atomic rename, and a
post-write identity check. Generation directories are immutable after publication. A
corrupt/foreign/unsafe repository is reported, never chmod-repaired or adopted.

## Canonical Identities

- `content_id = sha256(exact accepted bytes)`.
- `fragment_set_id = sha256(canonical schema, incarnation, server, authority and policy
  revisions, ordered name/content IDs)`.
- `generation_id = sha256(fragment_set_id, renderer revision, canonical manifest, exact
  rendered file names/modes/digests)`.
- `runtime_precondition_digest = sha256(incarnation, server, runtime ID, exact image ID,
  mount ID, observed generation ID)`.

Canonical JSON is UTF-8, sorted-key, compact, integer/string/boolean/null only. Unknown
schema/revision/field shapes fail closed.

## Read-Only Observation

`list`, metadata `show`, and lifecycle gates open existing state without creating the
root, lock, files, or timestamps. They compare:

1. authoritative instance incarnation/server/mount projection;
2. repository state and transaction integrity;
3. rendered generation manifest/digests;
4. adapter runtime/image/mount/effective-generation observation;
5. readiness.

They return `healthy`, `stopped`, `degraded`, `recovery_needed`, `unsupported`, or
`absent`. They never repair or choose an active set. Exact-content show reads only the
content ID named by a healthy committed set; degraded/recovery state cannot disclose an
unproven candidate as active content.

## Mutation Lock

- One nonblocking/bounded `flock` per incarnation serializes apply, revert, recovery,
  lifecycle reconciliation, and confirmed deletion of fragment state.
- Lock ordering is: existing project/instance lifecycle lock, then fragment lock, then
  adapter/runtime operation. Never acquire a project/global lock while holding the
  fragment lock.
- An overlapping mutation may wait only inside the whole-operation deadline. After lock
  acquisition it re-reads committed state. Otherwise it returns `operation_conflict`
  without writes.
- Read-only inspection takes no exclusive lock and tolerates an atomic old/new file view;
  a visible transaction is reported, not repaired.

## Apply Algorithm

1. Resolve and validate exact instance projection; require ready supported runtime and
   attached incarnation mount.
2. Read stable bounded bytes; normalize/validate name; compute content ID.
3. Acquire the incarnation lock and re-resolve instance/runtime facts.
4. Load/verify repository and transaction. Reconcile an interrupted transaction only by
   the Recovery Algorithm below.
5. If the healthy active name has identical content, return `no_op` before generation,
   validation boot, activation, or reload.
6. Apply common and adapter authority to content and complete candidate set.
7. Materialize immutable candidate generation; retain exact prior known-good generation.
8. Observe runtime/image/mount, validate candidate with exact image, and record content-free
   validation evidence.
9. Re-observe and require identical preconditions.
10. Atomically publish `transaction.json` at `validated`, then enter `activating`.
11. Point only the instance adapter at candidate; reload/restart target web tier; observe
    readiness/effective generation within phase bounds. Journal every phase durably.
12. On proof, atomically replace `state.json`, mark transaction `active`, and prune only
    unreferenced superseded material.
13. On possible live-change failure, run the Rollback Algorithm.

No state receipt names the candidate as active before step 12.

## Revert Algorithm

Revert follows Apply with the named fragment removed from the complete set. A missing name
is `no_op` only after healthy committed state, exact runtime agreement, and no transaction
are proven. Empty candidate set activates the adapter's Sandbox baseline and leaves no
plugin fragment active.

## Rollback Algorithm

1. Preserve the original failure code/phase. Set `rollback_attempted=true` before any
   recovery runtime action.
2. Restore the exact `prior_generation_id` named by the durable transaction. Never choose
   by newest timestamp, directory order, or current pointer.
3. Perform at most one target-only recovery reload/restart.
4. Re-observe exact prior generation, incarnation, server, image/mount preconditions, and
   readiness within the 60-second rollback bound.
5. If proven, retain/restore the prior `state.json`, mark terminal `rolled_back`, return
   nonzero, and report both failure and recovery codes.
6. If any restoration, activation, observation, or durability step is unproven, mark
   terminal `recovery_needed`; preserve prior/candidate identities and content-free phase
   evidence; block later candidate mutation.

No rollback retries the failed candidate. No second recovery activation is allowed.

## Interruption Recovery Algorithm

On a later mutation only:

- `prepared`/`validated` with runtime still exactly on prior generation: discard only the
  unactivated transaction reference and continue after durable terminalization.
- `activating` or later with runtime exactly on the candidate or unknown: run the one
  journal-bound rollback if not already attempted.
- Runtime exactly on prior after a recorded recovery activation: prove readiness and
  terminalize `rolled_back` without another activation.
- Runtime exactly on a committed candidate while the receipt write was interrupted:
  commit only if the journal already contains complete validation, activation, and
  readiness evidence for the same runtime preconditions; otherwise restore prior.
- Any corrupt journal, missing referenced generation, wrong incarnation/server/image/
  mount, or ambiguous effective generation becomes `recovery_needed`.

Read-only commands report these conditions and never execute this algorithm.

## Lifecycle Gates

### Start, ensure, apply/reconcile, relocation

- A non-empty known-good set is usable only when the authoritative incarnation/server,
  repository, attached mount, exact generation, and current image are coherent.
- Start/reconcile activates only the exact committed generation; it does not translate,
  drop, or select a candidate.
- Readiness is reported healthy only after the effective generation is observed.
- Missing/legacy mount identity refuses fragment mutation and guides a supported
  instance `sb apply` reconciliation.

### Server switch

Before writing server selection or Compose:

- empty healthy set and no transaction: switch may proceed;
- any active fragment, nonterminal/retained transaction, degraded, stopped-unreconciled,
  or recovery-needed state: `server_switch_blocked`, no writes.

Fragments are never translated or retained for later silent activation.

### Stop/start

Stop preserves repository and incarnation. Start must reconcile the committed generation
before fragment state can be reported healthy. A stopped instance reports `stopped`, not
ready, and cannot mutate fragments.

### Delete

Ordinary deletion refuses active/unhealthy fragment state. Explicit fragment-state
deletion binds confirmation to the exact incarnation and current set/transaction digest.
The lifecycle removes the target runtime/mount and disassociates the incarnation before
removing its repository. Partial cleanup remains tombstoned/unadoptable. Reusing a display
name mints a new incarnation and cannot read the old root.

## Retention

- Retain the committed generation and every generation referenced by a transaction.
- After terminal `active` or proven `rolled_back`, prune only unreferenced generations
  while holding the lock.
- `recovery_needed` retains both prior and candidate material.
- Fragment content has no independent global deduplication; cross-instance byte equality
  never permits cross-instance locators or adoption.
- No automatic age-based deletion participates in correctness.

## Failure Semantics

| Failure point | Live mutation possible | Terminal handling |
|---|---:|---|
| Input/common policy | no | `refused`, no state write/reload |
| Adapter policy/native validation | no | `refused`, candidate uncommitted, no reload |
| Runtime precondition recheck | no | `refused` or `degraded`, no activation |
| Candidate pointer/activation/reload/readiness | yes | one exact rollback attempt |
| Rollback unproven | yes/unknown | `recovery_needed`, later mutation blocked |
| Read-only corruption/drift | no write by command | `degraded`/`recovery_needed` observation |
| Writer contention | no | `conflict` |

All phase errors are bounded codes. Raw fragment/native/container output stays private and
is never persisted in transaction evidence.
