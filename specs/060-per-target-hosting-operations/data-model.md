# Data Model: Per-Target Hosting Operations

## Remote coordination store (`$SANDBOX_HOME/runtime/hosting-leases/`)

### Target record (`targets/<sha16>.json`)

| Field | Type | Rule |
|---|---|---|
| `state_key` | `remote/project/environment` | identity; file name is sha256[:16] |
| `fencing_token` | int | monotonic per target; increments on every admit |
| `lease` | Lease or null | at most one |
| `hold` | Hold or null | at most one |
| `phase` | `{phase_id, dispatched_at, lease_id}` or null | set by phase-report |
| `fence` | `none` \| `fenced_pending_cessation` | set on expiry with phase |
| `queue` | list of QueueEntry (≤32) | FIFO |
| `history` | list (≤64, newest last) | admit/release/expire/break/cap entries |

### Lease

`{lease_id: "l-"+16hex, kind: operation|hold-nested, holder: {operation, request_id, controller_id, session}, started_at, renewed_at, expires_at}` (TTL 90 s).

### Hold

`{hold_id: "hd-"+16hex, holder: {controller_id, session}, purpose (≤120 printable), claimed_at, expires_at ≤ claimed_at+14400, renewed_at}`.

### QueueEntry

`{waiter_id: "w-"+16hex, holder: {operation, request_id, controller_id}, enqueued_at, deadline}`; dropped at deadline or on clean interrupt.

### Remote record (`remote.json`)

`{build_cap: int ≥1 (default 2), cap_history: [{value, controller_id, at}], build_slots: [{lease_id, state_key, started_at, expires_at}], shared_lease: {lease_id, state_key, step, acquired_at, expires_at ≤ +60s} | null, controllers: [controller_id]}`.

`controller_id` = 061 holder identity form (`h-`+16hex of local home realpath).

## Controller state

- `runtime/hosts/<sha16>.json`: `{version: 2, state_key, record}`; record keeps today's per-target fields unchanged in meaning (active_operation, recovery_uncertainty, image_activation, generation, receipts).
- `runtime/hosts-conversion.json`: `{version: 1, remotes: {<name>: {state: in_progress|converted, steps_done: [...], started_at, finished_at}}}`.

## State transitions

Target: `idle → leased(op) → idle` | `idle → held → held+leased(op) → held → idle` | `leased + expiry + phase → fenced_pending_cessation → idle (phase ceased; 054 fences then decide)`.

Hold: `claimed → renewed* → released | broken | expired`; renew refused past `claimed_at + 4h`.

Conversion per remote: `absent → in_progress → converted`; `in_progress` and `remote-enabled ∧ legacy records present` are mixed.

## Refusal codes

`target_busy`, `target_held`, `build_cap_reached`, `lease_authority_unavailable`, `predecessor_phase_running`, `hosting_state_mixed`, `lease_lost`, `hold_too_long`, `hold_renewal_exceeds_maximum`, `hold_not_owned`, `wait_out_of_range`, `host_reclaim_busy`.
