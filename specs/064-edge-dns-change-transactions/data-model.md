# Data Model: Transactional Edge and DNS Changes

## Desired hostname state

| Field | Type | Rule |
|---|---|---|
| `hostname` | FQDN or `*.`-wildcard | declared by the target |
| `families` | `{A: address\|absent, AAAA: address\|absent}` | from recorded origin IPv4/IPv6 |
| `proxied` | bool | from declaration |
| `ttl` | int | 1 (auto) proxied; 60 DNS-only |
| `redirect_target` | hostname or null | redirect routes only |
| `verification` | `probe` \| `skipped_wildcard` | wildcard → skipped |

Zone-level: `ssl_mode_required` (`strict` when any proxied hostname) and the
live prior value.

## Plan item

| Field | Type |
|---|---|
| `hostname`, `type` | |
| `record_id` | provider id or null (create) |
| `action` | `create` \| `update` \| `remove` \| `adopt` \| `adoptable` \| `left_alone` \| `drifted` \| `refused` \| `unchanged` |
| `reason` | `foreign_record` \| `unmarked` \| `conflicting_cname` \| `read_only` \| `provider_managed` \| `preview_owned` \| `ambiguous_records` \| `prior_incomplete_rollback` \| `interrupted_transaction` |
| `owner` | target named by a foreign marker |
| `drift_fields` | list of field names |
| `prior`, `desired` | record snapshots (type, content, proxied, ttl, comment) |

Plan verdict: `would_apply` \| `would_refuse` (any `refused`, or `adoptable`/`unmarked` without `--adopt-records`, or leftovers).

## Journal entry (JSONL)

`{kind: begin|intent|done|rollback|end, transaction_id, seq?, item?, prior?, write?, outcome?, result?, at}`

- `item`: `{kind: record|zone_setting|front_door, zone_id, hostname?, type?, record_id?, setting?}`
- `outcome` (rollback): `restored` \| `unchanged` \| `restore_failed` (reason) \| `rollback_conflict` (later owner/operation)
- `result` (end): `committed` \| `rollback_complete` \| `rollback_incomplete`

State: `begin → intent/done* → end(committed)` or `→ rollback* → end(rollback_*)`; no `end` = interrupted.

## Verification evidence

Per hostname: `{budget_s, used_s, confirmations, classes_version, addresses:
[{address, source: authoritative|edge, attempts, last_class, last_status,
certificate: edge|origin|public|untrusted}], result: success|real_failure|
propagation_timeout|skipped_wildcard}`.

## Transaction result (consumed by 062)

`{transaction_id, result, items:[{item, outcome}], front_door:{result,...},
verification:[...], leftovers:[...]}`.
