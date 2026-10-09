# Data Model: Remote Development Execution Readiness

## Development range (remote `state.json` → `ranges[]`)

| Field | Type | Rule |
|---|---|---|
| `range_id` | `r-` + 12 hex | sha256 of the CIDR; stable |
| `cidr` | IPv4 CIDR | prefix /12../24; no overlap with any FR-002 class at assignment |
| `subnet_prefix` | int | 24..29; default 26; >= CIDR prefix (equal means capacity 1, used by test ranges) |
| `assigned_at` | int epoch | set once |
| `assigned_by` | holder id | 061 holder identity of the assigning checkout |

Capacity = 2^(subnet_prefix - cidr_prefix). Re-assigning the same CIDR and
prefix is a no-op; a different prefix for an assigned CIDR is refused
`range_conflict`.

## Subnet allocation (`state.json` → `allocations[]`)

| Field | Type | Rule |
|---|---|---|
| `allocation_id` | `a-` + 16 hex | opaque; refusals and evidence may show it with `owner_id`, `owner_kind` and `workspace_id` (all opaque), never the subnet |
| `range_id` | ref | must exist |
| `subnet` | CIDR | unique across all allocations; shown only in `network-range list` |
| `owner_kind` | `workspace` \| `job` | |
| `owner_id` | opaque id | job or workspace id (existing opaque forms) |
| `workspace_id` | opaque id | owning workspace (equals owner for kind workspace) |
| `network` | name | Compose network name it was granted for, bounded 64 chars |
| `allocated_at` | int epoch | |

Identity: an allocation is unique per `(workspace_id, network)`, where
`network` is the project-scoped Docker network name the effective Compose
config creates (for example `sandbox-<instance>_default`), not the Compose
key, so two stacks in one workspace never share a subnet. Allocating
for a pair that already holds one returns the existing allocation, so a job,
preview or later run in the same workspace stack reuses its workspace's
subnets and allocations stay bounded by live workspaces times their networks.

Owner kind mapping (FR-004):
- `workspace`: networks of a workspace's Compose project, including jobs,
  previews and `exec` that run inside that stack.
- `job`: networks a job creates outside its workspace's stack. A CI matrix
  cell runs on its own labelled instance, which is its own workspace, so its
  networks are `workspace`-owned and freed with that workspace.

Lifecycle: `allocated` → (removed) on `release-owner` by workspace release,
reap, or retention expiry. There is no other transition; there is no reuse
while present.

## Last-proven capacity (`state.json` → `capacity_proof`)

`{proven_at, pool_capacity, range_capacity, allocated, usable}`, overwritten by
each admission probe in the same program call; `list` returns it (FR-023).

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

Proof file `$SANDBOX_HOME/runtime/readiness/<remote>/<project_sha16>.json`
(0600, `project_sha16` from the checkout's `project_identity`) stores the
result plus `installed_at` (the migrate receipt time). Reuse requires all of:
- the same project and remote;
- no `not_ready` row (a refused proof is never reused; the next submission
  re-checks);
- the current installed revision and `installed_at` equal the stored ones, so
  a migrate that reinstalls the same revision also invalidates it (FR-018);
- `now < reusable_until`.

`network-range assign`, `release-owner` (workspace release, reap, retention)
and `remote service migrate` delete every proof for that remote.

## Handoff record

`$SANDBOX_HOME/runtime/readiness/<remote>/handoff.json`:
`{installed_runtime_revision, proven_at, project}`, written after a successful
`ensure --remote` followed by `exec --remote`.

## Restart plan (docker-pool)

| Field | Type |
|---|---|
| `hosted_targets` | sorted `project/environment` list |
| `other_running_containers` | int |
| `plan_digest` | 16 hex |
| `no_restart_alternative` | the `network-range assign` command |
