# Data Model: Remote Development Execution Readiness

## Development range (remote `state.json` → `ranges[]`)

| Field | Type | Rule |
|---|---|---|
| `range_id` | `r-` + 12 hex | sha256 of the CIDR; stable |
| `cidr` | IPv4 CIDR | prefix /12../24; no overlap with any FR-002 class at assignment |
| `subnet_prefix` | int | 24..29; default 26; > CIDR prefix |
| `assigned_at` | int epoch | set once |
| `assigned_by` | holder id | 061 holder identity of the assigning checkout |

Capacity = 2^(subnet_prefix - cidr_prefix). Re-assigning the same CIDR and
prefix is a no-op; a different prefix for an assigned CIDR is refused
`range_conflict`.

## Subnet allocation (`state.json` → `allocations[]`)

| Field | Type | Rule |
|---|---|---|
| `allocation_id` | `a-` + 16 hex | opaque; the only id ever shown outside the range listing |
| `range_id` | ref | must exist |
| `subnet` | CIDR | unique across all allocations; shown only in `network-range list` |
| `owner_kind` | `workspace` \| `job` | |
| `owner_id` | opaque id | job or workspace id (existing opaque forms) |
| `workspace_id` | opaque id | owning workspace (equals owner for kind workspace) |
| `network` | name | Compose network name it was granted for, bounded 64 chars |
| `allocated_at` | int epoch | |

Lifecycle: `allocated` → (removed) on `release-owner` by workspace release,
reap, or retention expiry. There is no other transition; there is no reuse
while present.

## Readiness result

| Field | Type |
|---|---|
| `remote` | name or null |
| `remote_selection` | `explicit` \| `profile` \| `single-configured` \| `local` |
| `rows` | ordered list of six rows |
| `rows[].aspect` | `registration` \| `reachability` \| `runtime_compatibility` \| `capacity` \| `ownership_repair` \| `handoff` |
| `rows[].state` | `ready` \| `not_ready` \| `unknown` \| `not_applicable` |
| `rows[].reason` | safe code (for not_ready / not_applicable) |
| `rows[].probe_state` | `timeout` \| `partial` \| `unavailable` \| `unrecorded` (for unknown) |
| `rows[].remedy` | CLI command or declaration change (for not_ready / unknown) |
| `installed_runtime_revision` | 24 hex or null |
| `taken_at`, `reusable_until` | int epoch (reuse window 300 s) |
| `proposed_range` | CIDR + exact assign command, only when no range is assigned and the inventory is complete |

Proof file `$SANDBOX_HOME/runtime/readiness/<remote>.json` (0600) stores the
result; reuse requires the current installed revision to equal the stored one.

## Handoff record

`$SANDBOX_HOME/runtime/readiness/<remote>.handoff.json`:
`{installed_runtime_revision, proven_at, project}`, written after a successful
`ensure --remote` followed by `exec --remote`.

## Restart plan (docker-pool)

| Field | Type |
|---|---|
| `hosted_targets` | sorted `project/environment` list |
| `other_running_containers` | int |
| `plan_digest` | 16 hex |
| `no_restart_alternative` | the `network-range assign` command |
