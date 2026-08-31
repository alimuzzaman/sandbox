# Data Model: Observation-Only Hosting Recovery

## Target Record

One explicit `remote/project/environment` key.

- `generation`: non-negative integer; legacy absence is generation 0 but non-authorizing.
- `active_operation`: nullable bounded owner identity, action, explicit phase, and
  `effect_entered` marker. Only an exact `observation_pending`/false owner can resume.
- `recovery_provisional`: nullable bounded non-authorizing pre/post evidence fence. It exposes
  no receipt, attempt success, generation advance, or edge authority.
- existing hosting receipt fields remain compatible.
- `attempts`: newest 64 immutable full attempts.
- `tombstones`: request identity to permanent compact outcome; bounded entry size.

## Hosting Operation Identity

- durable job ID and original request ID
- canonical command/action identity
- explicit target key and stable host identity
- clean source commit and source-state identity
- allowed branch
- non-secret configuration digest
- secret binding key version and per-reference opaque digests
- persistent and one-shot service sets
- expected starting generation
- immutable canonical digest over all fields

Created before the first hosting effect. Missing fields mean legacy/ineligible.

## Recovery Attempt

- schema version, recovery request ID, canonical request digest
- action: `observe_reconcile` or `continue_edge`
- original operation digest/job/request
- expected and resulting generation
- referenced observation/evidence identity for edge action
- effect scope: `receipt_only` or `edge_only`
- bounded phase list and evidence summary/digest
- terminal family/class and safe timestamps

Transitions:

`accepted -> observing -> reconciliation_provisional -> post_observing -> reconciled|refused|failed`

`accepted -> revalidating_edge -> edge_effect_unknown|edge_completed|refused|failed`

Terminal attempts never transition. Exact replay returns the terminal record. Observation
success may use the `already_reconciled` alias; edge success retains `edge_only_completed`.

## Observation Epoch

- start/end stable host identity and runtime marker
- source/config/environment-file/compose-file digests
- exact image ID per declared persistent and one-shot service
- configured, running, missing, duplicate service sets
- persistent service state, health, source revision checks
- one-shot completed phase receipts
- bounded raw phase classifications only; no raw config or values
- completeness/bounds and canonical evidence digest

The epoch authorizes reconciliation only when start/end markers match and every required
field is exact and complete. After the non-authorizing provisional write, an immediate exact
post-write observation must match the first evidence identity before a separate atomic commit
can publish success.

## Tombstone

- request ID and canonical request digest
- action and effect scope
- terminal family/class
- resulting generation when known
- effect uncertainty flag

Tombstones are permanent for the authority lifetime and contain no observation payload.

## Generation Rules

1. Apply acceptance reads current generation under target lock and records it.
2. Apply phase persistence may update its own immutable operation record but never rewrites a
   terminal attempt.
3. Successful recovery reconciliation atomically commits attempt+receipt and increments once.
4. Exact replay returns the stored resulting generation without increment.
5. Edge continuation references the observation's resulting generation and increments only
   when its terminal receipt is atomically known; uncertain effect retains the fence and blocks
   automatic replay.
