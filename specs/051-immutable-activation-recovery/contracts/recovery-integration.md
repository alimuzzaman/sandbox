# Contract: Feature 051 Activation Recovery with Feature 048 Observer

`sb host image recover` is a distinct Feature 051 replay-safe entrypoint. It requires an
activation transaction identity, recovery request ID/digest, expected host generation,
and confirmation. It does not reuse, reinterpret, or change failed-apply `sb host recover`.

Feature 051 gives a new Feature 048 read-only observer a bounded projection of the active
transaction: exact request/transaction digests, expected generation, new/prior generation
identities, service/topology/image/health requirements, and phase/effect summary. Feature
048 returns a closed `exact_new`, `exact_prior`, `neither`, or `ambiguous` value with exact
evidence identity and target/runtime epoch boundaries and performs no repository/state write.
Each service projection includes its runtime-owned container identity. That identity is retained
inside the generation digest and must match the fresh running observation. If new and prior
projections both match, recovery classifies the observation as `ambiguous`; projection order can
never choose a generation.

Feature 051 reacquires/holds the shared target owner, validates a first fresh coherent
observer value and generation, then durably writes only a bounded 051-owned provisional:
exact recovery request/digest, transaction/generation, evidence identity/epoch, and
`authorizing: false`. It exposes no success, promotion, receipt, generation advance,
terminal result, or effect authority. Feature 051 immediately invokes the same read-only
observer again. Exact evidence identity plus target/runtime epoch, transaction, and generation
must match the first value.

## Closed recovery classification matrix

The matrix is identical for `activate` and `rollback`; `new` means that operation's requested
target generation and `prior` means its starting generation. Adoption is ineligible.

| Transaction phase | `exact_new` | `exact_prior` | `neither` | `ambiguous` |
|---|---|---|---|---|
| `accepted`, `preflight`, or `init_pending` with `effect_entered=false` | Contradiction before authorized effect; stable non-success, fenced, no promotion | Close as stable `no_effect` non-success with no generation advance | Stable non-success, fenced, no promotion | Stable non-success, fenced, no promotion |
| `init_pending` with `effect_entered=true` or `runtime_pending` | Uncertain because required effect/receipt chain is incomplete; no promotion | Uncertain; no promotion | Uncertain; no promotion | Uncertain; no promotion |
| `runtime_proven` | Promote only when every init receipt is authoritative and edge is not required or already has its exact terminal receipt; otherwise fenced for the separate edge protocol | Stable non-success, fenced, no generation advance | Stable non-success, fenced, no promotion | Stable non-success, fenced, no promotion |
| `edge_pending` | Promote only with the exact immutable terminal edge receipt plus fresh unchanged runtime proof; proven-not-entered/acceptance-unknown/possible-delivery remains non-promoting for its separate edge protocol | Stable non-success, fenced, no generation advance | Stable non-success, fenced, no promotion | Stable non-success, fenced, no promotion |
| `committed` | Exact terminal replay only; no new transition | Conflict/non-success; no promotion | Conflict/non-success; no promotion | Conflict/non-success; no promotion |
| `refused`, `failed`, `cancelled`, or `uncertain` | Exact recorded-result replay when identities match; no promotion | Recorded-result replay/conflict only; no promotion | Recorded-result replay/conflict only; no promotion | Recorded-result replay/conflict only; no promotion |

No classification supplies missing init, runtime, or edge authority. `neither` and
`ambiguous` never promote. `exact_prior` never advances generation.

Only then does Feature 051 construct one candidate host state containing both immutable
recovery result and the matrix-allowed transaction promotion, atomically commit both or neither,
and clear the provisional. A crash at the provisional phase may resume only the immediate
post-write observation for the exact request/digest. A different/malformed/effect-entered
owner or changed evidence stays fenced. Exact terminal replay returns the recorded result;
changed ID reuse refuses. Partial/stale/mixed/contradictory/unavailable post-evidence
atomically records stable non-success and clears the provisional while leaving the active
transaction fenced; persistence uncertainty retains the provisional.

Feature 048 cannot start/stop services, execute/repeat init, call edge, activate, adopt,
rollback, pull, build, decide trust, invoke broker/helper, receive credentials, or write a
provisional activation owner. The provisional is exclusively 051-owned. Existing Feature
048 requests/results, old schema-v2 image
planes, and legacy receipts remain compatible and non-authorizing.
