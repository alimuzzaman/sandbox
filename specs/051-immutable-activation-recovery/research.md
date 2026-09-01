# Research: Immutable Activation and Recovery

## Decision 1 — One target-wide owner

- **Decision**: All registered target mutations share the existing per-target lock and
  hosting-state generation CAS; unknown mutation capabilities fail closed.
- **Rationale**: A feature-local lock cannot prevent legacy apply/sync/edge interleaving.
- **Alternatives considered**: Activation-only lock. Rejected.

## Decision 2 — Closed artifacts are inputs, not authority services

- **Decision**: Validate canonical schemas/digests and exact equality of Feature 049/050
  value objects. Do not import their trust, broker, helper, credential, or pull services.
- **Rationale**: Activation cannot widen or re-decide earlier authority.
- **Alternatives considered**: Re-run verification/staging. Rejected.

Caller artifacts remain claims. A machine-owned `ActivationAuthorityBinding` pins their
exact plan/proof/stage-ledger identities. Before proof verification, Feature 050 must
durably prepare a proof-custody lease that pins the retained proof across Feature 051 host-
state acceptance, then promote it to an accepted pin. Lock order is target mutation, host-
state transaction, stage-ledger target. The holder is the durable activation-owner/request
identity. Expiry never auto-unpins or permits new acceptance; replay by the same holder
promotes an already committed acceptance after the deadline or cancels only proven absence.
Process and unrelated recovery identities have no custody authority.

## Decision 3 — Init is inspected before effect entry

- **Decision**: Create without start, inspect exact configuration, persist effect entry,
  then start/wait/terminate and persist a bounded receipt.
- **Rationale**: Possible non-idempotent execution cannot be safely guessed or replayed.
- **Alternatives considered**: Compose run then inspect; retry on missing result. Rejected.

## Decision 4 — Edge is one immutable transaction sub-request

- **Decision**: Use exact replay authority inside the 051 transaction. Proven-not-entered
  may resume; exact receipt may promote after fresh runtime proof; uncertainty fences.
- **Rationale**: Runtime exactness alone is not delivery success, and Feature 048 cannot
  perform edge effects.
- **Alternatives considered**: Separate edge transaction; automatic retry. Rejected.

## Decision 5 — Adoption is zero-init only

- **Decision**: V1 adoption refuses any plan declaring init.
- **Rationale**: External, legacy, health, or caller evidence cannot prove init effects.
- **Alternatives considered**: External init attestations. Rejected for v1.

## Decision 6 — Rollback requires pre-forward authority

- **Decision**: Before forward acceptance, machine owner grants one deterministic
  `ForwardRollbackSubject`: current rollback-target generation plus forward candidate
  plan/proof/activation-authority/config/topology/init-data/policy identities. Acceptance
  persists grant/subject digests and the terminal forward generation references them.
- **Rationale**: Post-hoc caller claims cannot make a data/schema reversal safe.
- **Alternatives considered**: Bind the future terminal generation digest; operator
  confirmation only; automatic rollback. Rejected.

## Decision 7 — Feature 048 remains observation-only

- **Decision**: Add a new read-only activation-observer API through Feature 048 models/
  policy/service. A distinct replay-safe `sb host image recover` is owned by 051. Under
  one owner/CAS, 051 records a bounded `authorizing: false` provisional containing the
  first evidence identity, immediately re-observes, requires exact pre/post identity and
  epoch/generation/transaction equality, then separately atomically writes recovery result
  plus only a closed-matrix promotion. The same matrix applies to activation and rollback:
  `neither`/`ambiguous` never promote, `exact_prior` never advances generation and may only
  close proven pre-effect work as no-effect non-success, and `exact_new` promotes only when
  its phase already has every required authoritative receipt. Adoption is ineligible.
- **Rationale**: Preserves implemented safe host recovery and its two-observation durable-
  commit invariant while keeping Feature 048 unable to write or own effects.
- **Alternatives considered**: Reuse failed-apply `sb host recover`; let Feature 048 write
  activation state; promote from one observation; recovery resumes activation/edge/init.
  Rejected.

## Decision 8 — Additive state, two generations

- **Decision**: Store 051 state as optional additive bounded fields in existing owner-
  only hosting state; retain current plus one previous generation.
- **Rationale**: Avoids split authority and preserves legacy schema readers.
- **Alternatives considered**: Separate journal; multi-generation history. Rejected.
