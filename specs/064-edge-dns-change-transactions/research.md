# Research: Transactional Edge and DNS Changes

## R1. Ownership marker format

- **Decision**: The record comment is
  `managed by Sandbox hosting; target=<project>/<environment>`. Project and
  environment use the existing validated slug alphabet. A comment that equals
  the legacy `managed by Sandbox hosting` exactly is `unmarked`. Previews
  write `managed by Sandbox preview; preview=<name>`, which hosting never
  treats as owned.
- **Rationale**: The marker is human-readable at the provider and fits the
  100-character comment limit for normal slugs. Parsing is exact, never
  substring-based.
- **Alternatives considered**: provider tags. Rejected because tags are not
  available on every plan; comments are.

## R2. Classification order

For each live record on a declared hostname, the first matching rule wins:

1. `read_only` or provider-managed with a covered type → `refused` with that reason.
2. The type is uncovered:
   - if the journal or the last-applied state shows this target owned the record under a covered type, it is `foreign_record`;
   - otherwise `left_alone`.
3. The hostname matches a recorded preview and the record carries the legacy comment → `preview_owned`.
4. The marker names another target → `foreign_record`.
5. The record is a CNAME → `adoptable` if its content equals the redirect target on a proxied redirect route and the record is unmarked; `conflicting_cname` otherwise, including an owned CNAME whose content changed.
6. Unmarked A/AAAA → `unmarked` (adoptable with `--adopt-records`).
7. An owned record whose fields differ from the last-applied state → `drifted` with the field names.

Then, per hostname:

- more than one record of one covered type where any is unowned → `ambiguous_records`;
- desired families lacking a record → `create`;
- owned records of a family the origin lacks → `remove`.

- **Rationale**: This is the spec's precedence. Refusals are decided before
  any change, so `would_refuse` means zero provider calls.

## R3. Journal durability and leftovers

- **Decision**: The journal is JSONL with these entries:
  - `begin {transaction_id, target, remote, started_at}`;
  - one `intent {seq, item, prior, write}` per change, written and fsync'd before the provider call;
  - `done {seq, written}` after the call;
  - `rollback {seq, outcome}` per restored item;
  - `end {result}` at the close.

  A journal without `end` is interrupted. Leftovers are the items with
  `intent` and no matching `rollback: restored|unchanged`, in a transaction
  whose result is not `committed`. A rollback writes its own entries into
  the same journal.
- **Rationale**: Write-ahead with an fsync per entry survives a controller
  crash (SC-006), and leftovers are computable from the journal alone.

## R4. Conditional restore and later-dependency detection

- **Decision**:
  - **Records.** An item is restored only if the live record equals the
    journal's `write` (type, content, proxied, TTL, comment). Otherwise it is
    `rollback_conflict`, naming the live owner from its marker.
  - **Zone SSL mode.** Restoring away from `strict` is a `rollback_conflict`
    when the zone holds any Sandbox-marked proxied record of another target,
    because proxied Sandbox targets rely on strict. Otherwise it is restored
    if live equals the written value.
- **Rationale**: The provider is the only state shared across remotes and
  controllers. Both rules are computable from provider state, need no shared
  ledger, and are conservative (Scenarios 11a and 11b).
- **Alternatives considered**: a zone ledger in a TXT record. Rejected: it is
  a write to a shared zone that itself needs ownership and rollback.

## R5. Verification resolution and classification

- **Decision**:
  - Get the zone's name servers from the provider API, resolve them once
    (the only use of the system resolver, for the name server hostnames
    only), and send A/AAAA queries for the declared hostname to them over UDP
    with a TCP fallback.
  - For a proxied hostname, the answers are the edge addresses. Connect to
    each with SNI = hostname and verify the certificate against the system
    trust store.
  - For a DNS-only hostname, the answer is the origin, and the certificate
    must be publicly trusted.

  Classification:

  | Answer | Class |
  |---|---|
  | 2xx/3xx; unauthenticated 401 on a basic-auth serve route | success |
  | authenticated 401; any 404 | real failure |
  | 52x (520-530), connection refused or reset, NXDOMAIN or stale answer (an origin address returned for a proxied hostname), origin certificate on a proxied hostname, TLS handshake failure on a proxied hostname | propagation |
  | other 4xx/5xx | real failure |

  Budget: 300 s per hostname, shared by the unauthenticated and
  authenticated checks. Propagation results retry every 10 s. A real failure
  confirms 3 times, 5 s apart, then fails. The result states the budget, the
  confirmation count, the classes, and per-address attempts and source.
- **Rationale**: These are exactly the failure modes in the feedback (530 on
  one edge, a stale resolver, an origin certificate), and they keep today's
  pass rules.

## R6. Lease seam

- **Decision**: `executor.run(lease)` requires `lease.acquire(bound_s)` and
  `lease.release()`. The mutation step runs under it, and verification runs
  after release. Rollback re-acquires the lease with a 30 s wait. If the wait
  fails, the result is `rollback_incomplete`, listing every journaled change
  still in place. Until 060 ships, the default lease wraps today's host-global
  edge lock.
- **Rationale**: This keeps 064 shippable before 060 without changing the
  rule.

## R7. Proxied control endpoint at provision

- **Decision**: When the control hostname's record is proxied, provision
  issues the apply-time control client's status request through the public
  hostname. A 403 with the provider's browser-integrity signature (error
  code 1010 in the body) or any non-success result becomes
  `control_endpoint_refused_by_proxy`. The offered modes are
  `--control-dns-only` (set the control record DNS-only and verify again) and
  `--control-transport tailscale`. Switching requires `--confirm`. Provision
  never reports `reachable` unless the client succeeded.
- **Rationale**: Provision tests with the same client and headers as the
  apply.

## R8. Parity

- **Decision**: Before swapping implementations, add
  `tests/test_edge_txn_hosting_parity.py`. It runs today's apply scenarios
  (declared-only updates, 60 s TTL for DNS-only, SSL mode refusal without the
  flag, redirect CNAME proxied flip, Caddy/nginx restore ordering) against
  both the old path and the new executor using the fake provider, and expects
  identical provider end state.
- **Rationale**: Constitution VI.
